"""Compact scene-local requirement entry; media is generated on Assets later."""
import streamlit as st

from .ui_tasks import busy


def render(scene_id,key,on_add,*,auto_save=False):
    with st.expander("Add new background, prop or BGM",expanded=False):
        kind = st.selectbox("Asset type",["Background","Prop","BGM"],key=key+"new-asset-kind",disabled=busy())
        st.text_input("Asset title (optional)",key=key+"new-asset-title",disabled=busy())
        description = st.text_area("Short asset description",key=key+"new-asset-description",height=100,disabled=busy())
        st.caption({"Background":"Describe the location's visible appearance. This replaces the scene's selected background.",
            "Prop":"Describe only the prop's appearance. It will be added alongside the scene's existing props, with a default mode.",
            "BGM":"Describe the music's mood, instruments and style. This replaces the scene's selected BGM."}[kind])
        st.caption("Creates a draft requirement with an automatic ID. "+("It saves automatically to Assets for generation or upload." if auto_save else "Save storyboard changes to register it on Assets for generation or upload."))

        def add():
            try:
                identity = on_add(scene_id,st.session_state[key+"new-asset-kind"],
                    st.session_state[key+"new-asset-description"],st.session_state[key+"new-asset-title"])
            except ValueError as exc:
                st.session_state[key+"new-asset-message"] = ("error",str(exc))
            else:
                st.session_state[key+"new-asset-description"] = ""
                st.session_state[key+"new-asset-title"] = ""
                st.session_state[key+"new-asset-message"] = ("success","Added draft "+identity+(". Saving automatically." if auto_save else ". Save storyboard changes to register it on Assets."))

        st.button("Add requirement to scene",key=key+"add-asset-requirement",disabled=busy() or not description.strip(),on_click=add)
        if message := st.session_state.pop(key+"new-asset-message",None):
            getattr(st,message[0])(message[1])
