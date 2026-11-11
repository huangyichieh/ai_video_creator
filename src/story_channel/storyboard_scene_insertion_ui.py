"""Open the insertion prompt before loading project data or starting AI work."""
from uuid import uuid4

import streamlit as st

from .dialog_ui import Deferred,dialog,request_open


def close():
    st.session_state.pop("storyboard-scene-insertion-target",None)


def open_insertion(pipeline,position,source):
    st.session_state["dialog-context-manager"] = pipeline.manager
    for key in ("active-stage-browser","modify-target","studio-modify-target",
                "storyboard-split-target","storyboard-scene-removal-target"):
        st.session_state.pop(key,None)
    st.session_state["storyboard-scene-insertion-target"] = {
        "project_id":pipeline.project_id,"position":position,"source":source,"token":uuid4().hex}
    request_open()


@dialog("Add scene in between",width="large",on_dismiss=close)
def insertion_dialog(pipeline,target):
    from .ui_tasks import find_task,collect,launch,take_task_result
    from .creative import Storyboard
    key = "storyboard-insert-"+target["token"]
    owner = ("storyboard-scene-insertion-target",target)
    task = find_task(lambda item:item.get("dialog_owner") == owner)
    if task and task["future"].done():
        collect(task)
        task = None
    if st.session_state.get(key+"-done"):
        close()
        st.rerun(scope="app")
    board = Storyboard.model_validate_json(target["source"])
    position = target["position"]
    if position == 0:
        st.caption("Insert before "+board.scenes[0].scene_id)
    elif position == len(board.scenes):
        st.caption("Insert after "+board.scenes[-1].scene_id)
    else:
        st.caption("Insert between "+board.scenes[position-1].scene_id+" and "+board.scenes[position].scene_id)
    prompt = st.text_area("New scene prompt",key=key+"-prompt",height=180,disabled=bool(task),
        placeholder="Describe the action, characters, background and camera treatment for this new scene.")
    st.caption("Existing scenes keep their content and duration. Scene IDs will be renumbered, and the total duration increases. Saving reopens storyboard review and invalidates downstream prompts and animations.")

    def confirm():
        from .storyboard_scene_insertion import insert
        instructions = prompt
        st.session_state.pop(key+"-error",None)
        launch(lambda:insert(pipeline,position,target["source"],instructions),"Adding storyboard scene",
            dialog_owner=owner,project_path=pipeline.path,service="openai",
            success=lambda result:st.session_state.__setitem__(key+"-done",True))

    def cancel():
        close()
        st.rerun(scope="app")

    with st.container(horizontal=True):
        st.button("Generating…" if task else "Confirm",key=key+"-confirm",on_click=confirm,
            disabled=bool(task) or not prompt.strip())
        st.button("Cancel",key=key+"-cancel",on_click=cancel,disabled=bool(task))
    if response := take_task_result(pipeline.project_id,[key+"-confirm","Adding storyboard scene"]):
        if response[0][0] == "error":
            st.session_state[key+"-error"] = response[0][1]
    if error := st.session_state.get(key+"-error"):
        from .error_report_ui import render_error
        render_error(error,project_path=pipeline.path,task_key=f"storyboard_insert:{position}",key=key+"-report")
    if task:
        watch(task)


@st.fragment(run_every=1)
def watch(task):
    if task["future"].done():
        st.rerun(scope="app")


def show(manager,target):
    from .pipeline import Pipeline
    insertion_dialog(Deferred(lambda:Pipeline(manager,target["project_id"])),target)
