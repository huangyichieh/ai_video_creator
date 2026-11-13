"""Preview saved mode images before selecting the shared current revision."""
from .image_preview_ui import image_preview
from .revision_labels import revision_label
from uuid import uuid4
import streamlit as st
from .dialog_ui import dialog,Deferred,request_open


def close():
    st.session_state.pop("prop-revisions-target",None)


def open_revisions(media,pid,mode,episode_id=None):
    st.session_state["dialog-context-manager"] = media.manager
    st.session_state["prop-revisions-target"] = {"project_id":media.project_id,"pid":pid,"mode":mode,
        "episode_id":episode_id,"token":uuid4().hex}
    request_open()


@dialog("Prop visual revisions",width="large",on_dismiss=close)
def revisions_dialog(media,target):
    from .check_prop_assets import selected
    from .prop_removal import snapshot
    from .prop_revisions import intact,apply
    from .ui_tasks import busy
    state = media.load()
    pid,mode = target["pid"],target["mode"]
    prop = state["props"].get(pid)
    source_modes = [item for item in prop["modes"] if state["visuals"].get("prop:"+pid+":"+item)] if prop else []
    if not prop or mode not in prop["modes"]:
        st.warning("This prop or mode no longer exists.")
    elif not source_modes:
        st.info("No saved visual revisions in any mode yet.")
    else:
        prefix = "prop-revisions-"+target["token"]
        if prefix+"-expected" not in st.session_state:
            st.session_state[prefix+"-expected"] = snapshot(state,pid,mode)
        current = selected(state,pid,mode)
        st.markdown("**"+prop["title"]+" · "+mode+"**")
        st.caption("Current revision: "+str(current.get("revision","None")))
        source_mode = st.selectbox("Source mode",source_modes,index=source_modes.index(mode) if mode in source_modes else 0,key=prefix+"-source-mode")
        versions = state["visuals"].get("prop:"+pid+":"+source_mode,[])
        numbers = sorted((item["revision"] for item in versions),reverse=True)
        revision = st.selectbox("Saved revision",numbers,key=prefix+"-choice-"+source_mode,
            format_func=lambda number:revision_label(next(item for item in versions if item["revision"] == number))+(" · Current" if source_mode == mode and number == current.get("revision") else ""))
        version = next(item for item in versions if item["revision"] == revision)
        usable = intact(media,version)
        if usable:
            image_preview(str(media.assets.local_path(version["asset"]["filePath"])),width="stretch")
        else:
            st.warning("This revision's image is missing or modified and cannot be applied.")
        asset = version.get("asset",{})
        st.caption("Service/model: "+str(asset.get("provider","Unknown"))+" / "+str(asset.get("model","Unknown")))
        with st.expander("Generation prompt",expanded=False):
            st.write(version.get("prompt","") or "No saved prompt")
        same = (source_mode == mode and revision == current.get("revision")) or (
            current.get("reusedFrom") == {"mode":source_mode,"revision":revision} and current.get("fileHash") == version.get("fileHash")
            and current.get("asset",{}).get("filePath") == version.get("asset",{}).get("filePath"))
        if source_mode != mode:
            st.info("Use this image from **"+source_mode+"** as the visual for **"+mode+"**. A new destination revision records the reuse; the image is used as-is.")
        st.caption("Applying uses this saved image without an API request or generation cost. Intact visuals are automatically ready in Check assets.")
        if st.button("Applied" if same else "Apply revision" if source_mode == mode else "Apply to "+mode,key=prefix+"-apply",disabled=busy() or same or not usable):
            try:
                from .files import input_hash
                apply(media,pid,mode,revision,expected=st.session_state[prefix+"-expected"],episode_id=target.get("episode_id"),
                    source_mode=source_mode,source_expected=input_hash(version))
            except (OSError,ValueError,RuntimeError) as exc:
                st.error(str(exc))
            else:
                close()
                st.rerun(scope="app")
    if st.button("Close",key="close-prop-revisions"):
        close()
        st.rerun(scope="app")


def show(manager,target):
    from .library_media import LibraryMedia
    revisions_dialog(Deferred(lambda:LibraryMedia(manager,target["project_id"])),target)
