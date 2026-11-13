"""Confirm removal before changing the saved episode storyboard."""
from uuid import uuid4

import streamlit as st

from .dialog_ui import Deferred,dialog,request_open


def close():
    st.session_state.pop("storyboard-scene-removal-target",None)


def open_removal(pipeline,scene_id,source):
    st.session_state["dialog-context-manager"] = pipeline.manager
    for key in ("active-stage-browser","modify-target","studio-modify-target","storyboard-split-target","storyboard-scene-insertion-target"):
        st.session_state.pop(key,None)
    st.session_state["storyboard-scene-removal-target"] = {
        "project_id":pipeline.project_id,"scene_id":scene_id,"source":source,"token":uuid4().hex}
    request_open()


@dialog("Remove storyboard scene",on_dismiss=close)
def removal_dialog(pipeline,target):
    from .creative import Storyboard
    from .ui_tasks import busy
    key = "remove-storyboard-scene-"+target["token"]
    board = Storyboard.model_validate_json(target["source"])
    scene = next(item for item in board.scenes if item.scene_id == target["scene_id"])
    st.markdown("**Remove "+scene.scene_id+"?**")
    st.write(scene.action or scene.visual_description)
    st.caption(f"{scene.shot_type} · {scene.duration_sec:g}s")
    st.warning("This removes the scene, including its dialogue and narration, from this storyboard. Remaining scenes will be renumbered and the total duration will decrease. Storyboard review, prompts and downstream animations need updating. Shared Assets and the Script are kept.")
    if error := st.session_state.get(key+"-error"):
        st.error(error)

    def confirm():
        from .storyboard_scene_removal import remove
        try:
            with st.spinner("Removing scene…"):
                remove(pipeline,target["scene_id"],target["source"])
        except (OSError,ValueError,RuntimeError) as exc:
            st.session_state[key+"-error"] = str(exc)
        else:
            close()
            st.rerun(scope="app")

    def cancel():
        close()
        st.rerun(scope="app")

    with st.container(horizontal=True):
        st.button("Remove scene",key=key+"-confirm",type="primary",disabled=busy(),on_click=confirm)
        st.button("Cancel",key=key+"-cancel",on_click=cancel)


def show(manager,target):
    from .pipeline import Pipeline
    removal_dialog(Deferred(lambda:Pipeline(manager,target["project_id"])),target)
