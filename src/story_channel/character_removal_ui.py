from .dialog_ui import dialog
"""Confirm removal and repeat eligibility checks before changing shared assets."""
from .error_report_ui import render_error, error_project, report_widget_key
import streamlit as st
from .character_removal import CharacterRemoval
from .ui_tasks import busy


@dialog("Remove character", on_dismiss=lambda: st.session_state.pop("remove-character-target", None))
def removal_dialog(manager, target):
    st.write("Remove this character from the shared asset library?")
    st.markdown("**" + target["character_id"] + "**")
    st.caption("Existing episode snapshots, generated media and usage records are preserved. Shared character and identity approvals must be checked again.")
    confirm, cancel = st.columns(2)
    if confirm.button("Remove character", key="confirm-remove-character", type="primary", disabled=busy()):
        try:
            CharacterRemoval(manager, target["project_id"]).remove(target["character_id"], expected=target["expected"])
            st.session_state.pop("remove-character-target", None)
            st.session_state["browser-save-message"] = "Character removed from the shared asset library. Existing episodes and generated files were preserved."
            st.rerun()
        except (OSError, ValueError, RuntimeError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('character_removal_ui:20', locals()))
    if cancel.button("Cancel", key="cancel-remove-character"):
        st.session_state.pop("remove-character-target", None)
        st.rerun()


def remove_button(engine, character, status, *, show_reason=True):
    def open_removal():
        from .dialog_ui import request_open
        st.session_state["remove-character-target"] = {
            "project_id": engine.project_id, "character_id": character.character_id,
            "expected": character.model_dump_json(by_alias=True)}
        request_open()
    st.button("Remove character", key="remove-character-" + character.character_id,
              disabled=busy() or not status.allowed,
              help=status.reason or "Allowed when this character is unapproved OR unused in stories, scripts and animations.",
              on_click=open_removal)
    if show_reason and not status.allowed:
        st.caption(status.reason)
