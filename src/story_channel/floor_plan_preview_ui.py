"""Compact clickable guide thumbnails with deferred full-size previews."""
import base64
from io import BytesIO
from pathlib import Path

from PIL import Image
import streamlit as st

from .dialog_ui import dialog,request_open


@st.cache_data(show_spinner=False,max_entries=96)
def _thumbnail(path,stamp):
    with Image.open(path) as image:
        image.thumbnail((320,240),Image.Resampling.LANCZOS)
        buffer = BytesIO()
        image.convert("RGB").save(buffer,format="PNG")
    return "data:image/png;base64,"+base64.b64encode(buffer.getvalue()).decode("ascii")


def thumbnails(engine,lid,guides,*,include_camera_guides=True):
    images = []
    if guides.get("floorPlan"):
        images.append(("layout","Visible floor plan · complete object layout",guides["floorPlan"]))
    if include_camera_guides:
        from .directional_masters import NAMES,LABELS
        images.extend((name,LABELS[name]+"-facing camera guide",guides["views"][name]["cameraGuide"])
            for name in NAMES)

    # Native button callbacks keep the click within Streamlit and open the
    # modal shell before loading its full-resolution image.
    st.html("""<style>
      [class*="st-key-floor-guide-thumbnails-"] button {padding:4px;border-radius:8px;height:auto;}
      [class*="st-key-floor-guide-thumbnails-"] button img {height:auto !important;max-height:none !important;
        width:100% !important;max-width:260px !important;display:block;}
      [class*="st-key-floor-guide-thumbnails-"] button p {margin:0;}
    </style>""")
    with st.container(key=f"floor-guide-thumbnails-{engine.project_id}-{lid}",horizontal=True,gap="medium"):
        for name,title,relative in images:
            with st.container(width=260):
                path = engine.local_path(relative)
                try:
                    stamp = path.stat()
                    uri = _thumbnail(str(path),(stamp.st_mtime_ns,stamp.st_size))
                except (OSError,ValueError):
                    st.caption("Image unavailable. Generate floor plan image again.")
                    continue
                def open_image(relative=relative,title=title):
                    st.session_state["dialog-context-manager"] = engine.manager
                    st.session_state["floor-plan-preview-target"] = {
                        "project_id":engine.project_id,"file":relative,"title":title,
                        "images":[{"file":file,"title":label} for _,label,file in images]}
                    request_open()
                st.button(f"![{title}]({uri})",key=f"floor-guide-preview-{engine.project_id}-{lid}-{name}",
                          help="Click to enlarge",width=260,on_click=open_image)
                st.caption(title)


def close():
    st.session_state.pop("floor-plan-preview-target",None)


def close_and_refresh():
    close()
    st.rerun(scope="app")


def navigate(direction):
    target = st.session_state.get("floor-plan-preview-target")
    if not target:
        return
    images = target.get("images",[])
    if not images:
        return
    index = next((i for i,item in enumerate(images) if item["file"] == target["file"]),0)
    selected = images[(index+direction)%len(images)]
    st.session_state["floor-plan-preview-target"] = {**target,**selected}


@dialog("Floor plan & camera guide",width="large",on_dismiss=close)
def preview_dialog(manager,target):
    # Dialog fragments retain their initial arguments; navigation lives in Session State.
    target = st.session_state.get("floor-plan-preview-target",target)
    images = target.get("images",[{"file":target["file"],"title":target["title"]}])
    index = next((i for i,item in enumerate(images) if item["file"] == target["file"]),0)
    with st.container(horizontal=True,vertical_alignment="center"):
        st.button("Previous",icon=":material/chevron_left:",key="previous-floor-plan-preview",
                  shortcut="Left",disabled=len(images)<2,on_click=navigate,args=(-1,))
        st.caption(f"{index+1} / {len(images)}")
        st.button("Next",icon=":material/chevron_right:",key="next-floor-plan-preview",
                  shortcut="Right",disabled=len(images)<2,on_click=navigate,args=(1,))
    st.markdown("**"+target["title"]+"**")
    try:
        root = manager.project_path(target["project_id"]).resolve()
        relative = Path(target["file"])
        path = (root/relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError("Image must belong to the selected project.")
        st.image(str(path),width="stretch")
    except (OSError,ValueError) as exc:
        st.error(str(exc))
    st.button("Close",key="close-floor-plan-preview",on_click=close_and_refresh)
