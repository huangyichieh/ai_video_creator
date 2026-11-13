"""Choose prop modes to remove from the selected episode, before invoking AI."""
from uuid import uuid4
import streamlit as st
from .dialog_ui import dialog,Deferred,request_open


def close():
    st.session_state.pop("storyboard-prop-removal-target",None)


def control(manager,project_id):
    def open_dialog():
        st.session_state["dialog-context-manager"] = manager
        st.session_state["storyboard-prop-removal-target"] = {"project_id":project_id,"token":uuid4().hex}
        request_open()
    from .ui_tasks import busy
    st.button("Delete props from scene",key="check-props-remove-"+project_id,disabled=busy(),on_click=open_dialog)


@dialog("Delete props from scene",width="large",on_dismiss=close)
def removal_dialog(pipeline,target):
    from .storyboard_asset_check import check
    from .ui_tasks import find_task,collect,launch,take_task_result
    key = "prop-removal-"+target["token"]
    owner = ("storyboard-prop-removal-target",target)
    task = find_task(lambda item:item.get("dialog_owner") == owner)
    if task and task["future"].done():
        collect(task)
        task = None
    if st.session_state.get(key+"-done"):
        close()
        st.rerun(scope="app")
    if key+"-source" not in st.session_state:
        st.session_state[key+"-source"] = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
        st.session_state[key+"-rows"] = [row for row in check(pipeline.manager,pipeline.project_id) if row["Category"] == "Props"]
    st.caption("Remove selected modes from all scenes that use this prop in this episode. If all modes are selected, the prop dependency is removed entirely. Reusable Assets are kept.")
    st.caption("AI adjusts storyboard staging while preserving dialogue and story beats. Existing storyboard approval is kept; downstream prompts and animation outputs need updating after saving.")
    choices = []
    for index,row in enumerate(st.session_state[key+"-rows"]):
        if st.checkbox(row["Asset"]+" · "+row["ID"]+" · Scenes: "+row["Scenes"],key=key+f"-item-{index}",disabled=bool(task)):
            choices.append(row["ID"])
    def confirm():
        from .storyboard_prop_removal import remove
        source = st.session_state[key+"-source"]
        selected = list(choices)
        st.session_state.pop(key+"-error",None)
        launch(lambda:remove(pipeline,selected,source),
            "Removing storyboard props",dialog_owner=owner,project_path=pipeline.path,service="openai",
            success=lambda result:st.session_state.__setitem__(key+"-done",True))
    with st.container(horizontal=True):
        st.button("Removing…" if task else "Confirm",key=key+"-confirm",disabled=bool(task) or not choices,on_click=confirm)
        def cancel():
            close()
            st.rerun(scope="app")
        st.button("Cancel",key=key+"-cancel",disabled=bool(task),on_click=cancel)
    if response := take_task_result(pipeline.project_id,[key+"-confirm","Removing storyboard props"]):
        if response[0][0] == "error":
            st.session_state[key+"-error"] = response[0][1]
    if error := st.session_state.get(key+"-error"):
        from .error_report_ui import render_error
        render_error(error,project_path=pipeline.path,task_key="storyboard:remove_props",key=key+"-report")
    if task:
        watch(task)


@st.fragment(run_every=1)
def watch(task):
    if task["future"].done():
        st.rerun(scope="app")
    st.caption("Removing props and adapting storyboard text…")


def show(manager,target):
    from .pipeline import Pipeline
    removal_dialog(Deferred(lambda:Pipeline(manager,target["project_id"])),target)
