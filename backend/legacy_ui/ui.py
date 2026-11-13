from .dialog_ui import dialog
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import json
import difflib
import re
from pathlib import Path

import streamlit as st

from .config import Settings
from .modules import MODULES, stage_title
from .pipeline import Pipeline
from .projects import ProjectManager
from .models import ProviderConfig
from .files import input_hash
from .proofreading import TypoSuggestion, apply_suggestion, check_typos
from .modification_ui import open_modification, show_modification
from .action_labels import text_action_label
from .ui_tasks import busy, collect, launch, preserve_page_widgets, task_status

PAGES = ("World", "Assets", "Story/Episode", "Animations", "Final", "Analytics")


def stage_files(pipeline, step):
    if step == "storyboard" and (pipeline.path / MODULES[step].filename).is_file():
        try:
            pipeline.export_storyboard_markdown()
        except (OSError, ValueError, RuntimeError):
            pass  # Keep corrupted canonical data browsable for manual repair.
    files = [pipeline.path / MODULES[step].filename]
    extras = {
        "characters": ["character_bible.md"],
        "story": ["story.md"],
        "script": ["script.md"],
        "storyboard": ["storyboard.md"],
        "prompts": ["prompts/image_prompts.json", "prompts/video_prompts.json"],
    }
    files.extend(pipeline.path / name for name in extras.get(step, []))
    if step == "characters":
        files.extend(sorted((pipeline.path / "characters").glob("*.json")))
    return sorted((path for path in files if path.is_file()), key=lambda path: path.suffix != ".md")


def show_stage_file(path, root, key):
    name = path.relative_to(root).as_posix()
    st.caption(name)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        st.warning(f"Cannot read {name}.")
        return
    if path.suffix == ".md":
        st.markdown(script_line_breaks(text) if path.name == "script.md" else text)
    else:
        try:
            st.json(json.loads(text))
        except ValueError:
            st.warning("This file contains invalid JSON. The saved text is shown below.")
            st.code(text, language="json")
    st.download_button("Download " + path.name, text, file_name=path.name,
                       mime="text/markdown" if path.suffix == ".md" else "application/json", key=key)


def script_line_breaks(text):
    # Markdown soft-wraps plain newlines; preserve speaker lines in existing exports too.
    return re.sub(r"(?m)^([A-Za-z0-9][A-Za-z0-9_-]*:[^\r\n]*?)[ \t]*\r?\n(?=[A-Za-z0-9][A-Za-z0-9_-]*:)",
                  r"\1  \n", text)


def dismiss_stage_browser():
    active = st.session_state.pop("active-stage-browser", None)
    if active:
        st.session_state.pop(f"approval-json-visible-{active[0]}-{active[1]}", None)
        prefix = f"browser-draft-{active[0]}-{active[1]}"
        for key in list(st.session_state):
            if key == prefix or key.startswith(prefix + "-"):
                del st.session_state[key]


def show_active_browser(pipeline):
    if st.session_state.get("modify-target"):
        return
    active = st.session_state.get("active-stage-browser")
    if active and active[0] == pipeline.project_id:
        if active[1] in ("review", "story_approval"):
            ai_story_review_dialog(pipeline)
        elif active[1] == "storyboard_approval":
            storyboard_approval_dialog(pipeline)
        else:
            browse_stage(pipeline, active[1])


def approval_json_toggle(pipeline, gate):
    key = f"approval-json-visible-{pipeline.project_id}-{gate}_approval"
    visible = st.session_state.get(key, False)
    def toggle():
        st.session_state[key] = not st.session_state.get(key, False)
    st.button("Hide JSON" if visible else "Show JSON", key=f"toggle-approval-json-{gate}", on_click=toggle)
    return visible


def approval_files(pipeline, files, show_json, prefix):
    from .approval_exports import readable_export, redundant_json_sources
    redundant = redundant_json_sources(files)
    for path in files:
        if path.suffix == ".json" and path not in redundant:
            try:
                st.markdown(readable_export(path))
            except (OSError, ValueError, UnicodeError):
                st.warning(f"Cannot create a readable export for {path.relative_to(pipeline.path).as_posix()}. Use Show JSON to inspect it.")
    if show_json:
        for path in files:
            if path.suffix == ".json" and path not in redundant:
                show_stage_file(path, pipeline.path, f"{prefix}-{path.relative_to(pipeline.path).as_posix()}")


@dialog("AI Review (Storyboard)", width="large", on_dismiss=dismiss_stage_browser)
def storyboard_approval_dialog(pipeline):
    project = pipeline.manager.load(pipeline.project_id)
    required = ("world", "characters", "story", "review", "script", "storyboard")
    ready = ("story" in project.approvals and
             all(project.steps.get(step) and project.steps[step].status == "succeeded" for step in required))
    st.subheader(project.title)
    st.caption("Compare the original Script and editable AI suggestions. Apply or revert every difference, then save and approve, or remain unchanged and approve.")
    show_json = approval_json_toggle(pipeline, "storyboard")
    if not ready:
        st.warning("Approve the story and generate current Script and Storyboard results before approving.")
    from .dialogue_review import approval_dialogue_review
    st.session_state.setdefault(f"page-tab-storyboard-approval-{project.project_id}", "AI Review (Storyboard)")
    script, storyboard, review = st.tabs(["Script", "Storyboard", "AI Review (Storyboard)"], key=f"page-tab-storyboard-approval-{project.project_id}", on_change="rerun")
    for tab, step in ((script, "script"), (storyboard, "storyboard")):
        with tab:
            files = stage_files(pipeline, step)
            if not files:
                st.info(f"No saved {step} data yet.")
            state = project.steps.get(step)
            if state and state.status != "succeeded":
                st.warning(f"{step.title()} is {state.status}; saved output may be from an earlier version.")
            approval_files(pipeline, files, show_json, f"storyboard-approval-{step}")
    with review:
        approval_dialogue_review(pipeline, source_ready=all(
            project.steps.get(step) and project.steps[step].status == "succeeded"
            for step in ("characters", "story", "script")), approval_ready=ready)
        report = pipeline.path / "dialogue_review.json"
        if report.exists():
            approval_files(pipeline, [report], show_json, "storyboard-approval-dialogue-review")
    if "storyboard" in project.approvals:
        st.success("This storyboard is approved.")
    if st.button("Close", key="close-storyboard-approval"):
        dismiss_stage_browser()
        st.rerun()


@dialog("AI Review (Story)", width="large", on_dismiss=dismiss_stage_browser)
def ai_story_review_dialog(pipeline):
    project = pipeline.manager.load(pipeline.project_id)
    required = ("world", "characters", "story", "review")
    ready = all(project.steps.get(step) and project.steps[step].status == "succeeded" for step in required)
    st.subheader(project.title)
    show_json = approval_json_toggle(pipeline, "story")
    st.caption("Review the original story and editable AI suggestions. Apply or revert every difference before saving and approving.")
    from .review_changes_ui import launch_review, run_story_review, review_editor
    from .action_labels import action_label
    sources_ready = all(project.steps.get(step) and project.steps[step].status == "succeeded" for step in ("world", "characters", "story"))
    from .generation_ui import review_button
    review_button(action_label("AI Review (Story)",pipeline.config.provider,pipeline.config.model),
        key="run-ai-review-story",disabled=busy() or not sources_ready,
        on_click=lambda:launch_review(pipeline,"story",lambda:run_story_review(pipeline)))
    st.session_state.setdefault(f"page-tab-story-approval-{project.project_id}", "AI Review (Story)")
    if not ready:
        st.warning("Generate all stages through AI Review (Story) and resolve outdated or failed stages before approving.")
    outline, world, characters, story, review, details = st.tabs(["Outline", "World", "Characters", "Story", "AI Review (Story)", "Project and activity"],
                                                              key=f"page-tab-story-approval-{project.project_id}", on_change="rerun")
    with outline:
        st.markdown(pipeline.manager.read_outline(project.project_id))
    for tab, step in ((world, "world"), (characters, "characters"), (story, "story"), (review, "review")):
        with tab:
            files = stage_files(pipeline, step)
            if not files:
                st.info(f"No saved {step} data yet.")
            state = project.steps.get(step)
            if state and state.status != "succeeded":
                st.warning(f"{step.title()} is {state.status}; saved output may be from an earlier version.")
            approval_files(pipeline, files, show_json, f"approval-{step}")
            if step == "review":
                review_editor(pipeline, "story", ready=ready)
    with details:
        hours, remainder = divmod(project.target_duration_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        st.caption(f"{project.title} · {project.language} · {hours:02d}:{minutes:02d}:{seconds:02d} · Schema {project.schema_version}")
        st.caption(f"Project ID: {project.project_id} · Updated {project.updated_at:%Y-%m-%d %H:%M %Z}")
        st.write("Story package status and saved versions. Each revision is a saved generation or edit.")
        rows = []
        for step in required:
            state = project.steps.get(step)
            rows.append({"Stage": stage_title(step), "Status": state.status.title() if state else "Pending",
                         "Version": f"r{state.revision}" if state else "—",
                         "Attempts": state.attempts if state else 0,
                         "Updated": state.updated_at.strftime("%Y-%m-%d %H:%M") if state else "—"})
        st.table(rows)
    digest = input_hash({step: project.steps[step].output_hash if step in project.steps else "" for step in required})
    if "story" in project.approvals:
        st.success("This story package is approved.")
    close = st.container()
    if close.button("Close", key="close-story-approval"):
        dismiss_stage_browser()
        st.rerun()


@dialog("Stage data", width="large", on_dismiss=dismiss_stage_browser)
def browse_stage(pipeline, step):
    project = pipeline.manager.load(pipeline.project_id)
    state = project.steps.get(step)
    st.subheader(stage_title(step))
    st.caption(f"{project.title} · Revision {state.revision if state else 0}")
    if state and state.status != "succeeded":
        st.warning(f"Stage status: {state.status}. Saved output may belong to a previous version.")
    draft_key = f"browser-draft-{pipeline.project_id}-{step}"
    if draft_key not in st.session_state:
        sources = {"project": json.dumps({"title": project.title, "language": project.language,
                                          "targetDurationSec": project.target_duration_sec}, ensure_ascii=False, indent=2),
                   "outline": pipeline.manager.read_outline(project.project_id)}
        for name in (*MODULES[step].dependencies, step):
            path = pipeline.path / MODULES[name].filename
            if path.is_file():
                sources[name] = path.read_text(encoding="utf-8-sig")
        st.session_state[draft_key] = sources
    baseline = st.session_state[draft_key]
    values = {}
    changes_summary = st.empty()

    def editor(name, label):
        key = f"{draft_key}-{name}"
        if key not in st.session_state:
            st.session_state[key] = baseline[name]
        text = st.text_area(label, key=key, height=300)
        values[name] = text
        if text != baseline[name]:
            st.caption("🟠 Modified — not saved")
        return text

    def reset_draft():
        st.session_state.pop(f"{draft_key}-typo-report", None)
        for name, text in baseline.items():
            st.session_state[f"{draft_key}-{name}"] = text

    def clear_draft():
        st.session_state.pop("active-stage-browser", None)
        st.session_state.pop(draft_key, None)
        st.session_state.pop(f"{draft_key}-typo-report", None)
        for name in baseline:
            st.session_state.pop(f"{draft_key}-{name}", None)

    output, inputs, history, activity = st.tabs(["Output", "Inputs", "History", "Activity"],
                                              key=f"page-tab-browser-{pipeline.project_id}-{step}", on_change="rerun")
    with output:
        files = stage_files(pipeline, step)
        for path in files:
            if path.suffix == ".md":
                show_stage_file(path, pipeline.path, f"browse-output-{step}-{path.name}")
        if step in baseline:
            editor(step, f"Edit {step.title()} JSON")
            st.caption("Edit all stage content in the JSON above. Readable exports below are regenerated when you save.")
        if not files:
            st.info("No saved output yet. Generate this stage to create its data.")
        for path in files:
            if path.suffix == ".md":
                continue
            with st.expander(path.relative_to(pipeline.path).as_posix(), expanded=True):
                show_stage_file(path, pipeline.path, f"browse-output-{step}-{path.name}")
    with inputs:
        st.caption("Inputs are read-only. Edit content on its own workspace page.")
        st.markdown("**Story outline**")
        st.markdown(baseline["outline"])
        st.json(json.loads(baseline["project"]))
        for dependency in MODULES[step].dependencies:
            path = pipeline.path / MODULES[dependency].filename
            with st.expander(dependency.title()):
                if path.is_file():
                    for source_file in stage_files(pipeline, dependency):
                        show_stage_file(source_file, pipeline.path, f"browse-input-{step}-{dependency}-{source_file.name}")
                else:
                    st.info("No saved input yet.")
    with history:
        st.caption("Saved revisions are immutable records. Make changes in Output to create a new revision.")
        versions = sorted((pipeline.path / "history" / step).glob("*.json"), reverse=True)
        if versions:
            version = st.selectbox("Saved revision", versions, format_func=lambda path: path.stem,
                                   key=f"browse-history-{pipeline.project_id}-{step}")
            show_stage_file(version, pipeline.path, f"browse-version-{step}-{version.stem}")
            if step in baseline:
                def use_revision():
                    st.session_state[f"{draft_key}-{step}"] = version.read_text(encoding="utf-8-sig")
                st.button("Use revision as draft", key=f"restore-browser-{step}", on_click=use_revision,
                          help="Load this revision into the output editor. Save changes to create a new version.")
        else:
            st.info("No saved versions yet.")
    with activity:
        st.caption("Status, approvals, usage, and events are managed by the workflow; editing content updates the relevant state.")
        st.markdown("**Stage state**")
        st.json(state.model_dump(mode="json", by_alias=True) if state else {"status": "pending"})
        approval = project.approvals.get(step) if step in ("story", "storyboard") else None
        if approval:
            st.markdown("**Human approval**")
            st.json(approval.model_dump(mode="json", by_alias=True))
        for name, field in (("usage.json", None), ("logs/pipeline.jsonl", "jsonl")):
            path = pipeline.path / name
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8")
                records = [json.loads(line) for line in text.splitlines() if line.strip()] if field else json.loads(text)
                st.markdown("**" + ("Generation events" if field else "Usage and costs") + "**")
                related = {"characters": "character_references", "world": "backgrounds", "prompts": "scene_images", "storyboard": "voices"}.get(step, step)
                st.json([record for record in records if record.get("step") in (step, f"typo_check:{step}", f"modify:{step}")
                         or record.get("step", "").startswith(f"modify:{related}:")])
            except (OSError, ValueError, TypeError, AttributeError):
                st.warning(f"Cannot read {name}.")
    changes = {name: text for name, text in values.items() if text != baseline[name]}
    if changes:
        changes_summary.warning(f"{len(changes)} unsaved change(s): {', '.join(changes)}")
        with st.expander("Review changes", expanded=True):
            for name, text in changes.items():
                st.code("\n".join(difflib.unified_diff(baseline[name].splitlines(), text.splitlines(),
                                                      fromfile=f"Saved {name}", tofile=f"Edited {name}", lineterm="")), language="diff")
    else:
        changes_summary.caption("No unsaved changes")
    save, revert, regenerate, modify, typo, close = st.columns([1.2, 1.5, 1.8, 1.8, 1.1, 0.7])
    modify.button(text_action_label("Modify", pipeline, step), key="modify-browser-openai", disabled=busy() or bool(changes) or step not in baseline, on_click=open_modification, args=(pipeline.project_id, "stage", step,), kwargs={'expected': baseline.get(step), 'rerun': False})
    if save.button("Save changes", key="save-browser-changes", type="primary", disabled=busy() or not changes):
        try:
            pipeline.edit_bundle(changes, expected=baseline)
            clear_draft()
            st.session_state["browser-save-message"] = "Changes saved. Affected downstream stages and approvals have been invalidated."
            st.rerun()
        except (OSError, ValueError, RuntimeError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('ui:343', locals()))
    if generation_button(text_action_label("Regenerate", pipeline, step), key="regenerate-browser-openai", disabled=busy() or bool(changes),
                         help="Create and save a new version of this stage using OpenAI. Save or revert unsaved changes first. API usage is recorded.", container=regenerate):
        def regenerated(_):
            clear_draft()
            st.session_state["active-stage-browser"] = (pipeline.project_id, step)
            st.session_state["browser-save-message"] = f"{step.title()} regenerated with OpenAI. The previous version is available in History."
        launch(lambda: pipeline.regenerate_with_openai(step), f"Regenerating {step.title()}", regenerated)
    if changes:
        st.caption("Save or revert your draft before regenerating with OpenAI.")
    checked_values = {step: values[step]} if step in values else {}
    if typo.button("Check typos", key="check-browser-typos", disabled=busy() or not checked_values,
                   help="Use OpenAI to check only this stage's output draft. Inputs and other stages are excluded. API usage is recorded."):
        def checked_draft(report):
            st.session_state[f"{draft_key}-typo-report"] = {"draft_hash": input_hash(checked_values),
                                                           "report": report.model_dump()}
        launch(lambda: check_typos(pipeline, checked_values, step), f"{step.title()} typo check", checked_draft)
    checked = st.session_state.get(f"{draft_key}-typo-report")
    if checked:
        def apply_recommendation(index):
            stored = st.session_state[f"{draft_key}-typo-report"]
            current = {step: st.session_state.get(f"{draft_key}-{step}", baseline.get(step, ""))}
            if stored["draft_hash"] != input_hash(current):
                st.session_state[f"{draft_key}-apply-error"] = "The draft changed. Check typos again before applying suggestions."
                return
            try:
                suggestion = TypoSuggestion.model_validate(stored["report"]["suggestions"][index])
                updated = apply_suggestion(current, suggestion)
                st.session_state[f"{draft_key}-{suggestion.source}"] = updated
                current[suggestion.source] = updated
                stored["draft_hash"] = input_hash(current)
                stored.setdefault("applied", []).append(index)
                st.session_state[f"{draft_key}-typo-report"] = stored
            except ValueError as exc:
                st.session_state[f"{draft_key}-apply-error"] = str(exc)

        st.markdown("**Typo recommendations**")
        stale = checked["draft_hash"] != input_hash(checked_values)
        if stale:
            st.warning("The draft has changed since this check. Check typos again for current recommendations.")
        error = st.session_state.pop(f"{draft_key}-apply-error", None)
        if error:
            render_error(error, project_path=error_project(locals()), key=report_widget_key('ui:385', locals()))
        report = checked["report"]
        st.write(report["summary"])
        for index, recommendation in enumerate(report["suggestions"]):
            st.caption(f"{recommendation['source']} · {recommendation['field']}")
            before, after = st.columns(2)
            before.text("Original: " + recommendation["original"])
            after.text("Suggested: " + recommendation["replacement"])
            st.write(recommendation["explanation"])
            applied = index in checked.get("applied", [])
            st.button("Applied" if applied else "Apply suggestion", key=f"apply-typo-{index}",
                      disabled=applied or stale, on_click=apply_recommendation, args=(index,))
        if not report["suggestions"]:
            st.info("No typos were identified.")
        st.caption("Apply suggestions individually to update the draft. Save changes when ready, or Revert all changes to discard edits.")
    revert.button("Revert all changes", key="revert-browser-changes", disabled=not changes, on_click=reset_draft)
    st.caption("Revert restores all unsaved edits to their saved values. Close discards unsaved edits. History and activity remain read-only.")
    if close.button("Close", key="close-stage-browser"):
        clear_draft()
        st.rerun()


def _main():
    from .project_deletion_ui import clear_deleted_state, open_delete, delete_dialog
    clear_deleted_state()
    from .episode_removal_ui import clear_removed_state
    clear_removed_state()
    st.session_state.pop("external-project-busy", None)
    preserve_page_widgets()
    st.set_page_config(page_title="Story Studio", page_icon="📖", layout="wide")
    from .dialog_ui import show_pending_dialog
    show_pending_dialog()
    collect()
    st.title("Story Studio")
    from .api_keys_ui import api_key_button
    api_key_button()
    st.caption("World → reusable Assets → Story/Episode → Animations → Final")
    if "browser-save-message" in st.session_state:
        st.success(st.session_state.pop("browser-save-message"))
    try:
        settings = Settings.load()
    except (OSError, ValueError) as exc:
        render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('ui:423', locals()))
        return
    with st.sidebar:
        st.header("Workspace")
        root = st.text_input("Project storage", value=str(settings.data_root))
        settings.data_root = Path(root)
        manager = ProjectManager(settings)
        st.session_state["dialog-context-manager"] = manager
        projects = [p for p in manager.list_projects() if not p.world_project_id]
        if st.button("＋ New project", use_container_width=True):
            st.session_state["creating_project"] = True
        if projects:
            ids = [p.project_id for p in projects]
            titles = {p.project_id: p.title for p in projects}
            selected = st.session_state.get("selected_project", ids[0])
            selected = selected if selected in ids else ids[0]
            if st.session_state.get("project-selector") != selected:
                st.session_state["project-selector"] = selected

            def select_project():
                st.session_state["selected_project"] = st.session_state["project-selector"]
                st.session_state["creating_project"] = False
                st.session_state.pop("active-stage-browser", None)
                st.session_state.pop("modify-target", None)
                st.session_state.pop("voice-design-target", None)
                st.session_state.pop("studio-modify-target", None)
                st.session_state.pop("remove-character-target", None)
                st.session_state.pop("remove-episode-target",None)
                st.session_state.pop("storyboard-split-target",None)
                st.session_state.pop("storyboard-scene-removal-target",None)
                st.session_state.pop("storyboard-scene-insertion-target",None)
                st.session_state.pop("floor-plan-preview-target", None)
                st.session_state.pop("location-pair-revisions-target",None)

            project_id = st.selectbox("Project", ids, key="project-selector", on_change=select_project,
                                      format_func=lambda key: f"{titles[key]} · {key[:8]}")
            st.session_state.setdefault("selected_project", project_id)
            if st.button("Open selected project", use_container_width=True):
                st.session_state["creating_project"] = False
            project = next(item for item in projects if item.project_id == project_id)
            st.button("Delete project", key="delete-project", use_container_width=True, disabled=busy(),
                      on_click=open_delete, args=(project_id, project.title))
            previous = project.provider_config.get("llm")
            default_demo = previous.provider == "demo" if previous else settings.llm_provider == "demo"
        else:
            project_id, default_demo = None, settings.llm_provider == "demo"
        settings_key = project_id or "new"
        mode_key = f"project-setting-{settings_key}-generation-mode"
        model_key = f"project-setting-{settings_key}-llm-model"
        st.session_state.setdefault(mode_key, "Demo (offline)" if default_demo else "OpenAI")
        st.session_state.setdefault(model_key, previous.model if project_id and previous and previous.provider != "demo" else settings.llm_model)
        demo = st.session_state[mode_key].startswith("Demo")
        settings.llm_model = st.session_state[model_key]
        st.markdown("### Workspace pages")
        st.markdown("""<style>
        .st-key-workspace-navigation button {
            min-height: 2.8rem; border-radius: 0.6rem; justify-content: flex-start;
            padding: 0.4rem 0.8rem; border-width: 2px;
        }
        .st-key-workspace-navigation button p {font-size: 1.1rem; font-weight: 600;}
        .st-key-workspace-navigation button[kind="primary"] {
            background-color: #1d4ed8; border-color: #60a5fa; color: white;
            box-shadow: 0 0 0 1px #60a5fa;
        }
        </style>""", unsafe_allow_html=True)
        page = st.session_state.get("workspace_page", "World")
        if page in ("Story & Settings", "Outline", "Story", "Overview"):
            page = "Story/Episode"
        if page not in PAGES:
            page = "World"
        st.session_state["workspace_page"] = page

        def choose_page(name):
            st.session_state["workspace_page"] = name

        with st.container(key="workspace-navigation"):
            for name in PAGES:
                st.button(name, key="nav-" + ("story-settings" if name == "Story/Episode" else name.lower()), use_container_width=True,
                          type="primary" if name == page else "secondary", on_click=choose_page, args=(name,))
    from .generation_preferences_ui import generation_preferences_button
    generation_preferences_button(manager, None if st.session_state.get("creating_project") else project_id)
    delete_target = st.session_state.get("delete-project-target")
    if delete_target:
        delete_dialog(manager, delete_target)
        return
    if st.session_state.get("creating_project") or not project_id:
        from .studio_ui import new_world
        new_world(manager)
        return
    from .studio import WorldStudio, Episodes, approved_content
    world_id = project_id
    episodes = Episodes(manager, world_id).list()
    episode_id = None
    if episodes:
        episode_ids = [p.project_id for p in episodes]
        episode_titles = {p.project_id: p.title for p in episodes}
        selected = st.session_state.get(f"selected-episode-{world_id}", episode_ids[0])
        selected = selected if selected in episode_ids else episode_ids[0]
        with st.sidebar:
            key = f"episode-selector-{world_id}"
            if st.session_state.get(key) != selected:
                st.session_state[key] = selected
            def select_episode():
                from .episode_list_ui import select_episode as select_shared_episode
                select_shared_episode(world_id,st.session_state[key])
            episode_id = st.selectbox("Story / Episode", episode_ids, key=key, format_func=lambda pid: episode_titles[pid], on_change=select_episode)
            st.session_state.setdefault(f"selected-episode-{world_id}", episode_id)
    if page in ("Story/Episode", "Animations", "Final") and episode_id:
        project_id = episode_id
        if page == "Story/Episode" and not busy():
            Episodes(manager, world_id).sync(episode_id)
    llm_config = ProviderConfig(provider="demo" if demo else "openai", model="demo-v1" if demo else settings.llm_model,
                                options={"max_output_tokens": settings.llm_max_output_tokens})
    st.session_state["generation-context-project"] = project_id
    pipeline = Pipeline(manager, project_id, llm_config=llm_config)
    st.session_state["error-report-context-path"] = str(pipeline.path)
    task_status(pipeline.path)
    message = st.session_state.pop("workspace-task-message", None)
    if message:
        if message[0] == "error":
            context = st.session_state.pop("workspace-error-report-context", {})
            render_error(message[1], project_path=context.get("path") or pipeline.path,
                         task_key=context.get("task"), key="workspace-error-report")
        else:
            getattr(st, message[0])(message[1])
        if message[0] == "error" and st.session_state.get("outline-dialog-project") == project_id:
            st.session_state[f"outline-dialog-error-{project_id}"] = message[1]
        voice_target = st.session_state.get("voice-design-target")
        if message[0] == "error" and voice_target and voice_target["project_id"] == project_id:
            voice_subject = "_narrator" if voice_target.get("narrator") else voice_target["character_id"]
            st.session_state[f"voice-design-error-{project_id}-{voice_subject}"] = message[1]
    from .locking import ProjectBusyError
    from .ui_tasks import external_project_status
    try:
        project = manager.load(project_id) if busy() else pipeline.inspect()
    except ProjectBusyError:
        project = manager.load(project_id)
        st.session_state["external-project-busy"] = True
        external_project_status(pipeline.path)
    except (OSError, ValueError, RuntimeError) as exc:
        render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('ui:553', locals()))
        return
    if demo:
        st.info("Story Demo uses fixed sample content. Asset generation has its own mode on Assets.")
    st.subheader(project.title)
    from .billing_ui import billing_panel
    billing_panel(project, detailed=page == "Analytics", manager=manager)
    if page not in ("World", "Analytics") and not WorldStudio(manager, world_id).load().approval:
        st.info("Approve World settings on World before continuing to other pages.")
        return
    if page == "World":
        from .studio_ui import world_page
        world_page(manager, world_id, Pipeline(manager, world_id, llm_config=llm_config))
    elif page == "Assets":
        from .studio_assets_ui import assets_page as shared_assets_page
        shared_assets_page(manager, world_id, pipeline)
    elif page == "Story/Episode":
        from .studio_ui import episode_page
        episode_page(manager, world_id, episode_id, pipeline if episode_id else None)
    elif page == "Animations":
        from .episode_list_ui import animation_episode_list
        animation_episode_list(manager,world_id,episode_id)
        if not episode_id:
            st.info("Create or select an episode on Story/Episode first.")
        elif not all(approved_content(pipeline, name) for name in ("outline", "script", "prompts")) or not {"story", "storyboard"} <= project.approvals.keys():
            from .storyboard_asset_check import available as check_assets_available
            if check_assets_available(manager,episode_id):
                from .animations_ui import animations_page
                animations_page(manager,episode_id,check_assets_only=True)
            else:
                st.info("Approve this episode's Storyboard on Story/Episode to open Check assets. Storyboard approval prepares animation prompts automatically.")
        else:
            if not busy():
                try:
                    Episodes(manager, world_id).import_assets(episode_id)
                except (OSError, ValueError, RuntimeError) as exc:
                    st.warning(str(exc))
            from .animations_ui import animations_page
            animations_page(manager, project_id)
    elif page == "Final":
        if episode_id:
            from .final_ui import final_page
            final_page(manager, project_id)
        else:
            st.info("Create or select an episode before assembling Final.")
    elif page == "Analytics":
        from .workspace_ui import analytics_overview
        analytics_overview(manager, project_id)
        from .billing_allocations import assigned_billing
        _, billing_assignments = assigned_billing(manager, project_id)
        if billing_assignments:
            with st.expander("Assigned billed costs"):
                st.caption("Account billing snapshots assigned to this Story Studio project by explicit instruction.")
                st.json([record.model_dump(mode="json", by_alias=True) for record in billing_assignments])
        from .request_tracking import request_rows
        requests = request_rows(pipeline.path / "requests.jsonl")
        if requests:
            with st.expander("API request IDs and story attribution"):
                st.caption("Client request IDs are sent with future requests. Historical entries are assigned by your instruction; unavailable server IDs are left empty. Request IDs do not expose per-request billed costs.")
                st.dataframe(requests, hide_index=True, width="stretch")
        from .cost_estimates import project_usage_records
        records = project_usage_records(manager, project_id)
        if records:
            from .cost_estimates import estimate_usage
            st.caption("Estimated USD uses recorded usage and rough rates. Unknown means no usable usage was recorded. Job polls are deduplicated in the project total.")
            rows = [dict(r.model_dump(mode="json", by_alias=True),
                         estimatedCost=f"{r.estimated_cost:.2f}" if r.pricing_known else "Unknown",
                         cost=f"{r.cost:.2f}" if r.pricing_known else "Unknown",
                         roughEstimateUSD=f"{estimate_usage(r, manager.settings)[0]:.2f}" if estimate_usage(r, manager.settings)[0] is not None else "Unknown",
                         estimateBasis=estimate_usage(r, manager.settings)[1]) for r in records]
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No generation usage recorded yet. Publishing performance is planned for a future milestone.")
    show_modification(manager, project_id)
    from .voice_design_ui import show_voice_design
    show_voice_design(manager, project_id)
    from .studio_ui import show_modify
    show_modify(manager)
    remove_target = st.session_state.get("remove-character-target")
    if remove_target:
        from .character_removal_ui import removal_dialog
        removal_dialog(manager, remove_target)


def main():
    from .windows_asyncio import install_windows_transport_cleanup
    install_windows_transport_cleanup()
    try:
        _main()
    except PermissionError:
        render_error("Project files cannot be accessed right now. Wait for the current save to finish, then retry. If this continues, check file access permissions or whether another application is holding the files open.", project_path=error_project(locals()), key=report_widget_key('ui:634', locals()))
        if st.button("Retry loading project", key="retry-project-file-access"):
            st.rerun()
    from .error_report_ui import show_error_report
    show_error_report()
