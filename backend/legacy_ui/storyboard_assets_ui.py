"""Source-linked storyboard asset requests, distinct from completed media."""
import streamlit as st


def show_requests(manager,world_id,kind):
    from .storyboard_assets import requests
    rows = requests(manager,world_id,kind)
    if not rows:
        return
    def status(row):
        if kind == "bgm":
            from .bgm_library import entries,ready
            return "Ready" if ready(manager.project_path(world_id),entries(manager,world_id).get(row["id"],{})) else "Pending audio import"
        return "Requested — generate or import"
    with st.expander("Added from storyboards",expanded=True):
        st.dataframe([{"ID":row["id"],"Title":row["title"],"Description":row["description"],
            "Episodes / scenes":"; ".join(source["episodeTitle"]+": "+(", ".join(source["sceneIds"]) or "Declared") for source in row["sources"]),
            "Status":status(row)} for row in rows],hide_index=True,width="stretch")
        if kind == "bgm":
            st.caption("Reusable music cards below support upload, ElevenLabs Music and selection from the imported Pixabay library.")
        elif kind == "sfx":
            st.caption("SFX cards below support licensed audio upload and ElevenLabs generation; a request becomes usable when its sound file exists.")
        else:
            st.caption("New props are registered below. Generate their mode visuals when needed; existing visuals are preserved.")
