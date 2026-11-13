"""Compact prompt, guidance upload and single-master controls."""
from .image_preview_ui import image_preview
import streamlit as st
from .background_assets_ui import launch_background, loading_result
from .background_library import BackgroundLibrary
from .generation_ui import generation_button
from .ui_tasks import busy, generation_busy, asset_activity


def render(engine, lid, state):
    from .simple_locations import save
    from .files import input_hash
    library = BackgroundLibrary(engine)
    spec = state.location_reference_sets[lid]
    prefix = f"simple-background-{engine.project_id}-{lid}"
    with st.container(border=True):
        st.subheader("SIMPLE background master")
        text_key = prefix+"-prompt-"+input_hash(spec.get("simple_prompt", ""))[:12]
        st.session_state.setdefault(text_key, spec.get("simple_prompt", ""))
        draft = st.text_area("Detail prompt", key=text_key, height=180)
        changed = draft.strip() != spec.get("simple_prompt", "").strip()
        st.button("Save detail prompt", key=prefix+"-save", disabled=busy() or not changed or not draft.strip(),
            on_click=lambda: launch_background(engine, lambda: save(engine,lid,prompt=st.session_state[text_key]),
                "Saving background prompt", service="local", subject=lid))
        upload_key = prefix+"-guidance"
        def upload():
            value = st.session_state.get(upload_key)
            if value is not None:
                data = value.getvalue()
                launch_background(engine,lambda:library.set_image(lid,data),"Saving background guidance",service="local",subject=lid)
        st.file_uploader("Guidance image (optional)",type=["png","jpg","jpeg","webp"],key=upload_key,on_change=upload)
        if lid in state.background_images:
            image_preview(str(library.image(state,lid)),width=300)
            st.button("Remove guidance image",key=prefix+"-remove-guidance",disabled=busy(),
                on_click=lambda:launch_background(engine,lambda:library.set_image(lid,None),"Removing background guidance",service="local",subject=lid))
        if changed:
            st.caption(":orange[Save the detail prompt before generating.]")
        subject = lid+"__simple_master"
        activity = asset_activity(engine,"backgrounds",subject)
        generation_button("Generate master (OpenAI)",key=prefix+"-generate",activity=activity,
            disabled=generation_busy() or changed or not draft.strip() or not engine.can_generate("backgrounds",state),
            on_click=lambda:launch_background(engine,lambda:engine.generate("backgrounds",subject=subject,force=True),
                "Generating background "+lid,service="openai",subject=lid))
        if activity in ("queued","running"):
            loading_result("Queuing..." if activity == "queued" else "Generating...")
        record = state.records.get("backgrounds:"+subject)
        if record and record.asset:
            image_preview(str(engine.local_path(record.asset.file_path)),width="stretch")
            st.caption(f"{record.status} · revision {record.revision}")
        if record and record.error:
            from .error_report_ui import render_error
            render_error(record.error,project_path=engine.path,task_key="assets:backgrounds:"+subject,key=prefix+"-error")
        from .background_revisions_ui import actions
        actions(engine,subject,allow_generate=False)
