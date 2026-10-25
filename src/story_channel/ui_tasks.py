"""Session-owned background work; workers never call Streamlit APIs."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import streamlit as st
from .asset_models import Production


def busy():
    task = st.session_state.get("workspace-task")
    return bool(st.session_state.get("external-project-busy") or task and not task["future"].done())


@st.fragment(run_every=1)
def external_project_status(project_path):
    from .locking import ProjectBusyError, project_lock
    try:
        with project_lock(project_path):
            pass
    except ProjectBusyError:
        st.caption("Project generation is active in another tab/session. Showing saved data; editing resumes automatically when it finishes.")
        return
    st.rerun(scope="app")


def collect():
    task = st.session_state.get("workspace-task")
    if not task or not task["future"].done():
        return
    del st.session_state["workspace-task"]
    try:
        result = task["future"].result()
        if task["success"]:
            task["success"](result)
        stopped = task.get("stop_event") and task["stop_event"].is_set()
        if not task.get("inline"):
            st.session_state["workspace-task-message"] = ("success", "Stopped after running tasks finished. Finished assets and costs were saved; generate again to resume." if stopped else task["label"] + " completed.")
    except Exception as exc:
        handled = task.get("failure") and task["failure"](exc)
        if not handled:
            st.session_state["workspace-task-message"] = ("error", str(exc))


def launch(callback, label="Generation", success=None, *, stop_event=None, progress_path=None, asset_engine=None,
           dialog_owner=None, rerun_on_start=True, failure=None, inline=False):
    if busy():
        st.warning("A request is already running. You can browse other workspace pages while it finishes.")
        return
    collect()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-workspace")
    future = executor.submit(callback)
    executor.shutdown(wait=False)
    st.session_state["workspace-task"] = {"future": future, "label": label, "success": success, "failure": failure, "stop_event": stop_event,
                                          "progress_path": progress_path, "asset_engine": asset_engine, "dialog_owner": dialog_owner, "inline": inline}
    if dialog_owner or not rerun_on_start:
        # The dialog's polling fragment updates its own result in place. A full
        # app rerun would dismiss and reconstruct the open dialog. Inline save
        # controls can also update themselves until normal completion polling.
        return
    # Fast local actions finish without an extra polling cycle.
    try:
        future.result(timeout=1)
    except Exception:
        pass
    # Apply completion callbacks at the start of the next app run, before their
    # editor widgets exist (Streamlit forbids changing a rendered widget's value).
    st.rerun()


def launch_assets(engine, callback, label="Generating assets"):
    event = Event()
    engine.stop_requested = event.is_set
    launch(callback, label, stop_event=event, progress_path=engine.state_path, asset_engine=engine)


def launch_render(engine, callback, label="Rendering video"):
    event = Event()
    engine.stop_requested = event.is_set
    launch(callback, label, stop_event=event, progress_path=engine.state_path)


def can_queue_asset(engine, stage, subject):
    task = st.session_state.get("workspace-task")
    running = task.get("asset_engine") if task and busy() else None
    return bool(running and running.path == engine.path and running.can_enqueue(stage, subject))


def queue_asset(engine, stage, subject, *, force=False):
    task = st.session_state.get("workspace-task")
    running = task.get("asset_engine") if task and busy() else None
    if running and running.path == engine.path:
        if running.enqueue(stage, subject, force=force):
            task["label"] = "Generating assets"
    st.rerun()


@st.fragment(run_every=1)
def task_status(project_path):
    task = st.session_state.get("workspace-task")
    if task:
        owner = task.get("dialog_owner")
        if owner and st.session_state.get(owner[0]) == owner[1]:
            # Keep the parent page stable while the open dialog owns progress.
            # Dismissing it restores normal background-task polling here.
            return
        if task["future"].done():
            st.rerun(scope="app")
        if task.get("inline"):
            # The owning controls show progress. Inserting a status above the
            # workspace would move every control when work starts or finishes.
            return
        stopping = bool(task.get("stop_event") and task["stop_event"].is_set())
        st.status("Stopping after running tasks finish…" if stopping else task["label"] + "…", state="running")
        if task.get("progress_path"):
            try:
                finishing_file = project_path / "finishing.json"
                if finishing_file.exists():
                    from .finishing_models import FinishingState
                    finishing = FinishingState.model_validate_json(finishing_file.read_text(encoding="utf-8"))
                    for record in finishing.records.values():
                        if record.status == "running":
                            st.caption(f"{record.key}: generating · attempt {record.attempts}" + (f" · {record.job_status} {record.progress}%" if record.job_id else ""))
                if task["progress_path"].name == "render.json":
                    from .render_models import RenderState
                    state = RenderState.model_validate_json(task["progress_path"].read_text(encoding="utf-8"))
                    completed = sum(r.status == "succeeded" for r in state.scenes.values())
                    st.caption(f"Scenes rendered: {completed}/{len(state.scenes)}")
                    for record in state.scenes.values():
                        if record.status == "running":
                            st.caption(f"{record.scene_id}: rendering" + (f" · OpenAI {record.remote_progress}%" if record.remote_job_id else ""))
                    animation_file = task["progress_path"].parent / "animation.json"
                    if animation_file.exists():
                        from .animation_models import AnimationState
                        animation = AnimationState.model_validate_json(animation_file.read_text(encoding="utf-8"))
                        for shot in animation.shots.values():
                            if shot.status == "running":
                                st.caption(f"Animating {shot.shot_id} · OpenAI {shot.remote_progress}% · attempt {shot.attempts}")
                else:
                    production = Production.model_validate_json(task["progress_path"].read_text(encoding="utf-8"))
                    st.caption(f"Running: {production.generation_active}/{production.max_parallel_tasks} · Queued: {production.generation_queued} · Finished: {production.generation_finished}/{production.generation_total}")
                    if production.generation_total:
                        st.progress(production.generation_finished / production.generation_total)
            except (OSError, ValueError):
                pass
        st.caption("You can change workspace pages while the current request finishes.")
        if task.get("stop_event") is not None:
            if st.button("Stop after running tasks", key="stop-workspace-task", disabled=stopping):
                task["stop_event"].set()
                st.rerun(scope="app")
        # Refresh persisted stage progress without giving the worker UI callbacks.
        signature = tuple(path.stat().st_mtime_ns if path.exists() else None
                          for path in (project_path / "manifest.json", project_path / "production.json", project_path / "render.json", project_path / "animation.json", project_path / "finishing.json", project_path / "animation_workflow_progress.json"))
        prior = st.session_state.get("workspace-task-progress")
        st.session_state["workspace-task-progress"] = signature
        if prior is not None and prior != signature:
            st.rerun(scope="app")


def preserve_page_widgets():
    # Reassigning keyed widget state detaches it from Streamlit's hidden-widget cleanup.
    for key in list(st.session_state):
        if key.startswith("ai-review-") and "-draft-" in key:
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith("reference-revisions-") and key.endswith(("-choice", "-panel")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("animation-preview-voice-", "asset-narrator-")):
            # Voice inputs use native session persistence. Reassigning their
            # widget state on full reruns can overwrite a fragment's edit.
            continue
        if key.startswith(("finishing-draft-", "finishing-setting-", "finishing-picked-", "finishing-typos-")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("asset-character-", "asset-editor-")) and not key.endswith(("-draft", "-baseline", "-typos", "-error")):
            continue
        if key.startswith(("animation-direction-panel-", "animation-scene-panel-", "project-setting-", "identity-draft-", "identity-panel-", "animation-plan-draft-", "animation-preview-text-", "animation-preview-voice-", "animation-voice-panel-", "animation-emotion-choice-", "animation-voice-check-", "animation-performance-check-", "animation-plan-check-", "animation-continuity-check-", "billing-project-draft-", "render-setting-", "render-plan-", "render-note-", "render-accept-", "outline-", "browser-draft-", "browse-history-", "modify-draft-", "asset-", "page-tab-",
                           "story-approval-note-", "story-approval-check-",
                           "storyboard-approval-note-", "storyboard-approval-check-")):
            st.session_state[key] = st.session_state[key]
