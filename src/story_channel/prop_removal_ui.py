"""Library prop and mode removal confirmations."""
from uuid import uuid4
import streamlit as st
from .dialog_ui import dialog,request_open,Deferred


def close():
    st.session_state.pop("remove-prop-target",None)


def open_removal(media,pid,mode=None):
    from .prop_removal import snapshot
    st.session_state["dialog-context-manager"] = media.manager
    st.session_state["remove-prop-target"] = {"project_id":media.project_id,"pid":pid,"mode":mode,
        "expected":snapshot(media.load(),pid,mode),"token":uuid4().hex}
    request_open()


@dialog("Remove prop / mode",on_dismiss=close)
def removal_dialog(media,target):
    from .ui_tasks import busy
    state = media.load()
    prop = state["props"].get(target["pid"])
    if not prop:
        st.info("This prop has already been removed.")
    else:
        mode = target["mode"]
        if mode is None:
            st.write("Delete prop **"+prop["title"]+"** (`"+target["pid"]+"`)?")
            st.write("Modes: "+(", ".join(prop["modes"]) or "None"))
            st.caption("Deletes the shared prop draft, its modes, all visual revisions and generated image files. Saved episode snapshots and billing records are kept.")
            typed = st.text_input("Type the full prop name to confirm",key="remove-prop-name-"+target["token"])
        else:
            st.write("Delete mode **"+mode+"** from **"+prop["title"]+"**?")
            count = len(state["visuals"].get("prop:"+target["pid"]+":"+mode,[]))
            st.write(f"This removes this mode, its {count} saved visual revision(s), and their generated image files. Other modes are kept.")
            if len(prop["modes"]) == 1:
                st.caption("This is the last mode. The prop will remain as a Draft; add a new mode before generating a visual.")
            typed = None
        st.caption("Storyboard text is kept. Deleted assets are not automatically recreated; scenes that still need them must remove the requirement or deliberately recreate the draft in Check assets.")
        if st.button("Confirm deletion",key="confirm-remove-prop",type="primary",disabled=busy() or mode is None and typed != prop["title"]):
            try:
                from .prop_removal import remove
                with st.spinner("Deleting…"):
                    remove(media,target["pid"],mode=mode,confirmation=typed,expected=target["expected"])
            except (OSError,ValueError,RuntimeError) as exc:
                st.error(str(exc))
            else:
                close()
                st.rerun(scope="app")
    if st.button("Cancel",key="cancel-remove-prop"):
        close()
        st.rerun(scope="app")


def show(manager,target):
    from .library_media import LibraryMedia
    removal_dialog(Deferred(lambda:LibraryMedia(manager,target["project_id"])),target)
