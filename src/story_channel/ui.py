import json
import difflib
import re
import tempfile
from pathlib import Path

import streamlit as st

from .config import Settings
from .costs import CostTracker
from .modules import MODULES, STEPS, stage_title
from .pipeline import Pipeline
from .projects import ProjectManager
from .models import ProviderConfig
from .files import input_hash
from .proofreading import TypoSuggestion, apply_suggestion, check_typos
from .assets_ui import assets_page
from .modification_ui import open_modification, show_modification
from .action_labels import text_action_label
from .ui_tasks import busy, collect, launch, preserve_page_widgets, task_status

PAGES = ("Story & Settings", "Assets", "Animations", "Final", "Analytics")


def stage_rows(project):
    rows = []
    for step in STEPS:
        state = project.steps.get(step)
        status = state.status if state else "pending"
        if status in ("pending", "stale"):
            dependencies_ready = all(project.steps.get(d) and project.steps[d].status == "succeeded"
                                     for d in MODULES[step].dependencies)
            if step in ("script", "storyboard", "prompts") and "story" not in project.approvals:
                status = "Blocked: story approval"
            elif step == "prompts" and "storyboard" not in project.approvals:
                status = "Blocked: storyboard approval"
            elif dependencies_ready:
                status = "Ready" if status == "pending" else "Ready (outdated)"
            else:
                status = "Waiting for earlier stages"
        else:
            status = {"running": "▶ Running", "succeeded": "Completed", "failed": "Failed"}.get(status, status)
        rows.append({"Stage": stage_title(step), "Status": status,
                     "Revision": state.revision if state else 0, "Error": state.error or "" if state else ""})
        if step == "review" and status == "Completed":
            rows[-1]["Status"] = "Approved" if "story" in project.approvals else "Awaiting review decisions / approval"
        if step == "storyboard":
            approved = "storyboard" in project.approvals
            ready = state is not None and state.status == "succeeded"
            rows.append({"Stage": "AI Review (Storyboard)", "Status": "Approved" if approved else "Awaiting review decisions / approval" if ready else "Waiting",
                         "Revision": None, "Error": ""})
    return rows


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
    markdown = {path for path in files if path.suffix == ".md"}
    redundant = redundant_json_sources(files)
    hidden_markdown = set()
    for path in files:
        if path.suffix == ".json":
            try:
                exported = readable_export(path)
                markdown.add(exported)
                if path in redundant:
                    hidden_markdown.add(exported)
            except (OSError, ValueError, UnicodeError):
                st.warning(f"Cannot create a readable export for {path.relative_to(pipeline.path).as_posix()}. Use Show JSON to inspect it.")
    for path in sorted(markdown - hidden_markdown):
        show_stage_file(path, pipeline.path, f"{prefix}-{path.relative_to(pipeline.path).as_posix()}")
    if show_json:
        for path in files:
            if path.suffix == ".json" and path not in redundant:
                show_stage_file(path, pipeline.path, f"{prefix}-{path.relative_to(pipeline.path).as_posix()}")


@st.dialog("AI Review (Storyboard)", width="large", on_dismiss=dismiss_stage_browser)
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


@st.dialog("AI Review (Story)", width="large", on_dismiss=dismiss_stage_browser)
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
    if st.button(action_label("AI Review (Story)", pipeline.config.provider, pipeline.config.model),
                 key="run-ai-review-story", disabled=busy() or not sources_ready):
        launch_review(pipeline, "story", lambda: run_story_review(pipeline))
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


@st.dialog("Stage data", width="large", on_dismiss=dismiss_stage_browser)
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
    if modify.button(text_action_label("Modify", pipeline), key="modify-browser-openai", disabled=busy() or bool(changes) or step not in baseline):
        open_modification(pipeline.project_id, "stage", step, expected=baseline[step])
    if save.button("Save changes", key="save-browser-changes", type="primary", disabled=busy() or not changes):
        try:
            pipeline.edit_bundle(changes, expected=baseline)
            clear_draft()
            st.session_state["browser-save-message"] = "Changes saved. Affected downstream stages and approvals have been invalidated."
            st.rerun()
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
    if regenerate.button(text_action_label("Regenerate", pipeline), key="regenerate-browser-openai", disabled=busy() or bool(changes),
                         help="Create and save a new version of this stage using OpenAI. Save or revert unsaved changes first. API usage is recorded."):
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
            st.error(error)
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


def action(callback):
    launch(callback, "Story operation")


def new_project(manager):
    st.header("New project")
    with st.form("new_project"):
        title = st.text_input("Title")
        language = st.text_input("Content language", value=manager.settings.default_language)
        st.write("Target length")
        default_hours, remainder = divmod(manager.settings.default_target_duration_sec, 3600)
        default_minutes, default_seconds = divmod(remainder, 60)
        hours_column, minutes_column, seconds_column = st.columns(3)
        hours = hours_column.number_input("Hours", min_value=0, value=default_hours, step=1)
        minutes = minutes_column.number_input("Minutes", min_value=0, max_value=59, value=default_minutes, step=1)
        seconds = seconds_column.number_input("Seconds", min_value=0, max_value=59, value=default_seconds, step=1)
        duration = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        outline = st.text_area("Story outline", height=250, placeholder="Describe the premise, characters, conflict, and ending…")
        if st.form_submit_button("Create project", type="primary"):
            try:
                if duration == 0:
                    raise ValueError("Target length must be at least one second.")
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "outline.md"
                    source.write_text(outline, encoding="utf-8")
                    project = manager.create(title, source, language=language, target_duration_sec=int(duration))
                st.session_state["selected_project"] = project.project_id
                st.session_state["creating_project"] = False
                st.session_state["workspace_page"] = "Story & Settings"
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(str(exc))



def story_workflow(manager, project_id, pipeline, project):
    from .outline_ui import outline_heading
    outline_heading(manager, project_id, pipeline)
    st.markdown(manager.read_outline(project_id))
    next_step = project.current_step
    next_label = stage_title(next_step)
    is_review = next_step in ("story_review", "storyboard_review")
    def open_review():
        st.session_state["active-stage-browser"] = (project_id, "review" if next_step == "story_review" else "storyboard_approval")
    generate_next = st.button(("Next stage: " if is_review else "Generate next stage: ") + next_label, key="generate-next-stage", type="primary",
                              disabled=busy() or next_step not in (*STEPS, "story_review", "storyboard_review"),
                              on_click=open_review if is_review else None)
    generate_next = generate_next and not is_review
    run_all = st.button("Generate / Resume pipeline", disabled=busy() or next_step == "complete",
                        help="Automatic generation: runs remaining stages until review decisions and approval are required.")
    st.caption("Generates one stage, then stops. Use each stage's Browse button to inspect, edit, or regenerate its result.")
    if next_step in ("story_review", "storyboard_review"):
        st.info("Open AI Review (Story), resolve every difference, then save and approve before continuing." if next_step == "story_review"
                else "Open AI Review (Storyboard), resolve each difference or remain unchanged, then approve before continuing.")
    elif next_step == "complete":
        st.success("All stages are complete.")
    progress_message = st.empty()
    stage_cells = {}
    browse_requested = None
    stage_lookup = {stage_title(step): step for step in STEPS}
    stage_lookup.update({"AI Review (Storyboard)": "storyboard"})
    column_widths = [2.3, 2.8, 1, 4, 1.3]
    with st.container(border=True):
        headers = st.columns(column_widths)
        for column, label in zip(headers, ("Stage", "Status", "Revision", "Error", "Browse")):
            column.markdown(f"**{label}**")
        for row in stage_rows(project):
            st.divider()
            name, status, revision, error, browse = st.columns(column_widths, vertical_alignment="center")
            name.write(row["Stage"])
            stage_cells[row["Stage"]] = (status.empty(), revision.empty(), error.empty())
            step = stage_lookup[row["Stage"]]
            is_approval = row["Stage"] == "AI Review (Storyboard)"
            key = f"browse-{step}-approval" if is_approval else f"browse-{step}"
            if browse.button("Browse", key=key, help=f"View {row['Stage'].lower()} data", use_container_width=True):
                browse_requested = f"{step}_approval" if is_approval else step

    def render_progress(manifest):
        for row in stage_rows(manifest):
            status_cell, revision_cell, error_cell = stage_cells[row["Stage"]]
            status = row["Status"]
            color = ("blue" if status == "▶ Running" else "green" if status in ("Completed", "Approved")
                     else "red" if status == "Failed" else "orange" if "approval" in status.lower() else "gray")
            status_cell.markdown(f":{color}[{status}]")
            revision_cell.write(str(row["Revision"]) if row["Revision"] is not None else "—")
            error_cell.caption(row["Error"] or "—")
        running = [step.title() for step, state in manifest.steps.items() if state.status == "running"]
        if running:
            progress_message.info(f"Working on: {', '.join(running)}")
        else:
            progress_message.markdown(f"Next step:  \n**{stage_title(manifest.current_step)}**")

    render_progress(project)
    # Background work persists progress to disk, independently of this page's widgets.
    pipeline.on_progress = None
    if generate_next:
        def generate_one():
            result = pipeline.generate(next_step, force=project.steps.get(next_step) is not None and project.steps[next_step].status == "failed")
            if next_step == "review":
                from .review_changes import prepare_review
                prepare_review(pipeline, "story")
            return result
        action(generate_one)
    elif run_all:
        def run_story_pipeline():
            result = pipeline.run()
            if result.current_step == "story_review":
                from .review_changes import prepare_review
                prepare_review(pipeline, "story")
            return result
        action(run_story_pipeline)
    if browse_requested is not None:
        if browse_requested in ("story_approval", "storyboard_approval"):
            st.session_state.pop(f"approval-json-visible-{project_id}-{browse_requested}", None)
        st.session_state["active-stage-browser"] = (project_id, browse_requested)
    show_active_browser(pipeline)
    st.write("Approved:", ", ".join(project.approved_steps) or "None")
    st.code(str(pipeline.path), language=None)


def outline_editor(manager, project_id, pipeline, project):
    outline_key = f"outline-{project_id}"
    report_key = f"outline-typos-{project_id}"
    if outline_key not in st.session_state:
        st.session_state[outline_key] = manager.read_outline(project_id)
    outline = st.text_area("Edit outline", height=400, key=outline_key)
    save_outline, check_outline, modify_outline = st.columns(3)
    if modify_outline.button(text_action_label("Modify", pipeline), key="modify-outline-openai", disabled=busy() or outline != manager.read_outline(project_id) or not outline.strip()):
        open_modification(project_id, "stage", "outline", expected=outline)
    if save_outline.button("Save outline", type="primary", disabled=busy()):
        action(lambda: pipeline.edit("outline", outline))
    if check_outline.button("Check typos", key="check-outline-typos", disabled=busy() or not outline.strip(),
                            help="Check only this outline draft with OpenAI; API usage is recorded."):
        def checked_outline(report):
            st.session_state[report_key] = {"draft_hash": input_hash({"outline": outline}), "report": report.model_dump()}
        launch(lambda: check_typos(pipeline, {"outline": outline}, "outline"), "Outline typo check", checked_outline)
    if outline != manager.read_outline(project_id):
        st.caption("🟠 Modified — not saved")
    checked = st.session_state.get(report_key)
    if checked:
        def apply_outline_recommendation(index):
            stored = st.session_state[report_key]
            current = {"outline": st.session_state[outline_key]}
            if stored["draft_hash"] != input_hash(current):
                return
            try:
                suggestion = TypoSuggestion.model_validate(stored["report"]["suggestions"][index])
                text = apply_suggestion(current, suggestion)
                st.session_state[outline_key] = text
                stored["draft_hash"] = input_hash({"outline": text})
                stored.setdefault("applied", []).append(index)
            except ValueError as exc:
                st.session_state[f"{report_key}-error"] = str(exc)

        stale = checked["draft_hash"] != input_hash({"outline": outline})
        if stale:
            st.warning("The outline changed since this check. Check typos again for current recommendations.")
        if f"{report_key}-error" in st.session_state:
            st.error(st.session_state.pop(f"{report_key}-error"))
        st.write(checked["report"]["summary"])
        for index, suggestion in enumerate(checked["report"]["suggestions"]):
            st.text(f"{suggestion['original']} → {suggestion['replacement']}")
            st.write(suggestion["explanation"])
            applied = index in checked.get("applied", [])
            st.button("Applied" if applied else "Apply suggestion", key=f"apply-outline-typo-{index}",
                      disabled=stale or applied, on_click=apply_outline_recommendation, args=(index,))
        if not checked["report"]["suggestions"]:
            st.info("No typos were identified.")
    st.caption("Saving changes invalidates generated modules and approvals.")


def _main():
    st.session_state.pop("external-project-busy", None)
    preserve_page_widgets()
    collect()
    st.set_page_config(page_title="Story Studio", page_icon="📖", layout="wide")
    st.title("Story Studio")
    from .api_keys_ui import api_key_button
    api_key_button()
    st.caption("From a story outline to a reviewed production plan")
    if "browser-save-message" in st.session_state:
        st.success(st.session_state.pop("browser-save-message"))
    try:
        settings = Settings.load()
    except (OSError, ValueError) as exc:
        st.error(str(exc))
        return
    with st.sidebar:
        st.header("Workspace")
        root = st.text_input("Project storage", value=str(settings.data_root))
        settings.data_root = Path(root)
        manager = ProjectManager(settings)
        projects = manager.list_projects()
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

            project_id = st.selectbox("Project", ids, key="project-selector", on_change=select_project,
                                      format_func=lambda key: f"{titles[key]} · {key[:8]}")
            st.session_state.setdefault("selected_project", project_id)
            if st.button("Open selected project", use_container_width=True):
                st.session_state["creating_project"] = False
            project = next(item for item in projects if item.project_id == project_id)
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
        page = st.session_state.get("workspace_page", "Story & Settings")
        if page == "Outline" and project_id:
            st.session_state[f"page-tab-story-{project_id}"] = "Story outline"
        if page not in PAGES:
            page = "Story & Settings"
        st.session_state["workspace_page"] = page

        def choose_page(name):
            st.session_state["workspace_page"] = name

        with st.container(key="workspace-navigation"):
            for name in PAGES:
                st.button(name, key="nav-" + ("story-settings" if name == "Story & Settings" else name.lower()), use_container_width=True,
                          type="primary" if name == page else "secondary", on_click=choose_page, args=(name,))
    if st.session_state.get("creating_project") or not project_id:
        new_project(manager)
        return
    llm_config = ProviderConfig(provider="demo" if demo else "openai", model="demo-v1" if demo else settings.llm_model,
                                options={"max_output_tokens": settings.llm_max_output_tokens})
    pipeline = Pipeline(manager, project_id, llm_config=llm_config)
    task_status(pipeline.path)
    message = st.session_state.pop("workspace-task-message", None)
    if message:
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
        st.error(str(exc))
        return
    if demo:
        st.info("Demo mode uses fixed sample content to demonstrate the workflow. It does not generate a custom story or call an API.")
    st.subheader(project.title)
    from .billing_ui import billing_panel
    billing_panel(project, detailed=page == "Analytics", manager=manager)
    if page == "Story & Settings":
        from .workspace_ui import story_settings
        story_settings(manager, project_id, pipeline, project, story_workflow, outline_editor)
    elif page == "Assets":
        assets_page(manager, project_id)
    elif page == "Animations":
        from .animations_ui import animations_page
        animations_page(manager, project_id)
    elif page == "Final":
        from .final_ui import final_page
        final_page(manager, project_id)
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
        records = CostTracker(manager, project_id).records()
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


def main():
    try:
        _main()
    except PermissionError:
        st.error("Project files cannot be accessed right now. Wait for the current save to finish, then retry. If this continues, check file access permissions or whether another application is holding the files open.")
        if st.button("Retry loading project", key="retry-project-file-access"):
            st.rerun()
