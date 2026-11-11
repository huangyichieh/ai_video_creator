"""Lazy scene-video history and a deferred region-edit dialog."""
import streamlit as st

from .animation_formats import delivery_formats
from .files import input_hash
from .scene_visual_revisions import intact
from .ui_panels import persistent_expander
from .ui_tasks import busy,can_queue_work
from .revision_labels import revision_label


def revision_controls(service,sid,options,activity,changed):
    from .animated_scene_cards_ui import request,target_key,show_video
    target = target_key(service.project_id,sid)
    current = service.visuals().get(sid,{})
    has_video = bool(current.get("asset"))
    blocked = bool(activity) or changed or (busy() and not can_queue_work())
    from .scene_video_modify_ui import open_modify
    st.button("Modify animated scene (Runway / Aleph 2.0)",key=target+"-modify-region",
        disabled=not has_video or blocked,on_click=open_modify,args=(service,sid,options,target))
    from .scene_video_extension_ui import open_extend
    st.button("Extend animated scene",key=target+"-extend",disabled=not has_video or blocked,
        on_click=open_extend,args=(service,sid,options,target))
    panel = persistent_expander("View / apply saved video revisions",key=target+"-revisions",
        on_change=lambda:st.rerun(scope=target))
    if not panel.open:
        return
    with panel:
        history = service.revisions(sid)
        if not history:
            st.caption("No saved video revisions yet.")
            return
        choices = {row["asset"]["assetId"]:row for row in history}
        choice_key = target+"-revision-choice"
        if st.session_state.get(choice_key) not in choices:
            st.session_state.pop(choice_key,None)
        active = current.get("asset",{}).get("assetId")
        selected = st.selectbox("Preview video revision",list(choices),
            index=list(choices).index(active) if active in choices else 0,key=choice_key,
            format_func=lambda identity:revision_label(choices[identity])+(" · Active" if identity == active else ""),
            on_change=lambda:st.rerun(scope=target))
        record = choices[selected]
        if intact(service,record):
            show_video(service,record,service.project_id,sid,key_prefix=target+"-preview-"+selected)
        else:
            st.warning("This revision's video is missing or modified.")
        compatible = intact(service,record,delivery_formats(options))
        if not compatible:
            st.caption("Apply requires intact files for the currently selected delivery formats.")
        already = selected == active and service.visual_current(sid,options)
        st.caption("Preview leaves the active clip unchanged. Apply selects all saved delivery formats without an API request; intact selected videos are automatically approved.")
        st.button("Applied revision" if already else "Apply video revision",key=target+"-apply-revision",
            disabled=blocked or already or not compatible,
            on_click=lambda:request(service.project_id,sid,"apply",asset_id=selected,expected=input_hash(current)))
        with st.expander("Revision generation details"):
            st.caption(str(record["asset"].get("provider"))+" / "+str(record["asset"].get("model")))
            st.json(record)
