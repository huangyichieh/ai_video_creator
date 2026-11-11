"""Shared room build/review and exact-angle preview controls."""
from .image_preview_ui import image_preview
import json

import streamlit as st

from .generation_ui import generation_button
from .ui_tasks import busy,generation_busy,asset_activity


def method_switch(engine,lid,spec):
    from .shared_room import enabled
    from .background_assets_ui import launch_background
    from .background_library import BackgroundLibrary
    if enabled(spec):
        st.caption("DETAIL: shared 3D room → panorama → 8 canonical renders → AI enhancement → reference pack → shot backgrounds.")
        st.button("Use legacy two-master workflow",key="use-legacy-room-"+lid,disabled=busy(),on_click=lambda:
            launch_background(engine,lambda:BackgroundLibrary(engine).configure_references(lid,spec["reference_type"],
                spec.get("floor_plan",""),spec.get("object_layout",{}),generation_method="dual_panorama"),
                "Switching to legacy two-master workflow",service="local",subject=lid))
        return
    st.button("Use shared 3D room workflow",key="use-shared-room-"+lid,disabled=busy(),on_click=lambda:
        launch_background(engine,lambda:BackgroundLibrary(engine).configure_references(lid,spec.get("reference_type","KEY_360"),
            spec.get("floor_plan",""),spec.get("object_layout",{}),generation_method="shared_3d"),
            "Switching location to shared 3D",service="local",subject=lid))
    st.caption("Switching retains old panorama revisions in history and reopens this background's approval.")


def outputs(engine,lid):
    from .shared_room import current,perspective_keys
    from .room_geometry import scene_from_spec
    from .background_library import BackgroundLibrary
    from .background_assets_ui import launch_background,loading_result
    from .error_report_ui import render_error
    state = engine.load()
    spec = state.location_reference_sets[lid]
    subject = lid+"__room_panorama"
    record = state.records.get("backgrounds:"+subject)
    source = current(engine,state,lid)
    try:
        scene_from_spec(spec)
        missing = None
    except ValueError as exc:
        missing = str(exc)
    prefix = f"location-reference-set-{engine.project_id}-{lid}"
    try:
        draft_dirty = (st.session_state.get(prefix+"-floor",spec.get("floor_plan","")) != spec.get("floor_plan","") or
            json.loads(st.session_state.get(prefix+"-layout",json.dumps(spec.get("object_layout",{})))) != spec.get("object_layout",{}))
    except ValueError:
        draft_dirty = True
    st.markdown("**Shared 3D room & 360° panorama**")
    st.caption("Local CPU blockout rendering defines spatial truth. The 360° panorama provides geometry/context; eight enhanced canonical images define visual appearance.")
    generation_button("Build 3D room & render panorama",key="render-shared-room-"+lid,
        disabled=generation_busy() or bool(missing) or draft_dirty,activity=asset_activity(engine,"backgrounds",subject),on_click=lambda:
        launch_background(engine,lambda:engine.generate("backgrounds",subject=subject,force=True),
                          "Generating shared 3D panorama",service="local",subject=lid))
    if missing:
        st.info(missing+" Use Check reference / Generate floor plan image to prepare spatial data.")
    elif draft_dirty:
        st.info("Save the edited reference settings before building the room.")
    if asset_activity(engine,"backgrounds",subject) == "running":
        loading_result("Rendering shared 3D room…")
    elif source:
        image_preview(str(engine.local_path(source.asset.file_path)),caption="One rendered 360° × 180° panorama",width=700)
        with st.expander("Look around the rendered panorama",expanded=False):
            from .panorama_viewer_ui import panorama_viewer
            panorama_viewer(engine.local_path(source.asset.file_path))
        details = source.asset.generation_params["sharedRoom"]
        st.download_button("Download 3D scene JSON",data=engine.local_path(details["sceneFile"]).read_bytes(),
                           file_name=lid+"-scene.json",mime="application/json",key="download-room-scene-"+lid)
        if details.get("inferredHeights"):
            st.caption("Default object heights used for: "+", ".join(details["inferredHeights"])+". Adjust the 3D geometry below if needed.")
    if record and record.error:
        render_error(record.error,project_path=engine.path,task_key="assets:backgrounds:"+subject,key="room-error-"+lid)
    from .background_revisions_ui import actions
    actions(engine,subject,allow_generate=False)
    approved = BackgroundLibrary(engine).approved(lid,state)
    from .room_canonical_ui import outputs as canonical_outputs
    canonical_outputs(engine,lid)
    if spec.get("camera_plan"):
        with st.expander("3D geometry: dimensions, object heights & materials",expanded=False):
            from .files import input_hash
            from .floor_plan_guides import CameraPlan
            key = f"room-geometry-{engine.project_id}-{lid}-"+input_hash(spec["camera_plan"])[:12]
            normalized = CameraPlan.model_validate(spec["camera_plan"]).model_dump(mode="json")
            st.session_state.setdefault(key,json.dumps(normalized,ensure_ascii=False,indent=2))
            st.caption("Coordinates are metres: x east, y north, z up. Object footprints, height_m, elevation_m and material_color define the blockout. Walls/floor/ceiling use the room colors. Saving changes requires rendering and approving again.")
            st.text_area("3D geometry JSON",key=key,height=260,disabled=busy())
            def save_geometry():
                from .floor_plan_guides import CameraPlan
                try:
                    plan = CameraPlan.model_validate_json(st.session_state[key])
                    scene_from_spec({**spec,"camera_plan":plan.model_dump(mode="json")})
                except ValueError as exc:
                    st.session_state[key+"-error"] = str(exc)
                    return
                st.session_state.pop(key+"-error",None)
                st.session_state[prefix+"-camera-plan"] = plan.model_dump(mode="json")
                st.session_state.pop(prefix+"-guides",None)
                launch_background(engine,lambda:BackgroundLibrary(engine).configure_references(lid,spec["reference_type"],
                    spec["floor_plan"],spec["object_layout"],camera_plan=plan.model_dump(mode="json"),generation_method="shared_3d"),
                    "Saving shared 3D geometry",service="local",subject=lid)
            st.button("Save 3D geometry",key=key+"-save",disabled=busy(),on_click=save_geometry)
            if st.session_state.get(key+"-error"):
                st.error(st.session_state[key+"-error"])
    with st.expander("Shot-specific perspective preview",expanded=False):
        st.caption("Shot references are generated automatically at the requested yaw, pitch and FOV after approval. Perspective views share the panorama's fixed camera center; camera translation requires another 3D render.")
        columns = st.columns(3)
        yaw = columns[0].number_input("Yaw",min_value=-360.0,max_value=360.0,value=0.0,key="room-yaw-"+lid)
        pitch = columns[1].number_input("Pitch",min_value=-89.0,max_value=89.0,value=0.0,key="room-pitch-"+lid)
        fov = columns[2].number_input("FOV",min_value=1.0,max_value=169.0,value=70.0,key="room-fov-"+lid)
        result_key = "room-preview-result-"+lid
        def preview():
            def run():
                latest = engine.load()
                keys = perspective_keys(engine,latest,lid,yaw,yaw,pitch=pitch,fov=fov)
                if not keys:
                    raise ValueError("Approve the current rendered room first.")
                asset = latest.records[keys[0]].asset
                return {"file":asset.file_path,"sourceAssetId":asset.generation_params["roomProjection"]["sourceAssetId"],
                        "yaw":yaw,"pitch":pitch,"fov":fov}
            from .ui_tasks import launch
            job = launch(run,"Generating shot perspective",success=lambda path:st.session_state.update({result_key:path}),
                   rerun_on_start=False,service="local",project_path=engine.path)
            if job:
                job.update(stable_background_project=engine.project_id,asset_subject=lid)
        generation_button("Generate shot preview",key="project-room-preview-"+lid,disabled=busy() or not approved,on_click=preview)
        result = st.session_state.get(result_key)
        if result and source and isinstance(result,dict) and result.get("sourceAssetId") == source.asset.asset_id:
            image_preview(str(engine.local_path(result["file"])),width=700,
                     caption=f"Yaw {result['yaw']:g}° · pitch {result['pitch']:g}° · FOV {result['fov']:g}°")
        elif result:
            st.caption("The source panorama changed. Generate a new shot preview.")
