"""Quick outline editing from the story workflow."""
import streamlit as st

from .ui_tasks import busy, launch


def close_outline_dialog():
    st.session_state.pop("outline-dialog-project", None)


@st.dialog("Edit story outline", width="large", on_dismiss=close_outline_dialog)
def outline_dialog(manager, project_id, pipeline):
    draft_key = f"outline-dialog-draft-{project_id}"
    baseline = st.session_state[f"outline-dialog-baseline-{project_id}"]
    draft = st.text_area("Outline", key=draft_key, height=400, disabled=busy())
    st.caption("Saving updates the outline and invalidates dependent story stages and approvals.")
    error = st.session_state.get(f"outline-dialog-error-{project_id}")
    if error:
        st.error(error)
    save, cancel = st.columns(2)
    if save.button("Save outline", key="save-outline-dialog", type="primary", disabled=busy()):
        st.session_state.pop(f"outline-dialog-error-{project_id}", None)
        if not draft.strip():
            st.error("Story outline must not be empty")
        else:
            def saved(_):
                st.session_state[f"outline-{project_id}"] = draft
                st.session_state.pop(f"outline-typos-{project_id}", None)
                if st.session_state.get("outline-dialog-project") == project_id:
                    close_outline_dialog()

            launch(lambda: pipeline.edit_bundle(
                {"outline": draft} if draft != baseline else {},
                expected={"outline": baseline}), label="Save outline", success=saved)
    if cancel.button("Cancel", key="cancel-outline-dialog", disabled=busy()):
        close_outline_dialog()
        st.rerun()


def outline_heading(manager, project_id, pipeline):
    def open_dialog():
        baseline = manager.read_outline(project_id)
        st.session_state[f"outline-dialog-baseline-{project_id}"] = baseline
        st.session_state[f"outline-dialog-draft-{project_id}"] = st.session_state.get(
            f"outline-{project_id}", baseline)
        st.session_state["outline-dialog-project"] = project_id
        st.session_state.pop("active-stage-browser", None)
        st.session_state.pop("modify-target", None)
        st.session_state.pop("workspace-task-message", None)
        st.session_state.pop(f"outline-dialog-error-{project_id}", None)

    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader("Story outline", width="content")
        st.button("", icon=":material/edit:", help="Edit story outline",
                  key="edit-story-outline", on_click=open_dialog, disabled=busy())
    if st.session_state.get("outline-dialog-project") == project_id:
        outline_dialog(manager, project_id, pipeline)
