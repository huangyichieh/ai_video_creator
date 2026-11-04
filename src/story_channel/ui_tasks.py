"""Session-owned background work; workers never call Streamlit APIs."""
from concurrent.futures import ThreadPoolExecutor, Future
from threading import Event
from uuid import uuid4
from pathlib import Path

import streamlit as st
from .asset_models import Production


def workspace_tasks():
    primary = st.session_state.get("workspace-task")
    return ([primary] if primary else []) + list(st.session_state.get("workspace-parallel-tasks", []))


def find_task(predicate):
    return next((task for task in workspace_tasks() if predicate(task)), None)


def _result_key(task):
    project = Path(task["error_report_path"]).name if task.get("error_report_path") else task.get("generation_button_project")
    return (project, task.get("generation_button_key") or task["label"])


def _store_result(task, message, context=None):
    results = st.session_state.setdefault("workspace-task-results", {})
    results[_result_key(task)] = (message, context or {})
    if task.get("resource"):
        results[(_result_key(task)[0],task["resource"])] = (message,context or {})
    while len(results) > 200:
        results.pop(next(iter(results)))


def take_task_result(project_id, keys=(), *, prefixes=()):
    results = st.session_state.get("workspace-task-results", {})
    latest = None
    for key in list(results):
        if key[0] == project_id and (key[1] in keys or any(key[1].startswith(prefix) for prefix in prefixes)):
            latest = results.pop(key)
    return latest


def busy():
    return bool(st.session_state.get("external-project-busy") or
                any(not task["future"].done() for task in workspace_tasks()))


def can_queue_work():
    return bool(not st.session_state.get("external-project-busy") and
                not st.session_state.get("workspace-stopping") and
                not any(task.get("stop_event") is not None and task["stop_event"].is_set() for task in workspace_tasks()) and
                any(task.get("queue_enabled") for task in workspace_tasks()))


def generation_busy():
    return busy() and not can_queue_work()


def _start_task(task):
    from .service_limits import task_context
    callback = task.pop("callback")
    def run():
        task["phase"] = "running"
        with task_context(task):
            return callback()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-workspace")
    task["future"] = executor.submit(run)
    executor.shutdown(wait=False)
    if st.session_state.get("workspace-task") is None:
        st.session_state["workspace-task"] = task
    else:
        st.session_state.setdefault("workspace-parallel-tasks", []).append(task)


def _can_start(task, running):
    from .service_limits import LIMITS, activity
    # A completed API request waiting for a short file commit does not occupy
    # a provider slot. Bound the commit backlog separately from API work.
    if len(running) >= 32 or sum(item.get("phase") != "saving" for item in running) >= 5:
        return False
    service = task.get("initial_service")
    if service in LIMITS:
        reserved = sum(item.get("service", item.get("initial_service")) == service
                       and item.get("phase") != "saving" for item in running)
        if max(reserved, activity()[service]["active"]) >= LIMITS[service]:
            return False
    for other in running:
        if task.get("project_key", task.get("generation_button_project")) != other.get("project_key", other.get("generation_button_project")):
            continue
        if not task.get("allow_parallel") or not other.get("allow_parallel"):
            return False
        if task.get("resource") and task["resource"] == other.get("resource"):
            return False
        if not task.get("concurrent_safe") and not other.get("concurrent_safe"):
            return False  # One production/library writer per project.
    return True


def _dispatch():
    if st.session_state.get("workspace-stopping"):
        return
    queue = st.session_state.get("workspace-task-queue", [])
    running = [task for task in workspace_tasks() if not task["future"].done()]
    for task in list(queue):
        if _can_start(task, running):
            queue.remove(task)
            _start_task(task)
            running.append(task)


def stop_tasks():
    st.session_state["workspace-stopping"] = True
    for task in workspace_tasks():
        if task.get("stop_event") is not None:
            task["stop_event"].set()
    queue = st.session_state.get("workspace-task-queue", [])
    for task in queue:
        task["future"].cancel()
    queue.clear()


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


def collect(selected_task=None):
    finished = [task for task in workspace_tasks() if task["future"].done()
                and (selected_task is None or task is selected_task)]
    for task in finished:
        remaining = [item for item in workspace_tasks() if item is not task]
        st.session_state.pop("workspace-task", None)
        st.session_state["workspace-parallel-tasks"] = remaining[1:]
        if remaining:
            st.session_state["workspace-task"] = remaining[0]
        _collect_task(task)
        if task.get("ui_owned_asset_project") and task.get("resource"):
            revisions = st.session_state.setdefault("workspace-resource-completions",{})
            key = (task["ui_owned_asset_project"],task["resource"])
            revisions[key] = revisions.get(key,0)+1
            while len(revisions) > 256:
                revisions.pop(next(iter(revisions)))
        if task.get("stop_event") and task["stop_event"].is_set():
            stop_tasks()
    if not workspace_tasks():
        st.session_state.pop("workspace-stopping", None)
    _dispatch()


def _collect_task(task):
    try:
        result = task["future"].result()
        if task["success"]:
            task["success"](result)
        if task.get("error_report_path"):
            from .error_reports import clear_reports
            try:
                clear_reports(task["error_report_path"], task["error_report_task"])
            except (OSError, ValueError):
                pass
        stopped = task.get("stop_event") and task["stop_event"].is_set()
        if not task.get("inline"):
            st.session_state["workspace-task-message"] = ("success", "Stopped after running tasks finished. Finished assets and costs were saved; generate again to resume." if stopped else task["label"] + " completed.")
            _store_result(task, st.session_state["workspace-task-message"])
    except Exception as exc:
        if isinstance(exc, InterruptedError) and task.get("stop_event") is not None and task["stop_event"].is_set():
            st.session_state["workspace-task-message"] = ("success", "Stopped. Completed media and costs were retained.")
            _store_result(task, st.session_state["workspace-task-message"])
            return
        path = getattr(exc, "error_report_project_path", None) or task.get("error_report_path")
        report_task = getattr(exc, "error_report_task", None) or task.get("error_report_task", "operation")
        if path:
            from .error_reports import matching_reports, record_failure
            try:
                if not matching_reports(path, str(exc)):
                    record_failure(path, report_task, exc)
            except (OSError, ValueError):
                pass
        handled = task.get("failure") and task["failure"](exc)
        if not handled:
            st.session_state["workspace-task-message"] = ("error", str(exc))
            st.session_state["workspace-error-report-context"] = {"path": path, "task": report_task}
            _store_result(task, st.session_state["workspace-task-message"], st.session_state["workspace-error-report-context"])


def launch(callback, label="Generation", success=None, *, stop_event=None, progress_path=None, asset_engine=None,
           dialog_owner=None, rerun_on_start=True, failure=None, inline=False, queue_enabled=False,
           concurrent_safe=False, allow_parallel=False, resource=None, project_path=None, service=None):
    if busy() and not can_queue_work():
        st.warning("A request is already running. You can browse other workspace pages while it finishes.")
        return
    collect()
    trigger = st.session_state.pop("generation-button-trigger", None)
    generation_button_key = trigger if trigger and st.session_state.get(trigger, False) else None
    report_path = str(project_path) if project_path else str(progress_path.parent) if progress_path else st.session_state.get("error-report-context-path")
    task = {"id": uuid4().hex, "phase": "queued", "concurrent_safe": concurrent_safe,
            "initial_service": service,
            "allow_parallel": allow_parallel or concurrent_safe, "resource": resource,
            "project_key": report_path or st.session_state.get("generation-context-project"),
            "future": Future(), "callback": callback, "queue_enabled": queue_enabled or can_queue_work(), "label": label, "success": success, "failure": failure, "stop_event": stop_event,
                                          "progress_path": progress_path, "asset_engine": asset_engine, "dialog_owner": dialog_owner, "inline": inline,
                                          "generation_button_key": generation_button_key,
                                          "error_report_path": report_path,
                                          "error_report_task": "operation:" + (generation_button_key or label),
                                          "generation_button_project": st.session_state.get("generation-context-project")}
    st.session_state.get("workspace-task-results", {}).pop(_result_key(task), None)
    running = [item for item in workspace_tasks() if not item["future"].done()]
    if not _can_start(task, running):
        st.session_state.setdefault("workspace-task-queue", []).append(task)
        if rerun_on_start and not dialog_owner:
            st.rerun()
        return task
    _start_task(task)
    future = task["future"]
    if dialog_owner or not rerun_on_start:
        # The dialog's polling fragment updates its own result in place. A full
        # app rerun would dismiss and reconstruct the open dialog. Inline save
        # controls can also update themselves until normal completion polling.
        return task
    if generation_button_key:
        st.rerun()
    # Fast local actions finish without an extra polling cycle.
    try:
        future.result(timeout=1)
    except Exception:
        pass
    # Apply completion callbacks at the start of the next app run, before their
    # editor widgets exist (Streamlit forbids changing a rendered widget's value).
    st.rerun()


def launch_assets(engine, callback, label="Generating assets", *, rerun_on_start=True, service=None):
    event = Event()
    def run():
        engine.stop_requested = event.is_set
        return callback()
    service = service or (engine.settings.tts_provider if "voice" in label.lower() else engine.settings.image_provider)
    return launch(run, label, stop_event=event, progress_path=engine.state_path, asset_engine=engine, rerun_on_start=rerun_on_start, queue_enabled=True, allow_parallel=True, service=service)


def launch_render(engine, callback, label="Rendering video"):
    event = Event()
    engine.stop_requested = event.is_set
    launch(callback, label, stop_event=event, progress_path=engine.state_path)


def can_queue_asset(engine, stage, subject):
    if can_queue_work():
        return True
    task = find_task(lambda item: item.get("asset_engine") is not None and item["asset_engine"].path == engine.path and not item["future"].done())
    running = task.get("asset_engine") if task and busy() else None
    return bool(running and running.path == engine.path and running.can_enqueue(stage, subject))


def asset_activity(engine, stage, subject):
    task = find_task(lambda item: item.get("asset_engine") is not None and item["asset_engine"].path == engine.path and not item["future"].done())
    running = task.get("asset_engine") if task and not task["future"].done() else None
    if running and running.path == engine.path:
        return running.task_activity(stage, subject)
    return None


def queue_asset(engine, stage, subject, *, force=False):
    if can_queue_work():
        launch_assets(engine, lambda: engine.generate(stage, subject=subject, force=force), "Generating " + subject,
                      rerun_on_start=False)
        st.rerun()
    task = find_task(lambda item: item.get("asset_engine") is not None and item["asset_engine"].path == engine.path and not item["future"].done())
    running = task.get("asset_engine") if task and busy() else None
    if running and running.path == engine.path:
        if running.enqueue(stage, subject, force=force):
            task["label"] = "Generating assets"
    st.rerun()


def queue_status(task, *, key="stop-queued-workspace-tasks"):
    """Render queue progress in the owning UI without forcing an app rerun."""
    _dispatch()
    queue = st.session_state.get("workspace-task-queue", [])
    tasks = workspace_tasks()
    if task and (queue or any(item.get("queue_enabled") for item in tasks)):
        from .service_limits import activity
        counts = activity()
        names = {"openai": "OpenAI", "runway": "Runway", "elevenlabs": "ElevenLabs"}
        summary = " · ".join(f"{names[name]}: {values['active']}/{values['limit']}" for name, values in counts.items())
        st.caption(f"Generation requests: {sum(item['active'] for item in counts.values())}/5 · Active jobs: {sum(not item['future'].done() for item in tasks)} · Queued: {len(queue)} · " + summary)
        rows = []
        for item in tasks:
            phase = item.get("phase", "running")
            status = "Finishing" if item["future"].done() else "Waiting for service" if phase == "waiting" else "Saving" if phase == "saving" else "Generating"
            rows.append({"Task": item["label"], "Service": item.get("service") or item.get("initial_service") or "Pipeline", "Status": status})
        rows.extend({"Task": item["label"], "Service": item.get("initial_service") or "Pending", "Status": "Queued"} for item in queue)
        st.table(rows)
        if any(item.get("stop_event") is not None for item in tasks):
            st.button("Stop after running tasks", key=key, disabled=bool(st.session_state.get("workspace-stopping")), on_click=stop_tasks)


@st.fragment(run_every=1)
def task_status(project_path):
    _dispatch()
    if st.session_state.pop("dialog-open-requested", False):
        st.rerun(scope="app")
    task = st.session_state.get("workspace-task")
    stable_scene_view = (st.session_state.get("workspace_page") == "Animations"
        and st.session_state.get(f"page-tab-animations-{project_path.name}") == "Scene images")
    if (task and st.session_state.get("workspace_page") == "Animations"
            and task.get("ui_owned_asset_project") == project_path.name
            and str(task.get("resource","")).startswith("storyboard-audio:")):
        # Audio cards and the batch control own dispatch/completion polling.
        # A full-app rerun per completed sound rebuilds the whole checklist.
        return
    if (st.session_state.get("workspace_page") == "Assets" and any(
            project_path.name in (item.get("sample_project"), item.get("costume_project"), item.get("visual_design_project"), item.get("stable_background_project"), item.get("ui_owned_asset_project"))
            or item.get("asset_engine") is not None and item["asset_engine"].project_id == project_path.name
            for item in workspace_tasks())):
        # Character media controls own progress and completion. Inserting status above
        # the cards or rebuilding the page would displace the current viewport.
        return
    queue_status(task)
    if task:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        context = get_script_run_ctx()
        fragment_only = bool(context and context.fragment_ids_this_run)
        owner = task.get("dialog_owner")
        if owner and st.session_state.get(owner[0]) == owner[1]:
            # Keep the parent page stable while the open dialog owns progress.
            # Dismissing it restores normal background-task polling here.
            return
        if (task.get("stable_background_project") == project_path.name
                and st.session_state.get("workspace_page") == "Assets"):
            # The background fragment owns controls, completion and result
            # polling. Full-app progress reruns would remove its cards/forms.
            return
        if task.get("style_check_project") == project_path.name and st.session_state.get("workspace_page") == "Assets":
            return
        if task["future"].done():
            if fragment_only:
                st.rerun(scope="app")
            return
        engine = task.get("asset_engine")
        if engine:
            snapshot = engine.activity_snapshot()
            previous = task.get("asset-activity-snapshot")
            task["asset-activity-snapshot"] = snapshot
            if fragment_only and previous is not None and previous != snapshot and not stable_scene_view:
                # Refresh labels only when a task changes state, not on every tick.
                st.rerun(scope="app")
        if (task.get("character_generation_project") == project_path.name
                and st.session_state.get("workspace_page") == "Assets"):
            # Character controls already show compact progress beside the button.
            # Keep polling for completion without adding a second status box.
            return
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
                    from .file_cache import read_model
                    finishing = read_model(finishing_file, FinishingState)
                    for record in finishing.records.values():
                        if record.status == "running":
                            st.caption(f"{record.key}: generating · attempt {record.attempts}" + (f" · {record.job_status} {record.progress}%" if record.job_id else ""))
                if task["progress_path"].name == "render.json":
                    from .render_models import RenderState
                    from .file_cache import read_model
                    state = read_model(task["progress_path"], RenderState)
                    completed = sum(r.status == "succeeded" for r in state.scenes.values())
                    st.caption(f"Scenes rendered: {completed}/{len(state.scenes)}")
                    for record in state.scenes.values():
                        if record.status == "running":
                            st.caption(f"{record.scene_id}: rendering" + (f" · OpenAI {record.remote_progress}%" if record.remote_job_id else ""))
                    animation_file = task["progress_path"].parent / "animation.json"
                    if animation_file.exists():
                        from .animation_models import AnimationState
                        animation = read_model(animation_file, AnimationState)
                        for shot in animation.shots.values():
                            if shot.status == "running":
                                st.caption(f"Animating {shot.shot_id} · OpenAI {shot.remote_progress}% · attempt {shot.attempts}")
                else:
                    from .file_cache import read_model
                    production = read_model(task["progress_path"], Production)
                    st.caption(f"Running: {production.generation_active}/{production.max_parallel_tasks} · Queued: {production.generation_queued} · Finished: {production.generation_finished}/{production.generation_total}")
                    if production.generation_total:
                        st.progress(production.generation_finished / production.generation_total)
            except (OSError, ValueError):
                pass
        st.caption("You can change workspace pages while the current request finishes.")
        if task.get("stop_event") is not None and not task.get("queue_enabled"):
            if st.button("Stop after running tasks", key="stop-workspace-task", disabled=stopping):
                task["stop_event"].set()
                st.rerun(scope="app")
        # Refresh persisted stage progress without giving the worker UI callbacks.
        signature = tuple(path.stat().st_mtime_ns if path.exists() else None
                          for path in (project_path / "manifest.json", project_path / "production.json", project_path / "render.json", project_path / "animation.json", project_path / "finishing.json", project_path / "animation_workflow_progress.json"))
        prior = st.session_state.get("workspace-task-progress")
        st.session_state["workspace-task-progress"] = signature
        if fragment_only and prior is not None and prior != signature and not stable_scene_view:
            st.rerun(scope="app")


def preserve_page_widgets():
    from .ui_panels import preserve_panels
    preserve_panels()
    # Reassigning keyed widget state detaches it from Streamlit's hidden-widget cleanup.
    for key in list(st.session_state):
        if key.startswith(("character-library-card-", "character-costume-card-", "background-card-", "floor-plan-settings-")) or (
                key.startswith(("background-description-", "canonical-review-", "location-pair-prompt-")) or
                key.startswith("location-reference-set-") and key.endswith(("-type", "-floor", "-layout", "-area"))
                and not key.endswith("-generate-layout")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("studio-draft-", "studio-review-choice-", "selected-episode-", "episode-selector-", "studio-sample-emotion-")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith("ai-review-") and "-draft-" in key:
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("reference-revisions-", "character-revisions-")) and key.endswith(("-choice", "-panel")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("animation-preview-voice-", "asset-narrator-")):
            # Voice inputs use native session persistence. Reassigning their
            # widget state on full reruns can overwrite a fragment's edit.
            continue
        if key.startswith(("finishing-draft-", "finishing-setting-", "finishing-picked-", "finishing-typos-")):
            st.session_state[key] = st.session_state[key]
            continue
        if key.startswith(("asset-character-", "asset-editor-")) and not key.endswith(("-draft", "-baseline", "-typos", "-error", "-edit-json", "-edit-mode", "-metadata-json")):
            continue
        if key.startswith(("animation-direction-panel-", "animation-scene-panel-", "project-setting-", "identity-draft-", "identity-panel-", "animation-plan-draft-", "animation-preview-text-", "animation-preview-voice-", "animation-voice-panel-", "animation-emotion-choice-", "animation-voice-check-", "animation-performance-check-", "animation-plan-check-", "animation-continuity-check-", "billing-project-draft-", "render-setting-", "render-plan-", "render-note-", "render-accept-", "outline-", "browser-draft-", "browse-history-", "modify-draft-", "asset-", "page-tab-",
                           "story-approval-note-", "story-approval-check-",
                           "storyboard-approval-note-", "storyboard-approval-check-")):
            st.session_state[key] = st.session_state[key]
