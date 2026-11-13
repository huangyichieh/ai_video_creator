"""Stable scene cards: update only the scene whose visible content changed."""
from .image_preview_ui import image_preview
import streamlit as st
from .files import input_hash


def request_key(engine,sid):
    return f"scene-image-start-{engine.project_id}-{sid}"


def request_generation(engine,sid,force,target):
    """Paint the clicked card before collecting/dispatching background work."""
    from .ui_tasks import busy
    st.session_state[request_key(engine,sid)] = {"force":force,"phase":"queued" if busy() else "running"}
    st.session_state.pop(target+"-task-error",None)
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from streamlit.errors import StreamlitAPIException
    context = get_script_run_ctx()
    if context:
        try:
            context.fragment_storage.resolve_target(target)
        except StreamlitAPIException:
            return  # Standalone editor harnesses use their normal widget rerun.
        st.rerun(scope=target)


def submit_request(engine,sid,target,record):
    request = st.session_state.pop(request_key(engine,sid),None)
    if not request:
        return
    from .ui_tasks import busy,can_queue_work,find_task,launch_assets
    try:
        if busy() and not can_queue_work():
            running = find_task(lambda item:item.get("asset_engine") is not None
                and item["asset_engine"].path == engine.path and not item["future"].done())
            if running and running["asset_engine"].enqueue("scene_images",sid,force=request["force"]):
                return
            raise RuntimeError("This scene could not be queued. Wait for the current task to finish and retry.")
        task = launch_assets(engine,lambda:engine.generate("scene_images",subject=sid,force=request["force"]),
            "Generating "+sid,rerun_on_start=False,service="openai")
        if not task:
            raise RuntimeError("The scene-image task could not start. Wait for the current task to finish and retry.")
        task.update(scene_image_project=engine.project_id,asset_subject=sid,
            scene_image_baselines={sid:(record.revision,record.attempts) if record else (0,0)},
            generation_button_key="retry-asset-scene_images:"+sid,generation_button_project=engine.project_id)
    except (OSError,ValueError,RuntimeError) as exc:
        st.session_state[target+"-task-error"] = str(exc)


def scene_phase(engine,sid,record=None):
    """Use UI-owned jobs immediately, before provider preflight writes a record."""
    from .ui_tasks import workspace_tasks
    if pending := st.session_state.get(request_key(engine,sid)):
        return pending["phase"]
    for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[])):
        worker = task.get("asset_engine")
        if not worker or worker.path != engine.path or task["future"].done():
            continue
        owned = (task.get("scene_image_project") == engine.project_id
            and (task.get("asset_subject") == sid or sid in task.get("scene_image_subjects",[]))
            or task.get("generation_button_key") == "retry-asset-scene_images:"+sid)
        activity = worker.task_activity("scene_images",sid)
        if activity in ("queued","waiting","running"):
            return "queued" if activity in ("queued","waiting") else "running"
        if activity == "finished":
            initial = task.get("scene_image_baselines",{}).get(sid)
            current = (record.revision,record.attempts) if record else (0,0)
            if not owned or initial is None or tuple(initial) != current:
                continue
            # A reused engine can still report the previous attempt as finished
            # until the new job completes preflight. Its baseline is unchanged.
        if owned:
            return "queued" if task.get("phase") in ("queued","waiting") else "running"
    return "running" if record and record.status == "running" else None


def loading_preview(phase):
    label = "Queuing..." if phase == "queued" else "Generating..."
    st.html('''<style>
    .scene-image-loading {width:320px;max-width:100%;min-height:180px;display:flex;
        flex-direction:column;align-items:center;justify-content:center;gap:8px;}
    .scene-image-loading-icon {width:28px;height:28px;border:3px solid rgba(128,128,128,.3);
        border-top-color:#ff4b4b;border-radius:50%;animation:scene-image-spin .8s linear infinite;}
    @keyframes scene-image-spin {to {transform:rotate(360deg)}}
    </style><div class="scene-image-loading" role="status" aria-live="polite">
    <span class="scene-image-loading-icon" aria-hidden="true"></span><span>'''+label+'''</span></div>''')


def signature(engine,sid):
    from .asset_view_ui import cached,data,media_versions
    from .file_cache import version
    try:
        production_stamp = version(engine.state_path)
    except FileNotFoundError:
        production_stamp = None
    # Every scene watches the same production file. Reuse one read-only snapshot
    # per disk version instead of deep-copying the whole project on every timer.
    state = cached(engine,"scene-image-visible-state",production_stamp,engine.load)
    key = "scene_images:"+sid
    record = state.records.get(key)
    stamp = None
    if record and record.asset:
        try:
            stamp = version(engine.local_path(record.asset.file_path))
        except (OSError,ValueError):
            pass
    from .ui_tasks import workspace_tasks,asset_activity,busy,can_queue_asset
    manifest_path = engine.path/"manifest.json"
    manifest = cached(engine,"scene-control-manifest",version(manifest_path),lambda:engine.manager.load(engine.project_id))
    readiness = {
        "steps":{name:(step.status,step.output_hash) for name,step in manifest.steps.items()
            if name in ("world","characters","story","review","script","storyboard","prompts")},
        "approvals":{name:approval.content_hash for name,approval in manifest.approvals.items()},
        "content":{name:approval.content_hash for name,approval in manifest.content_approvals.items()},
        "busy":busy(),"queueable":can_queue_asset(engine,"scene_images",sid),
        "external_busy":bool(st.session_state.get("external-project-busy")),
    }
    jobs = [(task.get("id"),task.get("phase"),task["future"].done())
        for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("scene_image_project") == engine.project_id
        and (task.get("asset_subject") == sid or sid in task.get("scene_image_subjects",[]))]
    keyframes=data(engine,"scene_image_keyframes.json").get(sid,{})
    return input_hash({"status":record.status if record else None,"error":record.error if record else None,
        "keyframes":keyframes,"keyframeFiles":media_versions(engine,keyframes),
        "revision":record.revision if record else 0,"attempts":record.attempts if record else 0,
        "asset":record.asset.asset_id if record and record.asset else None,"file":stamp,
        "stale":record.stale_reason if record else None,"style":state.style_prompts.get(key),
        "modification":state.asset_modifications[key].model_dump(mode="json") if key in state.asset_modifications else None,
        "jobs":jobs,"activity":asset_activity(engine,"scene_images",sid),
        "requested":st.session_state.get(request_key(engine,sid)),"controls":readiness})


def render(engine):
    for scene in engine.creative.artifact("storyboard").scenes:
        target = f"scene-image-card-{engine.project_id}-{scene.scene_id}"
        st.fragment(key=target)(card)(engine,scene.scene_id,target)


def card(engine,sid,target):
    if st.session_state.get("dialog-open-requested"):
        from .dialog_ui import show_pending_dialog
        show_pending_dialog()
    from .character_assets_ui import draft_changed,generation_controls,character_details,revision_browser
    from .asset_sources import read_source
    from .ui_tasks import busy
    from .ui_tasks import workspace_tasks,collect,take_task_result
    for task in list(workspace_tasks()):
        if (task.get("scene_image_project") == engine.project_id and task.get("asset_subject") == sid
                and task["future"].done()):
            collect(task)
            result = take_task_result(engine.project_id,[task.get("generation_button_key") or task["label"]])
            if result and result[0][0] == "error":
                st.session_state[target+"-task-error"] = result[0][1]
            elif result:
                st.session_state.pop(target+"-task-error",None)
    baseline = signature(engine,sid)
    state = engine.load()
    record = state.records.get("scene_images:"+sid)
    phase = scene_phase(engine,sid,record)
    source = read_source(engine.creative,"scene_images",sid)
    with st.container(border=True,key=target+"-box"):
        status = "queuing" if phase == "queued" else "generating" if phase else record.status if record else "pending"
        st.markdown(f"**{sid} · {status} · revision {record.revision if record else 0}**")
        st.caption(source.scene.visual_description)
        st.caption(f"{source.scene.shot_type} · {source.scene.duration_sec:g}s")
        st.caption(source.scene.action)
        from .scene_keyframes_ui import controls as keyframe_controls
        keyframe_controls(engine,sid,target,available=bool(record and record.asset and engine._intact(record)) and not phase)
        if phase:
            loading_preview(phase)
        from .scene_keyframes_ui import saved_gallery
        gallery_shown=not phase and saved_gallery(engine,sid,target)
        has_asset = False
        if record and record.asset:
            try:
                path = engine.local_path(record.asset.file_path)
                if path.is_file():
                    has_asset = True
                    if not phase and not gallery_shown:
                        image_preview(str(path),width="stretch")
                        st.download_button("Download",lambda path=path:path.read_bytes(),file_name=path.name,
                            key="download-asset-scene_images:"+sid,on_click="ignore")
                else:
                    st.warning("The scene image is missing.")
            except (OSError,ValueError) as exc:
                st.warning(str(exc))
        if record and record.error and not phase:
            from .error_report_ui import render_error
            if "Too many scene assets for a reliable Runway identity prompt" in record.error:
                from .scene_image_references import SCENE_IMAGE_MODEL
                st.info("This saved error is from an earlier Runway attempt. Retry now uses OpenAI / "+SCENE_IMAGE_MODEL+
                    "; the Runway prompt limit does not apply. All asset references are kept.")
                with st.expander("Previous Runway error report",expanded=False):
                    render_error(record.error,project_path=engine.path,task_key="assets:scene_images:"+sid,key=target+"-error")
            else:
                render_error(record.error,project_path=engine.path,task_key="assets:scene_images:"+sid,key=target+"-error")
        if record and record.status == "stale" and not phase:
            st.caption(record.stale_reason or "Scene inputs changed; regenerate this image when ready.")
        def apply_saved_revision(callback,label):
            from .ui_tasks import launch
            task = launch(callback,label,inline=True,rerun_on_start=False,project_path=engine.path)
            if task:
                task.update(scene_image_project=engine.project_id,asset_subject=sid,
                    generation_button_key=target+"-apply-revision",generation_button_project=engine.project_id)
        revision_browser(engine,"scene_images",sid,allow_delete=False,launch_callback=apply_saved_revision,
            apply_hint="Preview leaves the active scene image unchanged. Apply selects the saved image for this scene's current storyboard. Dependent direction/video reviews must match the selected image. No generation API is called.")
        from .ui_panels import persistent_expander
        storyboard_token = input_hash(source.scene.model_dump(mode="json",by_alias=True))
        if st.session_state.get(target+"-storyboard-source") != storyboard_token:
            st.session_state[target+"-storyboard-source"] = storyboard_token
            st.session_state[target+"-storyboard-dirty"] = False
        panel = persistent_expander("Storyboard",key=target+"-storyboard-panel",expanded=False)
        if panel.open:
            with panel:
                from .storyboard_editor_ui import render as edit_storyboard
                st.session_state[target+"-storyboard-dirty"] = edit_storyboard(engine.creative,
                    generated_details_only=True,scene_id=sid,refresh_target_key=target,auto_save=True)
                # Autosave may have updated canonical data during this render.
                source = read_source(engine.creative,"scene_images",sid)
                state = engine.load()
        changed = draft_changed(engine,"scene_images",sid,source) or st.session_state.get(target+"-storyboard-dirty",False)
        if changed and st.session_state.pop(request_key(engine,sid),None):
            st.session_state[target+"-task-error"] = "Save or revert this scene's storyboard/source edits before generating."
        blocker = engine.generation_blocker("scene_images",state) if not phase and not busy() else None
        generation_controls(engine,state,"scene_images",sid,has_asset,changed,
            available=not phase and not busy() and blocker is None,stable_target=target,unavailable_reason=blocker)
        character_details(engine,source,record,stage="scene_images",subject=sid)
        # All visible feedback has been emitted before launch/queue setup.
        submit_request(engine,sid,target,record)
        if error := st.session_state.get(target+"-task-error"):
            if not record or error != record.error:
                from .error_report_ui import render_error
                render_error(error,project_path=engine.path,task_key="assets:scene_images:"+sid,key=target+"-task-error-report")
    watch(engine,sid,target,baseline)


@st.fragment(run_every=1)
def watch(engine,sid,target,baseline):
    dialog=st.session_state.get("scene-keyframes-target",{})
    if dialog.get("project_id")==engine.project_id and dialog.get("sid")==sid:
        return
    if not polling_tick():
        return  # Register the timer without refreshing a card still rendering.
    try:
        changed = signature(engine,sid) != baseline
    except (OSError,ValueError):
        return  # Keep the displayed card during a transient metadata replacement.
    if changed:
        from .asset_view_ui import refresh_target
        refresh_target(target)


def polling_tick():
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    from streamlit.runtime.scriptrunner_utils.script_run_context import ThreadState
    context = get_script_run_ctx()
    return bool(context and ThreadState.get().fragment_id in (context.fragment_ids_this_run or []))
