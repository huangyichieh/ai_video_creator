"""Four overlapping directional masters from the saved spatial layout."""
from .image_preview_ui import image_preview
import streamlit as st

from .dual_panorama import MASTERS, PANORAMA_MODEL
from .ui_tasks import asset_activity
from .directional_masters import LABELS


def settings(engine, state, lid):
    from .background_assets_ui import reference_set_settings
    from .background_library import BackgroundLibrary
    reference_set_settings(engine, BackgroundLibrary(engine), lid, state)


def _image(engine, state, lid, name):
    from .background_assets_ui import background_image_title, loading_result
    from .error_report_ui import render_error
    subject = lid+"__"+name
    record = state.records.get("backgrounds:"+subject)
    title = {"east_bridge":"East Bridge","west_bridge":"West Bridge","front_panorama":"Master / North",
             "back_panorama":"Master / South","east_panorama":"Master / East","west_panorama":"Master / West"}.get(name,name.replace("view_", "Yaw ")+"°")
    background_image_title(engine, title, record, subject)
    from .location_bridges import CAMERAS
    if name in CAMERAS:
        yaw,fov = CAMERAS[name]
        st.caption(f"Horizontal FOV: {fov}° · Center yaw: {yaw}° · Coverage: {yaw-fov/2:g}° to {yaw+fov/2:g}° · Vertical coverage: 180°")
        from .master_prefill import SIZES
        size = SIZES["bridges" if name.endswith("bridge") else "masters"]
        st.caption(f"Target resolution: {size[0]} × {size[1]}")
    else:
        from .dual_panorama import FOV
        st.caption(f"Horizontal FOV: {FOV}° · Pitch: 0°")
    activity = asset_activity(engine, "backgrounds", subject)
    status = "Generating…" if activity == "running" else "Queuing…" if activity == "queued" else record.status if record else "pending"
    st.caption(f"{status} · revision {record.revision if record else 0}")
    if activity == "running":
        loading_result("Generating…")
    elif record and record.asset and engine._intact(record):
        image_preview(str(engine.local_path(record.asset.file_path)))
    if record and record.error:
        render_error(record.error, project_path=engine.path, task_key="assets:backgrounds:"+subject, key="location-error-"+subject)
    if name in MASTERS:
        import json
        from .floor_plan_guides import current_guides
        from .generation_ui import generation_button
        from .ui_tasks import generation_busy
        from .background_assets_ui import launch_background
        from .location_pairs import regenerate_one
        spec = state.location_reference_sets[lid]
        prefix = f"location-reference-set-{engine.project_id}-{lid}"
        try:
            ready = (bool(current_guides(engine,lid,spec))
                and st.session_state.get(prefix+"-floor",spec.get("floor_plan","")) == spec.get("floor_plan","")
                and json.loads(st.session_state.get(prefix+"-layout",json.dumps(spec.get("object_layout",{})))) == spec.get("object_layout",{}))
        except (TypeError,ValueError):
            ready = False
        generation_button("Regenerate" if record and record.asset else "Generate",key=f"regenerate-master-{engine.project_id}-{subject}",
            activity=activity,disabled=generation_busy() or not ready,
            on_click=lambda:launch_background(engine,lambda:regenerate_one(engine,lid,name),
                "Generating "+LABELS[name]+" master",service="openai",subject=subject))


def outputs(engine, lid):
    from .location_pairs_ui import controls as pair_controls
    state = engine.load()
    with st.container(border=True,key=f"background-master-images-{engine.project_id}-{lid}"):
        st.markdown("### Master images")
        st.caption("Floor plan + object layout → four camera regions → North / East / South / West masters. Review all four overlaps, then approve the set.")
        columns = st.columns(2,vertical_alignment="top")
        for index,name in enumerate(MASTERS):
            column = columns[index % 2]
            with column:
                _image(engine,state,lid,name)
                st.caption("OpenAI / "+PANORAMA_MODEL+" | "+LABELS[name])
        pair_controls(engine,lid,"masters")
