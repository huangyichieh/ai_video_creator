from .dialog_ui import dialog
"""Explicit project deletion; confirmation never starts provider requests."""
import streamlit as st

from .ui_tasks import busy


def dismiss_delete():
    st.session_state.pop("delete-project-target", None)


def open_delete(project_id, title):
    from .dialog_ui import request_open
    request_open()
    for key in ("active-stage-browser", "modify-target", "voice-design-target", "studio-modify-target"):
        st.session_state.pop(key, None)
    st.session_state["api-key-dialog-open"] = False
    st.session_state["delete-project-target"] = {"id": project_id, "title": title}
    st.session_state.pop("delete-project-name-" + project_id, None)


@dialog("Delete project", on_dismiss=dismiss_delete)
def delete_dialog(manager, target):
    st.write("Permanently delete this project?")
    st.text(target["title"])
    st.caption("Project ID: " + target["id"])
    st.warning("This deletes the World, all its episodes, generated files, history and local usage records. It cannot be undone. API keys and other projects are kept.")
    name = st.text_input("Type the full project name to confirm", key="delete-project-name-" + target["id"], disabled=busy())
    delete, cancel = st.columns(2)
    if delete.button("Delete", key="confirm-delete-project", type="primary", disabled=busy() or name != target["title"]):
        try:
            with st.spinner("Deleting project..."):
                removed = manager.delete(target["id"], confirmation=name)
        except (OSError, ValueError, RuntimeError) as exc:
            st.error(str(exc))
        else:
            st.session_state["deleted-project-ids"] = removed
            dismiss_delete()
            st.rerun(scope="app")
    if cancel.button("Cancel", key="cancel-delete-project"):
        dismiss_delete()
        st.rerun(scope="app")


def clear_deleted_state():
    removed = st.session_state.pop("deleted-project-ids", [])
    if not removed:
        return
    for key in list(st.session_state):
        if any(pid in key for pid in removed):
            st.session_state.pop(key, None)
    for key in ("selected_project", "project-selector"):
        if st.session_state.get(key) in removed:
            st.session_state.pop(key, None)
    st.session_state["workspace_page"] = "World"
    st.session_state["creating_project"] = False
    st.session_state["browser-save-message"] = "Project deleted."
