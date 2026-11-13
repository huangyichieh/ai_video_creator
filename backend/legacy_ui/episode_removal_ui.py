"""Title-confirmed deletion scoped to one episode."""
import streamlit as st

from .dialog_ui import dialog,request_open
from .ui_tasks import busy


def dismiss():
    st.session_state.pop("remove-episode-target",None)


def open_removal(manager,world_id,episode_id,title):
    st.session_state["dialog-context-manager"] = manager
    for key in ("active-stage-browser","modify-target","studio-modify-target","voice-design-target"):
        st.session_state.pop(key,None)
    st.session_state.pop("remove-episode-title-"+episode_id,None)
    st.session_state["remove-episode-target"] = {"world_id":world_id,"episode_id":episode_id,"title":title}
    request_open()


@dialog("Remove episode",on_dismiss=dismiss)
def removal_dialog(manager,target):
    from .studio import Episodes
    st.write("Permanently delete this episode?")
    st.text(target["title"])
    st.warning("This deletes this episode's content, generated files, history and local usage records. It cannot be undone. The World and other episodes are kept.")
    title = st.text_input("Type the full episode title to confirm",key="remove-episode-title-"+target["episode_id"],disabled=busy())
    confirm,cancel = st.columns(2)
    if confirm.button("Delete episode",key="confirm-remove-episode",type="primary",disabled=busy() or title != target["title"]):
        try:
            with st.spinner("Deleting episode..."):
                removed = Episodes(manager,target["world_id"]).remove(target["episode_id"],confirmation=title)
        except (OSError,ValueError,RuntimeError) as exc:
            st.error(str(exc))
        else:
            st.session_state["deleted-episode"] = {"world_id":target["world_id"],"ids":removed}
            dismiss()
            st.rerun(scope="app")
    if cancel.button("Cancel",key="cancel-remove-episode"):
        dismiss()
        st.rerun(scope="app")


def clear_removed_state():
    target = st.session_state.pop("deleted-episode",None)
    if not target:
        return
    for key in list(st.session_state):
        if any(episode_id in key for episode_id in target["ids"]):
            st.session_state.pop(key,None)
    for key in (f"selected-episode-{target['world_id']}",f"episode-selector-{target['world_id']}"):
        if st.session_state.get(key) in target["ids"]:
            st.session_state.pop(key,None)
    st.session_state["workspace_page"] = "Story/Episode"
    st.session_state["browser-save-message"] = "Episode deleted."
