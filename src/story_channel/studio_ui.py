from .dialog_ui import dialog
"""World and episode pages; production editors delegate to existing components."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button,review_button
import json
import streamlit as st

from .studio import WorldStudio, Episodes, TextReview, generate_advice, approved_content, content_digest, resolve_content_review, review_differences
from .files import read_text, atomic_write, input_hash
from .ui_tasks import busy, launch
from .pipeline import Pipeline
from .world_settings_ui import new_world, world_page


def request_modify(project_id, kind, subject=None):
    from .dialog_ui import request_open
    request_open()
    for key in ("active-stage-browser", "modify-target", "voice-design-target"):
        st.session_state.pop(key, None)
    st.session_state["studio-modify-target"] = {"project_id": project_id, "kind": kind, "subject": subject}


def dismiss_modify():
    st.session_state.pop("studio-modify-target", None)


def selected_pipeline(manager, project_id):
    from .models import ProviderConfig
    manifest = manager.load(project_id)
    owner = manifest.world_project_id or project_id
    mode = st.session_state.get(f"project-setting-{owner}-generation-mode")
    if mode is None:
        return Pipeline(manager, project_id)
    demo = mode.startswith("Demo")
    model = st.session_state.get(f"project-setting-{owner}-llm-model", manager.settings.llm_model)
    return Pipeline(manager, project_id, llm_config=ProviderConfig(provider="demo" if demo else "openai",
        model="demo-v1" if demo else model, options={"max_output_tokens": manager.settings.llm_max_output_tokens}))


@dialog("Modify with prompt", width="large", on_dismiss=dismiss_modify)
def modify_dialog(manager, target):
    st.caption("Describe your direction. AI will propose changes for review; it will not approve content.")
    prompt = st.text_area("Modification request", key="studio-draft-modify-prompt", height=170, disabled=busy())
    pipeline = selected_pipeline(manager, target["project_id"])
    if generation_button("Generate suggestions", key="studio-modify-submit", disabled=busy() or not prompt.strip()):
        def generate():
            pid, kind = target["project_id"], target["kind"]
            if kind in ("world", "addition"):
                return WorldStudio(manager, pid, pipeline).review(setting_id=target.get("subject"), instructions=prompt)
            return review_episode(pipeline, kind, prompt)
        def done(_):
            st.session_state.pop("studio-modify-target", None)
        launch(generate, "Generating modification suggestions", done)


def show_modify(manager):
    target = st.session_state.get("studio-modify-target")
    if target:
        modify_dialog(manager, target)


def episode_review_context(pipeline, stage):
    manifest = pipeline.manager.load(pipeline.project_id)
    world = WorldStudio(pipeline.manager, manifest.world_project_id or pipeline.project_id)
    return {"canon": manifest.canon_snapshot or world.canon(), "stage": stage,
            "title": manifest.title, "targetDurationSec": manifest.target_duration_sec}


def review_episode(pipeline, stage, instructions=""):
    from .locking import project_lock
    with project_lock(pipeline.path):
        text = pipeline.manager.read_outline(pipeline.project_id) if stage == "outline" else pipeline.artifact(stage).model_dump_json(by_alias=True, indent=2)
        report = generate_advice(pipeline, text, episode_review_context(pipeline, stage), instructions=instructions,
            stage="episode_" + stage + "_review")
        file = pipeline.path / f"episode_{stage}_review.json"
        if file.exists():
            old = read_text(file)
            atomic_write(pipeline.path / "history" / "episode_reviews" / stage / f"{input_hash(old)}.json", old)
        atomic_write(file, report.model_dump_json(by_alias=True, indent=2))
        return report


def stage_review(pipeline, stage):
    from copy import deepcopy
    pid = pipeline.project_id
    text = pipeline.manager.read_outline(pid) if stage == "outline" else pipeline.artifact(stage).model_dump_json(by_alias=True, indent=2)
    review, modify = st.columns(2)
    review_button("AI review",container=review,key=f"episode-review-{pid}-{stage}",disabled=busy(),
        on_click=lambda:launch(lambda:review_episode(pipeline,stage),"Reviewing episode "+stage))
    modify.button("Modify with prompt", key=f"episode-modify-{pid}-{stage}", disabled=busy(),
                  on_click=request_modify, args=(pid, stage))
    file = pipeline.path / f"episode_{stage}_review.json"
    resolved, selected, report, pending = True, text, None, False
    decisions, all_reverted = [], []
    if file.exists():
        report = TextReview.model_validate_json(read_text(file))
        current = report.source_hash == input_hash(text) and report.context_hash == input_hash(episode_review_context(pipeline, stage))
        if report.decision == "resolved" and report.accepted_text == text:
            st.caption("Review decisions saved.")
        elif not current:
            st.caption("Previous review is outdated. Run AI review again for current content.")
        else:
            pending = not bool(report.decision)
            st.write(report.advice.summary)
            for reason in report.advice.reasons:
                st.caption(reason)
            for conflict in report.advice.conflicts:
                st.warning(conflict)
            try:
                original, changes = review_differences(stage, text, report.advice.proposed_text or text)
            except ValueError as exc:
                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('studio_ui:108', locals()))
                resolved, changes, original = False, [], None
            prefix = f"studio-review-choice-{pid}-{stage}-{input_hash(report.model_dump())[:12]}"
            choices = st.session_state.setdefault(prefix + "-decisions", {})
            edited = deepcopy(original)
            for index, change in enumerate(changes):
                st.markdown("**" + " / ".join(map(str, change["path"])) + "**")
                left, right = st.columns(2)
                left.text_area("Original", value=change["original"], disabled=True, key=f"episode-original-{pid}-{stage}-{input_hash(report.model_dump())[:12]}-{index}")
                draft_key = f"studio-draft-review-{pid}-{stage}-{input_hash(report.model_dump())[:12]}-{index}"
                st.session_state.setdefault(draft_key, change["suggested"])
                draft = right.text_area("Suggested change (editable)", key=draft_key, disabled=busy(),
                    on_change=lambda index=index, key=prefix+"-decisions": st.session_state[key].pop(str(index), None))
                def choose(choice, index=index, key=prefix+"-decisions"):
                    st.session_state[key][str(index)] = choice
                apply, revert = st.columns(2)
                apply.button("Applied" if choices.get(str(index)) == "apply" else "Apply change", key=f"episode-apply-{pid}-{stage}-{index}",
                    disabled=busy(), on_click=choose, args=("apply",))
                revert.button("Reverted" if choices.get(str(index)) == "revert" else "Revert change", key=f"episode-revert-{pid}-{stage}-{index}",
                    disabled=busy(), on_click=choose, args=("revert",))
                choice = choices.get(str(index))
                decisions.append({"path": change["path"], "choice": choice, "text": draft if choice == "apply" else change["original"]})
                all_reverted.append({"path": change["path"], "choice": "revert"})
                target = edited
                for part in change["path"][:-1]:
                    target = target[part]
                target[change["path"][-1]] = draft if choice == "apply" else change["original"]
            resolved = resolved and len(choices) == len(changes)
            st.caption(f"{len(choices)} / {len(changes)} differences resolved. Editing a suggestion resets its decision.")
            if original is not None:
                selected = edited["text"] if stage == "outline" else json.dumps(edited, ensure_ascii=False, indent=2)
    approved = approved_content(pipeline, stage)
    st.markdown(":green[Approved]" if approved else ":red[Not approved]")
    expected = content_digest(pipeline, stage)
    report_digest = input_hash(report.model_dump()) if report else None
    save, unchanged = st.columns(2)
    if save.button("Save and approve", key=f"episode-save-approve-{pid}-{stage}", type="primary", disabled=busy() or not resolved or approved and not pending):
        launch(lambda: resolve_content_review(pipeline, stage, selected, expected=expected, report_digest=report_digest, decisions=decisions),
               "Saving and approving " + stage)
    if unchanged.button("Remain unchanged and approve", key=f"episode-unchanged-{pid}-{stage}", disabled=busy() or approved and not pending):
        launch(lambda: resolve_content_review(pipeline, stage, text, expected=expected, report_digest=report_digest, decisions=all_reverted),
               "Approving original " + stage)


def episode_page(manager, world_id, episode_id=None, pipeline=None):
    service = Episodes(manager, world_id)
    episodes = service.list()
    ids = {project.project_id for project in episodes}
    selected = st.session_state.get(f"selected-episode-{world_id}",episode_id)
    episode_id = selected if selected in ids else episode_id if episode_id in ids else episodes[0].project_id if episodes else None
    if episode_id:
        st.session_state[f"selected-episode-{world_id}"] = episode_id
    st.header("Story/Episode")
    st.caption("Canon · TODO: cross-episode events and continuity ledger.")
    if not episodes and (service.world.path / "story.json").exists():
        st.info("This project has an existing story. Importing copies its saved content and media into an episode; originals and billing records stay in this project.")
        if st.button("Import existing story as an episode", key="studio-import-legacy", disabled=busy()):
            def imported(episode):
                st.session_state[f"selected-episode-{world_id}"] = episode.project_id
            launch(service.adopt_existing, "Importing existing story and media", imported)
    with st.expander("Add story or episode", expanded=episode_id is None):
        with st.form("new_episode_" + world_id):
            prefix = f"studio-draft-new-episode-{world_id}-"
            for field, value in (("title", ""), ("outline", ""), ("hours", 0), ("minutes", 2), ("seconds", 0)):
                st.session_state.setdefault(prefix + field, value)
            title = st.text_input("Episode title", key=prefix+"title")
            outline = st.text_area("Story outline", height=180, key=prefix+"outline")
            h, m, s = st.columns(3)
            hours = h.number_input("Hours", min_value=0, step=1, key=prefix+"hours")
            minutes = m.number_input("Minutes", min_value=0, max_value=59, step=1, key=prefix+"minutes")
            seconds = s.number_input("Seconds", min_value=0, max_value=59, step=1, key=prefix+"seconds")
            if st.form_submit_button("Create episode", disabled=busy()):
                try:
                    episode = service.create(title, outline, int(hours*3600+minutes*60+seconds))
                    st.session_state[f"selected-episode-{world_id}"] = episode.project_id
                    st.rerun()
                except (OSError, ValueError, RuntimeError) as exc:
                    render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('studio_ui:179', locals()))
    from .episode_list_ui import episode_list
    episode_list(world_id,episodes,episode_id,manager=manager)
    if not episode_id:
        st.info("Create or select an episode. Each episode keeps its own approvals and production outputs.")
        return
    project = manager.load(episode_id)
    from .episode_list_ui import episode_status
    status = episode_status(manager,project)
    if status == "READY TO ANIMATE":
        st.markdown(":green[READY TO ANIMATE]")
    else:
        st.caption("Status: "+status)
    pipeline = pipeline if pipeline and pipeline.project_id == episode_id else selected_pipeline(manager,episode_id)
    tabs = st.tabs(["Outline", "Story", "Script", "Storyboard", "Prompts"], key=f"page-tab-episode-{episode_id}", on_change="rerun")
    with tabs[0]:
        from .episode_removal_ui import open_removal
        st.button("Remove episode",key="episode-remove-"+episode_id,icon=":material/delete:",disabled=busy(),
            on_click=open_removal,args=(manager,world_id,episode_id,project.title))
        if project.shared_assets_hash:
            st.caption("Using this episode's approved shared asset snapshot. Root library changes are imported only when you refresh it.")
            if st.button("Refresh shared assets from World", key="episode-refresh-assets-" + episode_id,
                         disabled=busy() or project.canon_snapshot != service.world.canon()):
                launch(lambda: service.refresh_assets(episode_id), "Refreshing reusable cast and reference assets")
        if project.canon_snapshot != service.world.canon():
            st.info("New World additions are approved. This episode retains its previous canon snapshot until you include them.")
            if st.button("Include latest approved World additions", key="episode-refresh-world-" + episode_id, disabled=busy()):
                launch(lambda: service.refresh_world(episode_id), "Updating episode canon and reopening its approvals")
        key = f"studio-draft-episode-{episode_id}-"
        baseline_key = "studio-outline-baseline-" + episode_id
        current = {"title": project.title, "outline": manager.read_outline(episode_id),
                   "hours": project.target_duration_sec//3600, "minutes": project.target_duration_sec//60%60, "seconds": project.target_duration_sec%60}
        previous = st.session_state.setdefault(baseline_key, current)
        stale_draft = False
        if previous != current:
            draft_values = {field: st.session_state.get(key+field, value) for field, value in previous.items()}
            if draft_values in (previous, current):
                for field, value in current.items():
                    st.session_state[key+field] = value
                st.session_state[baseline_key] = current
            else:
                stale_draft = True
                st.warning("Saved episode inputs changed while you had an unsaved draft. Your draft is retained; reload the saved outline before saving.")
        for field, value in current.items():
            st.session_state.setdefault(key + field, value)
        with st.container():
            title = st.text_input("Title", key=key+"title", disabled=busy())
            outline = st.text_area("Episode story outline", key=key+"outline", height=220, disabled=busy())
            h, m, s = st.columns(3)
            hours = h.number_input("Hours", min_value=0, step=1, key=key+"hours", disabled=busy())
            minutes = m.number_input("Minutes", min_value=0, max_value=59, step=1, key=key+"minutes", disabled=busy())
            seconds = s.number_input("Seconds", min_value=0, max_value=59, step=1, key=key+"seconds", disabled=busy())
            outline_dirty = (title, outline, hours*3600+minutes*60+seconds) != (project.title, current["outline"], project.target_duration_sec)
            expected = content_digest(pipeline, "outline")
            save, revert = st.columns(2)
            if save.button("Save episode outline", key="episode-save-outline-" + episode_id, disabled=busy() or not outline_dirty or stale_draft):
                try:
                    service.save_outline(episode_id, title, outline, int(hours*3600+minutes*60+seconds), expected=expected)
                    st.rerun()
                except (OSError, ValueError, RuntimeError) as exc:
                    render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('studio_ui:228', locals()))
            def reset_outline():
                for field, value in current.items():
                    st.session_state[key+field] = value
                st.session_state[baseline_key] = current
            revert.button("Reload saved outline" if stale_draft else "Revert outline draft", key="episode-revert-outline-" + episode_id,
                          disabled=busy() or not (outline_dirty or stale_draft), on_click=reset_outline)
        if outline_dirty or stale_draft:
            st.caption("Save or revert this outline draft before reviewing or generating content.")
        else:
            stage_review(pipeline, "outline")
    from .modules import MODULES
    from .ui import approval_files, approval_json_toggle
    for tab, stage in zip(tabs[1:], ("story", "script", "storyboard", "prompts")):
        with tab:
            state = project.steps.get(stage)
            st.caption(stage.title() + " · " + (state.status if state else "pending") + (f" · revision {state.revision}" if state else ""))
            if state and state.error:
                render_error(state.error, project_path=pipeline.path, task_key=stage, key=f"episode-stage-error-{episode_id}-{stage}")
            def generate(stage=stage):
                if stage == "story":
                    pipeline.generate(stage, force=True)
                    pipeline.generate("review", force=True)
                    from .review_changes import prepare_review
                    prepare_review(pipeline, "story", force=True)
                else:
                    pipeline.generate(stage, force=True)
            can_generate = approved_content(pipeline, "outline") and not outline_dirty and not stale_draft
            if stage == "script":
                can_generate = can_generate and "story" in project.approvals
            elif stage == "storyboard":
                can_generate = can_generate and approved_content(pipeline, "script")
            elif stage == "prompts":
                can_generate = can_generate and "storyboard" in project.approvals
            if generation_button(("Regenerate " if state else "Generate ") + stage.title(), key=f"episode-generate-{episode_id}-{stage}", disabled=busy() or not can_generate):
                launch(generate, "Generating episode " + stage)
            path = pipeline.path / MODULES[stage].filename
            if not path.exists():
                st.info("Generate and approve the preceding stage first.")
                continue
            from .ui import stage_files
            show_json = approval_json_toggle(pipeline, "episode-" + stage)
            if stage == "storyboard":
                from .storyboard_editor_ui import render
                render(pipeline)
                if show_json:
                    st.json(pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True))
            else:
                approval_files(pipeline, stage_files(pipeline, stage), show_json, "episode-output-" + stage)
            if state and state.status != "succeeded":
                continue
            def edit_saved(stage=stage):
                st.session_state["active-stage-browser"] = (episode_id, stage)
                from .dialog_ui import request_open
                request_open()
            st.button("Edit saved JSON", key=f"episode-edit-{episode_id}-{stage}", disabled=busy(), on_click=edit_saved)
            if stage == "story":
                from .review_changes_ui import review_editor
                from .review_changes_ui import run_story_review
                review_button("AI Review (Story)",key="episode-story-review-"+episode_id,disabled=busy(),
                    on_click=lambda:launch(lambda:run_story_review(pipeline),"Reviewing current episode Story"))
                if project.steps.get("review") and project.steps["review"].status == "succeeded":
                    review_editor(pipeline, "story")
            elif stage == "storyboard":
                from .dialogue_review import approval_dialogue_review
                approval_dialogue_review(pipeline, source_ready=True)
            else:
                stage_review(pipeline, stage)
    from .ui import show_active_browser
    show_active_browser(pipeline)
