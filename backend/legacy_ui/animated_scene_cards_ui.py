"""Stable animated-scene cards with immediate feedback and serialized queues."""
from threading import Event

import streamlit as st

from .asset_view_ui import data,refresh_target
from .files import input_hash
from .generation_ui import generation_button
from .image_preview_ui import image_preview
from .rendering import RenderPipeline
from .scene_production import SceneProduction
from .scene_image_cards_ui import loading_preview,polling_tick
from .ui_tasks import busy,can_queue_work,workspace_tasks,collect,take_task_result,launch


def target_key(pid,sid):
    return f"animated-scene-card-{pid}-{sid}"


def jobs(pid,sid=None):
    return [task for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("animated_scene_project") == pid and (sid is None or task.get("asset_subject") == sid)]


def phase(pid,sid,record):
    if request := st.session_state.get(target_key(pid,sid)+"-request"):
        return request["phase"]
    for task in jobs(pid,sid):
        if not task["future"].done():
            return "queued" if task.get("phase") in ("queued","waiting") else "running"
    return "running" if record.get("status") == "running" else None


def request(pid,sid,operation,*,refresh_scope=None,**values):
    st.session_state[target_key(pid,sid)+"-request"] = {
        "operation":operation,"phase":"queued" if busy() else "running",**values}
    st.session_state.pop(target_key(pid,sid)+"-error",None)
    st.session_state.pop(target_key(pid,sid)+"-error-context",None)
    st.rerun(scope=refresh_scope or target_key(pid,sid))


def submit(manager,pid,sid,service,engine):
    pending = st.session_state.pop(target_key(pid,sid)+"-request",None)
    if not pending:
        return
    operation = pending["operation"]
    event = Event()
    service.stop_requested = engine.stop_requested = event.is_set
    options = engine.load().options
    if operation == "generate":
        callback = lambda:service.generate_visual(sid,options,force=pending["force"])
    elif operation == "save":
        callback = lambda:service.save_direction(sid,expected=pending["expected"],text=pending["text"])
    elif operation == "regenerate_direction":
        callback=lambda:service.regenerate_direction(sid,expected=pending["expected"])
    elif operation == "save_generate":
        def callback():
            service.save_direction(sid,expected=pending["expected"],text=pending["text"])
            if event.is_set():
                raise InterruptedError("Direction changes saved. Stopped before regenerating the scene.")
            return service.generate_visual(sid,options,force=True)
    elif operation == "apply":
        callback = lambda:service.apply_revision(sid,pending["asset_id"],options,expected=pending["expected"])
    elif operation == "modify":
        callback = lambda:service.modify_region(sid,options,expected=pending["expected"],rectangle=pending["rectangle"],
            prompt=pending["prompt"],expected_input_hash=pending["expected_input_hash"],
            start_time=pending.get("start_time"),end_time=pending.get("end_time"))
    elif operation == "extend":
        callback=lambda:service.extend_visual(sid,options,expected=pending["expected"],seconds=pending["seconds"],
            prompt=pending["prompt"],expected_input_hash=pending["expected_input_hash"])
    else:
        raise ValueError("Unknown animated-scene operation")
    try:
        labels = {"generate":"Generating animated scene ","modify":"Modifying animated scene ",
                  "apply":"Applying animated-scene revision ","save":"Saving scene direction ",
                  "save_generate":"Saving direction and regenerating animated scene ","extend":"Extending animated scene ",
                  "regenerate_direction":"Regenerating animation direction "}
        task = launch(callback,labels[operation]+sid,
            rerun_on_start=False,inline=True,queue_enabled=True,stop_event=event,project_path=engine.path,
            resource="animated-scene:"+sid,service="openai" if operation=="regenerate_direction" else "runway" if operation in ("modify","extend") else options.provider if operation in ("generate","save_generate") else None)
        if not task:
            raise RuntimeError("The task could not be queued. Wait for the current task and retry.")
        task.update(animated_scene_project=pid,asset_subject=sid,operation=operation,
            generation_button_project=pid,generation_button_key=target_key(pid,sid)+"-"+operation)
    except (OSError,ValueError,RuntimeError) as exc:
        st.session_state[target_key(pid,sid)+"-error"] = str(exc)


def signature(service,sid):
    from .file_cache import version
    stamps = []
    for name in ("animation.json","production.json","scenes.json","render.json"):
        try:
            stamps.append(version(service.path/name))
        except FileNotFoundError:
            stamps.append(None)
    return input_hash({"record":data(service.assets,"visual_scenes.json").get(sid),"inputs":stamps,
        "keyframes":data(service.assets,"scene_image_keyframes.json").get(sid),
        "jobs":[(t["id"],t.get("phase"),t["future"].done()) for t in jobs(service.project_id,sid)],
        "request":st.session_state.get(target_key(service.project_id,sid)+"-request"),
        "queueable":not busy() or can_queue_work()})


def render(manager,pid):
    summary(manager,pid)
    service = SceneProduction(manager,pid)
    for scene in sorted(service.assets.creative.artifact("storyboard").scenes,key=lambda scene:scene.order):
        st.fragment(key=target_key(pid,scene.scene_id))(card)(manager,pid,scene.scene_id)


def batch_signature(pid):
    return input_hash({"jobs":[(task["id"],task.get("phase"),task["future"].done()) for task in jobs(pid)],
        "pending":[key for key in st.session_state if key.startswith(f"animated-scene-card-{pid}-") and key.endswith("-request")]})


def batch_controls(manager,pid):
    st.fragment(key=f"animated-scene-batch-{pid}")(batch_panel)(manager,pid)


def batch_panel(manager,pid):
    service = SceneProduction(manager,pid)
    options = RenderPipeline(manager,pid).load().options
    records = service.visuals()
    scenes = sorted(service.assets.creative.artifact("storyboard").scenes,key=lambda scene:scene.order)
    eligible = [(scene.scene_id,service.visual_current(scene.scene_id,options)) for scene in scenes
        if not phase(pid,scene.scene_id,records.get(scene.scene_id,{}))
        and not st.session_state.get(target_key(pid,scene.scene_id)+"-dirty")
        and not st.session_state.get(target_key(pid,scene.scene_id)+"-direct-dirty")
        and service.direction_ready(scene.scene_id)]
    batch_key = f"animated-scene-batch-active-{pid}"
    active = any(not task["future"].done() for task in jobs(pid)) or any(
        st.session_state.get(target_key(pid,scene.scene_id)+"-request") for scene in scenes)
    if not active:
        st.session_state.pop(batch_key,None)
    def queue_all():
        already_busy = busy()
        for index,(sid,force) in enumerate(eligible):
            target = target_key(pid,sid)
            st.session_state[target+"-request"] = {"operation":"generate","force":force,
                "phase":"running" if index == 0 and not already_busy else "queued"}
            st.session_state.pop(target+"-error",None)
        st.session_state[batch_key] = True
        # Paint and dispatch each card in storyboard order, without rebuilding
        # the settings panel, other tabs or the surrounding page.
        st.rerun(scope=[f"animated-scene-batch-{pid}",*[target_key(pid,sid) for sid,_ in eligible]])
    generation_button("Generate all animated scenes",key=f"generate-all-animated-scenes-{pid}",
        activity="running" if active and st.session_state.get(batch_key) else None,
        disabled=not eligible or (busy() and not can_queue_work()),on_click=queue_all,
        help="Generate or regenerate all ready scenes in ascending storyboard order. Already running or queued scenes are skipped. Save unsaved direction edits first.")
    st.caption("Queues scenes in ascending storyboard order. Running/queued scenes and unsaved direction drafts are skipped.")
    watch_batch(manager,pid,batch_signature(pid))


@st.fragment(run_every=1)
def watch_batch(manager,pid,baseline):
    if polling_tick() and batch_signature(pid) != baseline:
        refresh_target(f"animated-scene-batch-{pid}")


@st.fragment(run_every=1)
def summary(manager,pid):
    active = [task for task in jobs(pid) if not task["future"].done()]
    queued = sum(task.get("phase") in ("queued","waiting") for task in active)
    from .file_cache import read_json
    path = manager.project_path(pid)/"visual_scenes.json"
    records = read_json(path) if path.exists() else {}
    generating = max(len(active)-queued,sum(record.get("status") == "running" for record in records.values()))
    st.caption(f"Animated scenes · Generating: {generating} · Queued: {queued}. Other scenes remain available for editing and queuing.")
    from .ui_tasks import stop_tasks
    can_stop = bool(active) or any(not task["future"].done() and
        str(task.get("error_report_path","")).replace("\\","/").rstrip("/").endswith("/"+pid)
        for task in workspace_tasks())
    st.button("Stop after running tasks",key=f"stop-animated-scenes-{pid}",on_click=stop_tasks,disabled=not can_stop)


def card(manager,pid,sid):
    if st.session_state.get("dialog-open-requested"):
        from .dialog_ui import show_pending_dialog
        show_pending_dialog()
    target = target_key(pid,sid)
    for task in list(jobs(pid,sid)):
        if task["future"].done():
            if task.get("operation") in ("save","regenerate_direction") and not task["future"].cancelled() and task["future"].exception() is None:
                st.session_state.pop(target+"-dirty",None)
            collect(task)
    if result := take_task_result(pid,[target+"-"+operation for operation in ("generate","save","save_generate","regenerate_direction","apply","modify","extend")]):
        if result[0][0] == "error":
            st.session_state[target+"-error"] = result[0][1]
            st.session_state[target+"-error-context"] = result[1]
    service = SceneProduction(manager,pid)
    engine = RenderPipeline(manager,pid)
    options = engine.load().options
    baseline = signature(service,sid)
    record = service.visuals().get(sid,{})
    activity = phase(pid,sid,record)
    own_jobs = jobs(pid,sid)
    saving = any(t.get("operation") in ("save","apply","regenerate_direction") and not t["future"].done() for t in own_jobs)
    pending = st.session_state.get(target+"-request",{})
    saving = saving or pending.get("operation") in ("save","apply","regenerate_direction")
    direction_generating=pending.get("operation")=="regenerate_direction" or any(
        t.get("operation")=="regenerate_direction" and not t["future"].done() for t in own_jobs)
    current = service.visual_current(sid,options)
    approved = service.visual_current(sid,options,approved=True)
    with st.container(border=True,key=target+"-box"):
        st.markdown("**"+sid+"**")
        image = service.assets.load().records.get("scene_images:"+sid)
        from .scene_keyframes_ui import saved_gallery
        gallery_shown=saved_gallery(service.assets,sid,target)
        if not gallery_shown and image and image.asset and service.assets.local_path(image.asset.file_path).is_file():
            image_preview(str(service.assets.local_path(image.asset.file_path)),width=320)
        status = "Queuing..." if activity == "queued" else "Generating direction..." if direction_generating else "Saving..." if saving else "Generating..." if activity else record.get("status","pending")
        if not activity and status=="succeeded" and not current:
            status="stale"
        st.caption(status+f" · revision {record.get('revision',0)}"+(" · Approved" if approved else " · Not approved"))
        from .scene_keyframe_video import saved
        image_count=len(saved(service,sid).get("frames",[]))
        if image_count>1:
            st.caption(f"Kling O3 Pro → Aleph 2.0 · {image_count} timed keyframes. Image guidance may not match every frame exactly; both passes incur usage.")
        if activity and record.get("generationStage") and record.get("status")=="running":
            st.caption(record["generationStage"])
        if (record.get("asset") or {}).get("duration") is not None:
            st.caption(f"Video duration: {record['asset']['duration']:g} seconds")
        if activity and not saving:
            loading_preview(activity)
        else:
            show_video(service,record,pid,sid)
        error = st.session_state.get(target+"-error") or record.get("error")
        if error and not activity:
            from .error_report_ui import render_error
            context = st.session_state.get(target+"-error-context",{})
            render_error(error,project_path=context.get("path") or service.path,
                task_key=context.get("task") or "animation:visual:"+sid,key=target+"-error-report")
        changed = st.session_state.get(target+"-dirty",False)
        from .animated_scene_direction_ui import render as direction_fields
        direct_changed=direction_fields(service,sid,target,activity)
        st.session_state[target+"-direct-dirty"]=direct_changed
        from .ui_panels import persistent_expander
        panel = persistent_expander("Direction and generation details",key=target+"-details",
            on_change=lambda:st.rerun(scope=target))
        if panel.open:
            with panel:
                try:
                    direction = service.direction(sid)
                    expected = service.direction_hash(sid)
                    source = direction.model_dump_json(by_alias=True,indent=2)
                    draft_key = target+"-direction-"+expected
                    text = st.text_area("Scene direction (editable)",value=source,key=draft_key,height=260,
                        disabled=bool(activity),on_change=lambda:st.rerun(scope=target))
                    changed = text != source
                    st.session_state[target+"-dirty"] = changed
                    st.button("Saving..." if saving else "Save direction",key=target+"-save-direction",
                        disabled=bool(activity) or not changed or (busy() and not can_queue_work()),
                        on_click=lambda expected=expected,draft_key=draft_key:request(pid,sid,"save",expected=expected,text=st.session_state[draft_key]))
                except (OSError,ValueError) as exc:
                    st.info(str(exc))
                if record:
                    st.json(record)
        changed=changed or direct_changed
        generation_button("Regenerate animated scene" if record.get("asset") else "Generate animated scene",
            key=f"scene-generate-visual-{pid}-{sid}",activity=activity if not saving else None,
            disabled=bool(activity) or changed or not service.direction_ready(sid) or (busy() and not can_queue_work()),
            on_click=lambda:request(pid,sid,"generate",force=current))
        if changed:
            st.caption("Apply direction edits before generating this scene.")
        from .animated_scene_revisions_ui import revision_controls
        revision_controls(service,sid,options,activity,changed)
        # Dispatch only after the active button and placeholder are on screen.
        submit(manager,pid,sid,service,engine)
    watch(manager,pid,sid,baseline)


def show_video(service,record,pid,sid,*,key_prefix=None):
    from .animation_formats import FORMAT_LABELS
    versions = record.get("formats") or {"video":record}
    if not any(version.get("asset") for version in versions.values()) and record.get("asset"):
        versions = {"video":record}
    for name,version in versions.items():
        if version.get("asset"):
            path = service.assets.local_path(version["asset"]["filePath"])
            if path.is_file():
                st.video(str(path))
                st.download_button("Download "+FORMAT_LABELS.get(name,"video"),lambda path=path:path.read_bytes(),
                    file_name=f"{sid}_{name}.mp4",mime="video/mp4",key=(key_prefix or f"visual-download-{pid}-{sid}")+"-"+name,on_click="ignore")


@st.fragment(run_every=1)
def watch(manager,pid,sid,baseline):
    dialog = st.session_state.get("scene-video-modify-target",{})
    extension=st.session_state.get("scene-video-extend-target",{})
    if any(target.get("project_id")==pid and target.get("sid")==sid for target in (dialog,extension)):
        return  # Keep a rectangle/prompt draft mounted while other jobs finish.
    if polling_tick():
        service = SceneProduction(manager,pid)
        try:
            changed = signature(service,sid) != baseline
        except (OSError,ValueError):
            return
        if changed:
            refresh_target(target_key(pid,sid))
