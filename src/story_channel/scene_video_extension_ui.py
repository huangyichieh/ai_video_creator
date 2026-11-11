"""Collect a continuation duration and prompt before returning to scene progress."""
from uuid import uuid4
import streamlit as st

from .dialog_ui import dialog,Deferred,request_open
from .files import input_hash


def dismiss():
    st.session_state.pop("scene-video-extend-target",None)


def close():
    dismiss()
    st.rerun(scope="app")


def open_extend(service,sid,options,card_target):
    st.session_state["dialog-context-manager"]=service.manager
    st.session_state["scene-video-extend-target"]={"project_id":service.project_id,"sid":sid,"token":uuid4().hex}
    request_open()
    st.rerun(scope=card_target)


@dialog("Extend animated scene",width="large",on_dismiss=dismiss)
def extend_dialog(service,target):
    from .rendering import RenderPipeline
    from .scene_visual_revisions import intact
    from .animation_formats import delivery_formats
    from .animated_scene_cards_ui import request,phase
    from .ui_tasks import busy,can_queue_work
    from .scene_video_region import ffmpeg
    sid=target["sid"]
    record=service.visuals().get(sid,{})
    options=RenderPipeline(service.manager,service.project_id).load().options
    usable=False
    try:
        inputs=service.visual_hash(sid,options)
        target.setdefault("expected",input_hash(record))
        target.setdefault("expected_inputs",inputs)
        usable=(input_hash(record)==target["expected"] and inputs==target["expected_inputs"]
            and intact(service,record,delivery_formats(options)))
        if usable and "duration" not in target:
            with st.spinner("Loading scene duration..."):
                target["duration"]=ffmpeg(service).validate(service.assets.local_path(record["asset"]["filePath"]))
    except (OSError,ValueError,RuntimeError) as exc:
        st.warning(str(exc))
    if not usable:
        st.warning("The scene video, direction or settings changed. Reopen Extend on an intact current revision.")
    seconds=st.number_input("Extension duration (seconds)",min_value=.1,max_value=15.,value=1.,step=.1,
        key="extend-seconds-"+target["token"])
    prompt_key="extend-prompt-"+target["token"]
    prompt=st.text_area("Extension prompt",key=prompt_key,height=140,
        placeholder="Describe what happens next, continuing from the current clip's final frame.")
    if usable:
        duration=target["duration"]
        st.caption(f"{duration:g}s original + {seconds:g}s extension = {duration+seconds:g}s full video.")
    st.caption("Runway / Kling O3 Pro generates a continuation from the last frame. The original stays at its original speed, followed by the requested extension. Successful current revisions are automatically approved.")
    st.caption("Kling generates at least 3 seconds, rounded up to whole seconds. Only the requested extension is appended; usage records include the full generated duration. Continuity depends on the generated result. Final audio assembly still fits the clip to the episode's dialogue timing.")
    blocked=bool(phase(service.project_id,sid,record)) or (busy() and not can_queue_work())
    def confirm():
        dismiss()
        request(service.project_id,sid,"extend",refresh_scope="app",expected=target["expected"],
            expected_input_hash=target["expected_inputs"],seconds=st.session_state["extend-seconds-"+target["token"]],
            prompt=st.session_state[prompt_key])
    confirm_col,cancel_col=st.columns(2)
    confirm_col.button("Confirm extension",key="confirm-scene-extension",type="primary",
        disabled=not usable or not prompt.strip() or blocked,on_click=confirm)
    cancel_col.button("Cancel",key="cancel-scene-extension",on_click=close)


def show(manager,target):
    from .scene_production import SceneProduction
    extend_dialog(Deferred(lambda:SceneProduction(manager,target["project_id"])),target)
