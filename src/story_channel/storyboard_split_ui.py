"""Deferred split dialog with one prompt per resulting scene."""
from uuid import uuid4

import streamlit as st

from .dialog_ui import Deferred,dialog,request_open


def close():
    st.session_state.pop("storyboard-split-target",None)


def open_split(pipeline,scene_id,source):
    # Called before the page render by the Split button callback. Let the normal
    # widget rerun open the shell through the central dispatcher.
    st.session_state["dialog-context-manager"] = pipeline.manager
    for key in ("active-stage-browser","modify-target","studio-modify-target","storyboard-scene-removal-target","storyboard-scene-insertion-target"):
        st.session_state.pop(key,None)
    st.session_state["storyboard-split-target"] = {"project_id":pipeline.project_id,"scene_id":scene_id,
        "source":source,"token":uuid4().hex}
    request_open()


@dialog("Split storyboard scene",width="large",on_dismiss=close)
def split_dialog(pipeline,target):
    from .ui_tasks import find_task,collect,launch,take_task_result
    key = "storyboard-split-"+target["token"]
    owner = ("storyboard-split-target",target)
    task = find_task(lambda item:item.get("dialog_owner") == owner)
    if task and task["future"].done():
        collect(task)
        task = None
    if st.session_state.get(key+"-done"):
        close()
        st.rerun(scope="app")
    st.markdown("**Split "+target["scene_id"]+"**")
    st.caption("Choose the number of resulting scenes and describe each in order. Total duration, dialogue and narration are preserved. All scene IDs will be renumbered consecutively.")
    st.warning("Saving reopens storyboard review. Downstream prompts and animations need updating.")
    count = st.number_input("Number of resulting scenes",min_value=2,max_value=20,value=2,step=1,
        key=key+"-count",disabled=bool(task))
    prompts = [st.text_area(f"Scene {index+1} prompt",key=key+f"-prompt-{index}",disabled=bool(task),
        placeholder="Describe this part's action, framing and camera treatment.") for index in range(count)]

    def confirm():
        from .storyboard_split import split
        instructions = list(prompts)
        st.session_state.pop(key+"-error",None)
        launch(lambda:split(pipeline,target["scene_id"],target["source"],instructions),
            "Splitting storyboard scene",dialog_owner=owner,project_path=pipeline.path,service="openai",
            success=lambda result:st.session_state.__setitem__(key+"-done",True))

    def cancel():
        close()
        st.rerun(scope="app")

    with st.container(horizontal=True):
        st.button("Splitting…" if task else "Split and save",key=key+"-confirm",on_click=confirm,
            disabled=bool(task) or not all(prompt.strip() for prompt in prompts))
        st.button("Cancel",key=key+"-cancel",on_click=cancel,disabled=bool(task))
    if response := take_task_result(pipeline.project_id,[key+"-confirm","Splitting storyboard scene"]):
        if response[0][0] == "error":
            st.session_state[key+"-error"] = response[0][1]
    if error := st.session_state.get(key+"-error"):
        from .error_report_ui import render_error
        render_error(error,project_path=pipeline.path,task_key="storyboard_split:"+target["scene_id"],key=key+"-report")
    if task:
        watch(task)


@st.fragment(run_every=1)
def watch(task):
    if task["future"].done():
        st.rerun(scope="app")


def show(manager,target):
    from .pipeline import Pipeline
    split_dialog(Deferred(lambda:Pipeline(manager,target["project_id"])),target)
