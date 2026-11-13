"""Horizontal scene-image gallery with generated drafts and editable keyframe times."""
from threading import Event
from uuid import uuid4
from datetime import datetime,timezone

import streamlit as st

from .dialog_ui import dialog,Deferred,request_open
from .files import input_hash
from .image_preview_ui import image_preview
from .fullscreen_image_ui import fullscreen_image
from . import scene_keyframes as store


def jobs(token):
    from .ui_tasks import workspace_tasks
    return [job for job in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if job.get("scene_keyframe_dialog")==token]


def dismiss():
    target=st.session_state.pop("scene-keyframes-target",None)
    if target:
        for job in jobs(target["token"]):
            if job.get("stop_event") is not None and not job["future"].done():
                job["stop_event"].set()


def close():
    dismiss()
    st.rerun(scope="app")


def controls(engine,sid,target,*,available):
    def open_dialog():
        st.session_state["dialog-context-manager"]=engine.manager
        st.session_state["scene-keyframes-target"]={"project_id":engine.project_id,"sid":sid,
            "demo":engine.demo,"token":uuid4().hex,"requests":[],"times":{},"errors":{}}
        request_open()
        st.rerun(scope=target)
    st.button("Multiple scene images",key=target+"-multiple-images",disabled=not available,on_click=open_dialog)


def saved_gallery(engine,sid,target):
    """Show confirmed multi-image sets directly on their scene card."""
    from .asset_view_ui import data
    saved=data(engine,"scene_image_keyframes.json").get(sid,{})
    images=sorted(saved.get("frames",[]),key=lambda frame:frame["timeSec"])
    if len(images)<=1:
        return False
    columns=st.columns(min(len(images),store.MAX_IMAGES),gap="small")
    for index,frame in enumerate(images):
        with columns[index % store.MAX_IMAGES]:
            st.caption(f"Image {index+1}")
            if store.frame_intact(engine,frame):
                path=engine.local_path(frame["asset"]["filePath"])
                image_preview(str(path),width=220)
                st.caption(f"Keyframe: {frame['timeSec']:g}s")
                st.download_button("Download image "+str(index+1),lambda path=path:path.read_bytes(),
                    file_name=path.name,mime="image/png",key=target+"-keyframe-download-"+frame["id"],on_click="ignore")
            else:
                st.warning("This keyframe image is missing or modified.")
                st.caption(f"Keyframe: {frame['timeSec']:g}s")
    return True


def rows(engine,target):
    return list(store.load(engine,target["token"]).get("frames",[]))


def preview(engine,target):
    identity=target.get("preview_id")
    if not identity:
        return None
    draft=store.load(engine,target["token"])
    value=next((dict(frame) for frame in draft.get("candidates",[]) if frame["id"]==identity),None)
    if value is None:
        request=next((item for item in target["requests"] if item["id"]==identity),None)
        if request is None:
            return None
        value={**request,"status":request.get("phase","generating")}
    tracked=next((job for job in jobs(target["token"]) if job.get("keyframe_id")==identity),None)
    if tracked and not tracked["future"].done():
        value["status"]="queued" if tracked.get("phase") in ("queued","waiting") else "generating"
    elif value["status"] in ("generating","queued"):
        request=next((item for item in target["requests"] if item["id"]==identity),{})
        if tracked or request.get("submitted"):
            error="This image task is no longer running or tracked. Check Error Report before regenerating."
            if tracked and not tracked["future"].cancelled() and tracked["future"].exception():
                error=str(tracked["future"].exception())
            value.update(status="failed",phase="failed",error=error)
    if identity in target["errors"]:
        value.update(status="failed",error=target["errors"][identity])
    return value


def progress_text(engine,target):
    value=preview(engine,target)
    if not value or value["status"] not in ("generating","queued"):
        return None
    job=next((job for job in jobs(target["token"]) if job.get("keyframe_id")==value["id"]),{})
    phase=job.get("phase")
    if phase=="queued" or value["status"]=="queued" and phase!="waiting":
        label="Queued - waiting for other tasks or an available service slot"
    elif phase=="waiting":
        label="Waiting for an OpenAI slot"
    elif phase=="saving" or value.get("phase")=="saving":
        label="Saving generated image"
    elif value.get("phase")=="requesting":
        label="Waiting for OpenAI image response"
    else:
        label="Preparing references and image request"
    request=next((item for item in target["requests"] if item["id"]==value["id"]),{})
    stamp=request.get("requestedAt") or value.get("startedAt")
    if stamp:
        try:
            elapsed=max(0,int((datetime.now(timezone.utc)-datetime.fromisoformat(stamp)).total_seconds()))
            label+=f" · {elapsed//60:02d}:{elapsed%60:02d} elapsed"
        except (TypeError,ValueError):
            pass
    return label


def signature(engine,target):
    from .file_cache import version
    try:
        stamp=version(store.draft_file(engine,target["token"]))
    except FileNotFoundError:
        stamp=None
    return input_hash({"file":stamp,"jobs":[(job["id"],job.get("phase"),job["future"].done()) for job in jobs(target["token"])],"errors":target["errors"]})


def next_time(times,duration):
    occupied=sorted({0.,duration,*times.values()})
    if duration not in times.values():
        return duration
    _,left,right=max((b-a,a,b) for a,b in zip(occupied,occupied[1:]))
    return (left+right)/2


@dialog("Multiple scene images",width="large",on_dismiss=dismiss)
def images_dialog(engine,target):
    key="scene-keyframes-"+target["token"]
    st.fragment(key=key)(body)(engine,target,key)


def body(engine,target,key):
    from .scene_image_cards_ui import loading_preview
    from .ui_tasks import busy,can_queue_work,launch,collect,take_task_result
    from .generation_ui import generation_button
    if not target.get("initialized"):
        try:
            with st.spinner("Loading scene images..."):
                store.start(engine,target["sid"],target["token"])
            target["initialized"]=True
        except (OSError,ValueError,RuntimeError) as exc:
            st.warning(str(exc))
            st.button("Close",key=key+"-close",on_click=close)
            return
    def refresh():
        st.rerun(scope=key)
    for job in list(jobs(target["token"])):
        if job["future"].done():
            if job["future"].cancelled():
                target["errors"][job["keyframe_id"]]="Generation was stopped."
            elif job["future"].exception():
                target["errors"][job["keyframe_id"]]=str(job["future"].exception())
            collect(job)
            take_task_result(engine.project_id,[job.get("generation_button_key") or job["label"]])
    baseline=signature(engine,target)
    draft=store.load(engine,target["token"])
    images=rows(engine,target)
    ready=[frame for frame in images if frame["status"]=="ready" and store.frame_intact(engine,frame)]
    for frame in images:
        if frame["id"] not in target["times"]:
            target["times"][frame["id"]]=frame.get("timeSec") if frame.get("timeSec") is not None else next_time(target["times"],draft["duration"])
    candidate=preview(engine,target)
    phase=("queued" if candidate["status"]=="queued" else "running") if candidate and candidate["status"] in ("generating","queued") else None
    active=bool(phase) or any(not job["future"].done() for job in jobs(target["token"]))
    active_reference=candidate.get("referenceId") if phase else None
    st.caption(f"{target['sid']} - {len(images)}/{store.MAX_IMAGES} images - Scene duration: {draft['duration']:g}s")
    st.html('<style>.st-key-'+key+'-list [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important;overflow-x:auto;}'
        '.st-key-'+key+'-list [data-testid="stColumn"]{min-width:115px;}'
        '.st-key-'+key+'-list [data-testid="stImage"] img{max-height:120px;object-fit:contain;}</style>')
    with st.container(key=key+"-list"):
        columns=st.columns(store.MAX_IMAGES,gap="small")
        for index,frame in enumerate(images):
            with columns[index % store.MAX_IMAGES],st.container(border=True):
                st.caption(f"Image {index+1}")
                with st.container(height=140,border=False):
                    if store.frame_intact(engine,frame):
                        fullscreen_image(engine.local_path(frame["asset"]["filePath"]),thumbnail_width=160,
                            key=key+"-fullscreen-"+frame["id"],title=f"{target['sid']} · Image {index+1}")
                    else:
                        st.warning("Image missing")
                def update_time(identity=frame["id"]):
                    target["times"][identity]=st.session_state[key+"-time-"+identity]
                    target.pop("save_error",None)
                    target.pop("save_success",None)
                    refresh()
                st.number_input("Time (s)",min_value=0.,max_value=float(draft["duration"]),
                    value=float(target["times"][frame["id"]]),step=.1,format="%.2f",key=key+"-time-"+frame["id"],on_change=update_time)
                def remove(identity=frame["id"]):
                    try:
                        store.remove_image(engine,target["token"],identity)
                    except (OSError,ValueError,RuntimeError) as exc:
                        target["save_error"]=str(exc)
                    else:
                        target["times"].pop(identity,None)
                        target.pop("save_error",None)
                        target.pop("save_success",None)
                        if st.session_state.get(key+"-reference")==identity:
                            st.session_state.pop(key+"-reference",None)
                    refresh()
                st.button("Remove",key=key+"-remove-"+frame["id"],on_click=remove,
                    disabled=len(images)<=1 or frame["id"]==active_reference,
                    help="Keep at least one image. A reference cannot be removed while it is generating a preview. Removal affects this image list, not the original asset.")
    with st.container(border=True):
        st.markdown("**Add scene image**")
        form_col,preview_col=st.columns([3,2])
        with form_col:
            text=st.text_area("Description",key=key+"-prompt",height=130,on_change=refresh,
                placeholder="Describe what should change in the selected reference image; other details will be preserved.")
            labels={frame["id"]:f"Image {images.index(frame)+1}" for frame in ready}
            reference=st.selectbox("Reference scene image",list(labels),format_func=labels.get,key=key+"-reference",on_change=refresh)
            full=len(images)>=store.MAX_IMAGES
            def request_preview():
                from .ui_tasks import busy
                identity=uuid4().hex
                target["preview_id"]=identity
                target["requests"].append({"id":identity,"referenceId":st.session_state[key+"-reference"],
                    "prompt":st.session_state[key+"-prompt"].strip(),"phase":"queued" if busy() else "generating",
                    "requestedAt":datetime.now(timezone.utc).isoformat()})
                target.pop("save_error",None)
                target.pop("save_success",None)
                refresh()
            generate_col,add_col=st.columns(2)
            generation_button("Regenerate image" if candidate else "Generate image",container=generate_col,key=key+"-generate-preview",
                activity=phase,disabled=active or full or not text.strip() or not reference or busy() and not can_queue_work(),on_click=request_preview)
            matches=bool(candidate and candidate["status"]=="ready" and store.frame_intact(engine,candidate)
                and candidate.get("prompt")==text.strip() and candidate.get("referenceId")==reference)
            def add():
                try:
                    frame=store.add_generated(engine,target["token"],target["preview_id"])
                except (OSError,ValueError,RuntimeError) as exc:
                    target["save_error"]=str(exc)
                else:
                    target["preview_id"]=None
                    target.pop("save_error",None)
                    target.pop("save_success",None)
                    st.session_state[key+"-prompt"]=""
                    st.session_state[key+"-reference"]=frame["id"]
                refresh()
            add_col.button("Add image",key=key+"-add",type="primary",disabled=not matches or active or full,on_click=add)
            if full:
                st.caption("Maximum 5 images. Remove one before adding another.")
            elif candidate and candidate["status"]=="ready" and not matches:
                st.caption("Regenerate the preview after changing its description or reference.")
        with preview_col:
            if phase:
                loading_preview(phase)
                watch(engine,target,key,baseline)
            elif candidate and candidate["status"]=="ready" and store.frame_intact(engine,candidate):
                fullscreen_image(engine.local_path(candidate["asset"]["filePath"]),thumbnail_width=260,
                    key=key+"-preview-fullscreen-"+candidate["id"],title=target["sid"]+" · Generated preview")
                st.caption("Generated preview. Press Add image to include it in the list.")
            elif candidate and candidate.get("error"):
                from .error_report_ui import render_error
                render_error(candidate["error"],project_path=engine.path,
                    task_key="assets:scene_keyframes:"+candidate["id"],key=key+"-error-"+candidate["id"])
            else:
                st.caption("Generate a preview before adding an image.")
    valid=False
    try:
        store.validate_times(images,{frame["id"]:target["times"][frame["id"]] for frame in images},draft["duration"])
        valid=bool(ready) and len(ready)==len(images)
    except ValueError as exc:
        st.warning(str(exc))
    if message:=target.get("save_error"):
        st.error(message)
    if target.get("save_success"):
        st.success("Scene images and keyframe times saved.")
    save_col,close_col=st.columns(2)
    def save():
        try:
            store.confirm(engine,target["token"],{frame["id"]:target["times"][frame["id"]] for frame in ready},keep_open=True)
        except (OSError,ValueError,RuntimeError) as exc:
            target["save_error"]=str(exc)
            refresh()
        else:
            target.pop("save_error",None)
            target["save_success"]=True
            refresh()
    save_col.button("Save",key=key+"-save",type="primary",disabled=not valid or active,on_click=save)
    close_col.button("Close",key=key+"-close",on_click=close)
    # Generate only a preview. Membership changes require the separate Add image action.
    for item in target["requests"]:
        if item.get("submitted"):
            continue
        item["submitted"]=True
        stop=Event()
        def generate(item=dict(item),stop=stop):
            from .assets import AssetPipeline
            worker=AssetPipeline(engine.manager,engine.project_id,demo=engine.demo)
            worker.scene_image_provider=engine.scene_image_provider
            worker.stop_requested=stop.is_set
            return store.generate(worker,target["token"],item["id"],item["referenceId"],item["prompt"],preview=True)
        job=launch(generate,"Generating additional scene image "+target["sid"],inline=True,rerun_on_start=False,
            queue_enabled=True,service="openai",project_path=engine.path,stop_event=stop,
            resource="scene-keyframes:"+target["sid"],dialog_owner=("scene-keyframes-target",target))
        if job:
            job.update(scene_keyframe_dialog=target["token"],keyframe_id=item["id"],
                generation_button_key=key+"-generate-"+item["id"],generation_button_project=engine.project_id)
        else:
            target["errors"][item["id"]]="Could not queue this preview. Generate it again when the service is available."


@st.fragment(run_every=1)
def watch(engine,target,key,baseline):
    from .scene_image_cards_ui import polling_tick
    if polling_tick():
        from .ui_tasks import dispatch_queued_tasks
        dispatch_queued_tasks()
        if signature(engine,target)!=baseline:
            from .asset_view_ui import refresh_target
            refresh_target(key)
    if engine is not None and target.get("preview_id"):
        message=progress_text(engine,target)
        if message:
            st.caption(message)


def show(manager,target):
    from .assets import AssetPipeline
    images_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target)
