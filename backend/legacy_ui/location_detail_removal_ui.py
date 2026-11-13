"""Exact-ID confirmation scoped to one location detail."""
import streamlit as st

from .dialog_ui import dialog,request_open,Deferred
from .ui_tasks import busy
from .files import input_hash


def dismiss():
    st.session_state.pop("remove-location-detail-target",None)


def open_removal(engine,lid,identity,reference):
    st.session_state["dialog-context-manager"] = engine.manager
    key = f"remove-location-detail-id-{engine.project_id}-{lid}-{identity}"
    st.session_state.pop(key,None)
    st.session_state["remove-location-detail-target"] = {"project_id":engine.project_id,
        "lid":lid,"id":identity,"expected":input_hash(reference),"demo":engine.demo,"input_key":key}
    request_open()


@dialog("Delete detail background",on_dismiss=dismiss)
def removal_dialog(engine,target):
    st.write("Delete detail background **"+target["id"]+"**?")
    st.caption("Saved generated files and revision history are retained.")
    typed = st.text_input("Type the detail ID to confirm",key=target["input_key"],disabled=busy())
    confirm,cancel = st.columns(2)
    if confirm.button("Delete detail background",key="confirm-delete-location-detail",type="primary",disabled=busy() or typed != target["id"]):
        try:
            from .location_details import remove
            remove(engine,target["lid"],target["id"],confirmation=typed,expected=target["expected"])
        except (OSError,ValueError,RuntimeError) as exc:
            st.error(str(exc))
        else:
            dismiss()
            st.rerun(scope="app")
    if cancel.button("Cancel",key="cancel-delete-location-detail"):
        dismiss()
        st.rerun(scope="app")


def show(manager,target):
    from .assets import AssetPipeline
    removal_dialog(Deferred(lambda:AssetPipeline(manager,target["project_id"],demo=target["demo"])),target)
