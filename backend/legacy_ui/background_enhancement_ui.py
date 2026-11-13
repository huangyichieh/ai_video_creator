"""Optional reference generation in a modal that stays open during provider work."""
from .image_preview_ui import image_preview
import streamlit as st

from .dialog_ui import dialog, Deferred
from .generation_ui import generation_button


def close():
    st.session_state.pop("background-enhance-target", None)


def close_and_refresh():
    close()
    st.rerun(scope="app")


@dialog("Enhance with Runway", width="large", on_dismiss=close)
def enhancement_dialog(engine, lid):
    content(engine, lid)
    st.button("Close", key="close-background-enhance", on_click=close_and_refresh)


@st.fragment(run_every=1)
def content(engine, lid):
    from .background_enhancement import generate
    from .dual_panorama import all_views_approved
    from .ui_tasks import launch, collect, workspace_tasks, take_task_result, generation_busy
    from .error_report_ui import render_error
    prefix = f"background-enhance-{engine.project_id}-{lid}"
    for task in list(workspace_tasks()):
        if task.get("background_enhancement") == prefix and task["future"].done():
            collect(task)
    result = take_task_result(engine.project_id, [prefix+"-generate"])
    if result and result[0][0] == "error":
        st.session_state[prefix+"-error"] = result[0][1]
    st.caption("Runway / gen4_image. Creates a separate optional reference; approved panorama masters and yaw images stay unchanged.")
    modes = {"enhancement":"Enhancement", "repair":"Local repair", "additional_reference":"Additional reference view"}
    mode = st.selectbox("Operation", list(modes), format_func=modes.get, key=prefix+"-mode")
    if mode == "additional_reference":
        yaw = st.number_input("Additional view yaw (degrees)", min_value=0, max_value=359, value=0, key=prefix+"-yaw-extra")
        st.caption("A local projection at this angle is created first; Runway then makes an optional reference from it.")
    else:
        yaw = st.selectbox("Source yaw", list(range(0,360,45)), key=prefix+"-yaw")
    region = None
    if mode == "repair":
        st.caption("Repair rectangle, from 0 to 1 relative to image dimensions. Pixels outside it are preserved exactly by local compositing.")
        columns = st.columns(4)
        region = tuple(column.number_input(label, min_value=0.0, max_value=1.0, value=.25, step=.05,
                                          key=prefix+"-"+label) for column, label in zip(columns, ("Left", "Top", "Width", "Height")))
    prompt = st.text_area("Instructions", value="Improve detail and clean minor artifacts while preserving the approved layout.",
                          key=prefix+"-prompt", height=140)
    def failed(error):
        st.session_state[prefix+"-error"] = str(error)
        return True
    def start():
        from threading import Event
        st.session_state.pop(prefix+"-error", None)
        def success(value):
            st.session_state[prefix+"-result"] = value
        task = launch(lambda: generate(engine, lid, mode=mode, yaw=yaw, prompt=prompt, region=region),
            "Generating optional Runway reference", success=success, failure=failed, rerun_on_start=False,
            project_path=engine.path, service="runway", inline=True, queue_enabled=True, allow_parallel=True, stop_event=Event())
        if task:
            task.update(background_enhancement=prefix, stable_background_project=engine.project_id, asset_subject=lid)
    generation_button("Generate (Runway / gen4_image)", key=prefix+"-generate", on_click=start,
                      disabled=generation_busy() or not all_views_approved(engine, engine.load(), lid))
    if error := st.session_state.get(prefix+"-error"):
        render_error(error, project_path=engine.path, task_key="background:runway:"+lid, key=prefix+"-error-report")
    if saved := st.session_state.get(prefix+"-result"):
        st.success("Optional reference saved. Review it before using it in scene generation.")
        image_preview(str(engine.local_path(saved["asset"]["filePath"])))
        area = st.text_input("Scene reference target area / name", key=prefix+"-area", placeholder="e.g. reception desk")
        def use():
            from .background_enhancement import use_reference
            def success(value):
                st.session_state[prefix+"-result"] = value
            task = launch(lambda: use_reference(engine,lid,saved,area),"Saving optional scene reference",success=success,
                failure=failed,rerun_on_start=False,project_path=engine.path,service="local",inline=True,queue_enabled=True,allow_parallel=True)
            if task:
                task.update(background_enhancement=prefix,stable_background_project=engine.project_id,asset_subject=lid)
        st.button("Saved as scene reference" if saved["status"] == "approved_reference" else "Approve & use as scene reference",
                  key=prefix+"-use",disabled=saved["status"] == "approved_reference" or not area.strip() or generation_busy(),on_click=use)
        with st.expander("Generation details", expanded=False):
            st.json(saved)


def show(manager, target):
    from .assets import AssetPipeline
    enhancement_dialog(Deferred(lambda: AssetPipeline(manager, target["project_id"], demo=target["demo"])), target["lid"])
