"""Collapsed shared prop editors beneath the episode's Props checklist."""
from .image_preview_ui import image_preview
import streamlit as st

from .files import input_hash
from .ui_tasks import busy,generation_busy,launch,workspace_tasks,collect,take_task_result
from .ui_panels import polling_fragment


def render(manager,project_id,rows):
    pending = {}
    for row in rows:
        if row.get("ID","").startswith("prop:"):
            _,pid,mode = row["ID"].split(":",2)
            pending.setdefault(pid,[]).append(mode)
    if not pending:
        return
    from .library_media import LibraryMedia
    from .pipeline import Pipeline
    owner = manager.load(project_id).world_project_id or project_id
    media = LibraryMedia(manager,owner)
    declared = {item.prop_id:item for item in Pipeline(manager,project_id).artifact("storyboard").props}
    st.caption("Open a prop to edit or generate its mode visuals. Created visuals synchronize into this episode and are automatically ready.")
    for pid,modes in pending.items():
        target = f"check-prop-editor-{project_id}-{pid}"
        item = declared.get(pid)
        fallback = {"title":item.title,"description":item.description,"modes":item.modes} if item else {"title":pid,"description":"","modes":modes}
        st.fragment(key=target)(_card)(media,project_id,pid,modes,fallback,target,approval=True)


def render_library(media):
    for pid,prop in media.load()["props"].items():
        target = f"asset-prop-editor-{media.project_id}-{pid}"
        st.fragment(key=target)(_card)(media,media.project_id,pid,[],prop,target)


def signature(media,pid):
    state = media.load()
    tasks = list(workspace_tasks())+list(st.session_state.get("workspace-task-queue",[]))
    return input_hash({"prop":state["props"].get(pid),
        "visuals":{key:value for key,value in state["visuals"].items() if key.startswith("prop:"+pid+":")},
        "active":{key:value for key,value in state["activeVisuals"].items() if key.startswith("prop:"+pid+":")},
        "tasks":[(task["id"],task.get("phase"),task["future"].done()) for task in tasks if task.get("check_prop_owner") == media.project_id and task.get("check_prop_id") == pid]})


@polling_fragment
def poll(media,pid,target,baseline):
    if signature(media,pid) != baseline:
        from .asset_view_ui import refresh_target
        refresh_target(target)


@polling_fragment
def visual_preview(media,pid,mode,approval=False):
    """Read the saved selection on each tick, independently of the parent card."""
    from .check_prop_assets import selected
    from .prop_revisions import intact
    version = selected(media.load(),pid,mode)
    ready = intact(media,version)
    if ready:
        image_preview(str(media.assets.local_path(version["asset"]["filePath"])),width=320)
        st.caption("Revision "+str(version["revision"]))
        if origin := version.get("reusedFrom"):
            st.caption("Reused from "+origin["mode"]+" · revision "+str(origin["revision"]))
    elif version:
        st.warning("The selected prop image is missing or modified. Generate or apply an intact revision.")
    if approval:
        st.caption(":green[Ready · automatically approved]" if ready else ":orange[Needs generation]")


def start(media,episode_id,pid,callback,label,*,service="local",resource=None):
    st.session_state.pop(f"check-prop-message-{episode_id}-{pid}",None)
    def run():
        result = callback()
        from .check_prop_assets import sync
        sync(media,episode_id,pid)
        return result
    task = launch(run,label,project_path=media.path,asset_engine=media.assets,service=service,
        resource=resource,allow_parallel=True,queue_enabled=True,rerun_on_start=False,inline=True)
    if task:
        task.update(check_prop_owner=media.project_id,check_prop_id=pid,ui_owned_asset_project=episode_id)
    return task


def _card(media,episode_id,pid,required_modes,fallback,target,approval=False):
    if st.session_state.get("dialog-open-requested"):
        from .dialog_ui import show_pending_dialog
        show_pending_dialog()
    from .generation_ui import generation_button
    from .prop_drafts import save
    message_key = f"check-prop-message-{episode_id}-{pid}"
    for task in list(workspace_tasks()):
        if task["future"].done() and task.get("check_prop_owner") == media.project_id and task.get("check_prop_id") == pid:
            collect(task)
            result = take_task_result(media.project_id,[task.get("generation_button_key") or task.get("label","")])
            if result:
                st.session_state[message_key] = result
    result = take_task_result(media.project_id,["Saving prop draft "+pid],prefixes=(target+"-","Generating prop visual prop:"+pid+":"))
    if result:
        st.session_state[message_key] = result
    baseline = signature(media,pid)
    state = media.load()
    prop = state["props"].get(pid)
    values = prop or fallback
    from .ui_panels import persistent_expander,asset_title
    label = values["title"]+" · "+pid
    from .check_prop_assets import selected
    from .storyboard_asset_check import intact
    displayed_modes = required_modes if approval else values.get("modes",[])
    versions = [selected(state,pid,mode) for mode in displayed_modes]
    exists = bool(versions) and all(intact(media.path,{"file":version.get("asset",{}).get("filePath"),"fileHash":version.get("fileHash")}) for version in versions)
    if approval:
        label = asset_title(label,exists)
    else:
        label += " · "+(":green[Ready]" if exists else ":orange[Draft]")
    panel = persistent_expander(label,expanded=False,key=target)
    if not panel.open:
        poll(media,pid,target,baseline)
        return
    with panel:
        if not approval:
            from .prop_removal_ui import open_removal
            st.button("Remove prop",key=target+"-remove",disabled=busy(),on_click=lambda:open_removal(media,pid))
        from .library_media import PROP_IMAGE_MODEL
        st.caption("Offline placeholder images" if media.assets.demo else "Prop images: OpenAI / "+PROP_IMAGE_MODEL+" · uses OPENAI_API_KEY")
        st.caption("Prop visuals use a clean white background with only the prop shown. Style guidance applies to the prop itself.")
        st.caption("Required modes: "+", ".join(required_modes) if approval else "Reusable prop draft. Generate its mode visuals here; created visuals are automatically ready in Check assets.")
        key = target+"-"+input_hash(prop)[:12]
        title = st.text_input("Prop title",value=values["title"],key=key+"-title",disabled=busy(),persist_state="session")
        description = st.text_area("Prop description",value=values["description"],key=key+"-description",disabled=busy(),persist_state="session")
        initial_modes = list(dict.fromkeys([*values.get("modes",[]),*required_modes]))
        modes_text = st.text_area("Modes (one per line)",value="\n".join(initial_modes),key=key+"-modes",disabled=busy(),persist_state="session")
        modes = list(dict.fromkeys(mode.strip() for mode in modes_text.splitlines() if mode.strip()))
        changed = prop is None or title.strip() != values["title"] or description.strip() != values["description"] or modes != values.get("modes",[])
        if any(mode not in modes for mode in required_modes):
            st.caption(":orange[Keep all modes required by this storyboard.]")
        with st.container(horizontal=True):
            st.button("Add prop draft" if prop is None else "Save prop draft",key=key+"-save",disabled=busy() or not changed or not title.strip() or not description.strip() or not modes or any(mode not in modes for mode in required_modes),
                on_click=lambda:start(media,episode_id,pid,lambda:save(media,pid,title,description,modes,expected=input_hash(prop)),"Saving prop draft "+pid))
            def revert():
                st.session_state[key+"-title"] = values["title"]
                st.session_state[key+"-description"] = values["description"]
                st.session_state[key+"-modes"] = "\n".join(values.get("modes",required_modes))
            st.button("Revert changes",key=key+"-revert",disabled=busy() or not changed,on_click=revert)
        if changed:
            st.caption("Save the draft before generating its visuals.")
        for mode in values.get("modes",required_modes):
            with st.container(border=True):
                st.markdown("**"+mode+"**")
                if not approval:
                    st.button("Remove mode",key=target+"-remove-mode-"+mode,disabled=busy(),
                        on_click=lambda mode=mode:open_removal(media,pid,mode))
                asset_key = "prop:"+pid+":"+mode
                from .prop_revisions_ui import open_revisions
                st.button("View / apply saved revisions",key=target+"-revisions-"+mode,
                    disabled=not any(state["visuals"].get("prop:"+pid+":"+item) for item in values.get("modes",required_modes)),
                    on_click=lambda mode=mode:open_revisions(media,pid,mode,episode_id if approval else None))
                visual_preview(media,pid,mode,approval and mode in required_modes)
                prompt = st.text_area("Visual prompt",value=values["description"]+"\nMode: "+mode,key=key+"-prompt-"+mode,persist_state="session")
                size = st.selectbox("Image resolution",["1024x1024","1536x1024"],key=key+"-size-"+mode,
                    format_func=lambda value:"Standard · 1024 × 1024" if value == "1024x1024" else "High resolution · 1536 × 1024",
                    help="Standard is suitable for most props. Use high resolution for detailed props or close-ups.")
                generation_button("Generate mode visual",key=key+"-generate-"+mode,disabled=generation_busy() or changed or not prompt.strip(),
                    on_click=lambda asset_key=asset_key,prompt=prompt,size=size:start(media,episode_id,pid,lambda:media.image(asset_key,"Prop reference, no text. "+prompt,size=size),
                        "Generating prop visual "+asset_key,service="openai",resource=asset_key))
        message = st.session_state.get(message_key)
        if message and message[0][0] == "error":
            from .error_report_ui import render_error
            render_error(message[0][1],project_path=media.path,task_key=message[1].get("task"),key=target+"-error")
    poll(media,pid,target,baseline)
