"""Per-shot direction drafts with explicit save and save/regenerate actions."""
import streamlit as st


def render(service,sid,target,activity):
    from .animated_scene_cards_ui import request,jobs
    from .generation_ui import generation_button
    from .ui_tasks import busy,can_queue_work
    try:
        direction=service.direction(sid)
        expected=service.direction_hash(sid)
    except (OSError,ValueError) as exc:
        st.info(str(exc))
        return False
    st.markdown("**Animation direction**")
    st.caption("Edit action, camera or poses, then save changes or save and regenerate this scene.")
    disabled=bool(activity) or st.session_state.get(target+"-dirty",False) or (busy() and not can_queue_work())
    changed=False
    draft_fields=[]
    for index,shot in enumerate(direction.shots):
        if len(direction.shots)>1:
            st.markdown("**"+shot.shot_id+"**")
        columns=st.columns(2)
        fields=(("action","Action / animation direction"),("camera","Camera direction"),
                ("start_pose","Start pose"),("end_pose","End pose"))
        for field_index,(field,label) in enumerate(fields):
            key=f"{target}-direct-{expected}-{shot.shot_id}-{field}"
            source=getattr(shot,field)
            draft_fields.append((index,field,key))
            columns[field_index%2].text_area(label,value=source,key=key,height=90,disabled=disabled,
                on_change=lambda:st.rerun(scope=target))
            changed=changed or st.session_state.get(key,source)!=source
    def save(regenerate=False):
        edited=direction.model_copy(deep=True)
        for index,field,key in draft_fields:
            setattr(edited.shots[index],field,st.session_state[key])
        request(service.project_id,sid,"save_generate" if regenerate else "save",expected=expected,
            text=edited.model_dump_json(by_alias=True))
    pending=st.session_state.get(target+"-request",{})
    operation=pending.get("operation") or next((job.get("operation") for job in jobs(service.project_id,sid)
        if not job["future"].done()),None)
    save_col,generate_col=st.columns(2)
    save_col.button("Saving..." if activity and operation=="save" else "Save changes",key=target+"-save-changes",
        disabled=disabled or not changed,on_click=save)
    generation_button("Save changes & regenerate",container=generate_col,key=target+"-save_generate",
        activity=activity if operation=="save_generate" else None,
        disabled=disabled or not changed or not service.direction_ready(sid),on_click=save,args=(True,))
    generation_button("Regenerate animation direction",key=target+"-regenerate_direction",
        activity=activity if operation=="regenerate_direction" else None,
        disabled=disabled or changed or not service.direction_ready(sid),
        on_click=lambda:request(service.project_id,sid,"regenerate_direction",expected=expected),
        help="Regenerate and apply only this scene's direction. Save your manual edits first. The scene video is regenerated separately.")
    if changed and not activity:
        st.caption("Unsaved direction changes.")
    return changed
