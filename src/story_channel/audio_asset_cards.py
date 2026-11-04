"""The same compact audio item UI on Assets and Check assets."""
from pathlib import Path

import streamlit as st

from .ui_tasks import busy,workspace_tasks,collect,take_task_result
from .storyboard_audio_ui import controls,start,pixabay_library


def card(manager,project_id,row,key,*,approval=False):
    target = "audio-card-"+key
    st.fragment(key=target)(_card)(manager,project_id,row,key,approval=approval,target=target)


def audio_tasks(project_id,resource):
    return [task for task in workspace_tasks()+list(st.session_state.get("workspace-task-queue",[]))
        if task.get("ui_owned_asset_project") == project_id and task.get("resource") == resource]


def task_signature(project_id,resource):
    return ([(task.get("id",id(task)),task.get("phase"),task["future"].done()) for task in audio_tasks(project_id,resource)],busy(),
        st.session_state.get("workspace-resource-completions",{}).get((project_id,resource),0))


def _card(manager,project_id,row,key,*,approval=False,target):
    from .audio_assets import upload,items
    kind,identity = row["Category"],row["ID"]
    resource = "storyboard-audio:"+kind+":"+identity
    for task in list(workspace_tasks()):
        if task.get("ui_owned_asset_project") == project_id and task.get("resource") == resource and task["future"].done():
            collect(task)
    if approval:
        from .storyboard_asset_check import check
        row = next((current for current in check(manager,project_id) if current["Category"] == kind and current.get("ID") == identity),row)
    error_key = "audio-item-error-"+project_id+"-"+resource
    response = take_task_result(project_id,[resource,key+"-elevenlabs",key+"-pixabay",key+"-pixabay-use",
        "Saving "+kind+": "+row["Asset"],"Linking "+kind+": "+row["Asset"]])
    if response and response[0][0] == "error":
        st.session_state[error_key] = response
    from .ui_panels import persistent_expander,asset_title
    active = next((task for task in audio_tasks(project_id,resource) if not task["future"].done()),None)
    phase = (" · Queuing…" if active.get("phase") in ("queued","waiting") else " · Saving…" if active.get("phase") == "saving" else " · Generating…") if active else ""
    label = asset_title(row["Asset"]+phase,row.get("FileExists",row["Ready"])) if approval else row["Asset"]+phase
    panel = persistent_expander(label,expanded=False,key=key+"-card")
    if not panel.open:
        watch(project_id,resource,target,task_signature(project_id,resource))
        return
    item = items(manager,project_id,kind).get(identity,{})
    description = row.get("Description") or item.get("description") or row["Asset"]
    row = {**row,"Description":description}
    with panel:
        st.caption(kind+" · "+identity)
        st.caption("Saved details, generation settings and audio are shared with Assets. Episode approval remains separate.")
        from .check_asset_sync import save_audio_details
        from .files import input_hash
        detail_key = key+"-details-"+input_hash([item.get("title",row["Asset"]),description])[:12]
        title = st.text_input("Title",value=item.get("title",row["Asset"]),key=detail_key+"-title",persist_state="session")
        detail = st.text_area("Description",value=description,key=detail_key+"-description",persist_state="session")
        details_changed = title.strip() != item.get("title",row["Asset"]) or detail.strip() != description
        if st.button("Save item details",key=detail_key+"-save",disabled=busy() or not details_changed or not title.strip() or not detail.strip()):
            try:
                save_audio_details(manager,project_id,kind,identity,title,detail)
                st.rerun()
            except (OSError,ValueError,RuntimeError) as exc:
                st.error(str(exc))
        controls(manager,project_id,row,key)
        if error := st.session_state.get(error_key):
            from .error_report_ui import render_error
            render_error(error[0][1],project_path=manager.project_path(project_id),task_key=error[1].get("task"),key=key+"-operation-error")
        with st.container(border=True):
            uploaded = st.file_uploader("Upload BGM" if kind == "BGM" else "Upload SFX",type=["wav","mp3","m4a","ogg","flac"],key=key+"-file",disabled=busy())
            source = st.text_input("Audio source / license",key=key+"-source")
            if st.button("Save audio",key=key+"-save",disabled=busy() or uploaded is None or not source.strip()):
                data,filename = uploaded.getvalue(),uploaded.name
                start(manager,project_id,lambda:upload(manager,project_id,kind,identity,data,filename,source),
                    "Saving "+kind+": "+row["Asset"],"local","storyboard-audio:"+kind+":"+identity)
        path = manager.project_path(project_id)
        from .storyboard_asset_check import document
        choices = {}
        for receipt in (path/"audio"/"library").glob("*.source.json"):
            value = document(path,receipt.relative_to(path))
            if value.get("file") and (path/value["file"]).is_file() and (kind == "BGM" or value.get("kind") != "bgm"):
                choices[value["file"]] = value
        if choices:
            selected = st.selectbox("Existing imported audio",list(choices),key=key+"-existing")
            if st.button("Link sound",key=key+"-link",disabled=busy()):
                value = choices[selected]
                start(manager,project_id,lambda:upload(manager,project_id,kind,identity,(path/selected).read_bytes(),Path(selected).name,value["source"]),
                    "Linking "+kind+": "+row["Asset"],"local","storyboard-audio:"+kind+":"+identity)
    watch(project_id,resource,target,task_signature(project_id,resource))


from .ui_panels import polling_fragment


@polling_fragment
def watch(project_id,resource,target,baseline):
    finished = [task for task in audio_tasks(project_id,resource) if task["future"].done()]
    for task in finished:
        collect(task)
    if finished or task_signature(project_id,resource) != baseline:
        from .asset_view_ui import refresh_target
        refresh_target(target)


def library(manager,project_id,kind):
    from .audio_assets import items,add_sfx
    from .bgm_library import add
    prefix = "audio-library-"+project_id+"-"+kind
    with st.expander("Add "+kind,expanded=False):
        with st.form(prefix+"-add"):
            identity = st.text_input("BGM ID",key=prefix+"-id") if kind == "BGM" else None
            title = st.text_input(kind+" title",key=prefix+"-title")
            description = st.text_area(kind+" description",key=prefix+"-description")
            if st.form_submit_button("Add "+kind+" draft",disabled=busy()):
                try:
                    if kind == "BGM":
                        add(manager,project_id,identity,title,description)
                    else:
                        add_sfx(manager,project_id,title,description)
                    st.rerun()
                except (OSError,ValueError,RuntimeError) as exc:
                    st.error(str(exc))
    for identity,item in items(manager,project_id,kind).items():
        card(manager,project_id,{"Category":kind,"ID":identity,"Asset":item["title"],"Description":item.get("description",item["title"])},
            prefix+"-"+identity)
    if kind == "BGM":
        pixabay_library(manager,project_id)
