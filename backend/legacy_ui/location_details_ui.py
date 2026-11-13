"""Optional area references stay separate from the two required masters."""
from .image_preview_ui import image_preview
import streamlit as st

from .background_assets_ui import launch_background
from .generation_ui import generation_button
from .ui_tasks import generation_busy,busy


def detail_activity(engine,lid,identity,status):
    from .ui_tasks import workspace_tasks
    tasks = list(workspace_tasks())+list(st.session_state.get("workspace-task-queue",[]))
    for task in tasks:
        if (task.get("stable_background_project") == engine.project_id
                and task.get("asset_subject") == lid+"__detail_"+identity
                and not task["future"].done()):
            return "queued" if task.get("phase") in ("queued","waiting") else "running"
    return "running" if status.get("status") == "running" else None


def camera_fields(spec,key,camera=None,*,engine=None,lid=None,identity=None):
    from .location_details import saved_camera,save_camera
    from .files import input_hash
    if engine is not None:
        camera = saved_camera(engine,lid,identity) or camera
    camera = camera or {}
    # New saved versions must supersede older widget/session values.
    key += "-saved-"+input_hash(camera)[:12]
    st.markdown("#### Camera setup")
    st.caption("Press inside the floor plan to place the red camera dot. Drag to aim the red fan and release to save. The fan's angle equals the field of view.")
    from .detail_camera_ui import render
    def changed_fov():
        if engine is None:
            return
        latest = saved_camera(engine,lid,identity) or camera
        if latest.get("floorPlanSetup"):
            try:
                save_camera(engine,lid,identity,latest["floorPlanSetup"],st.session_state[key+"-fov"])
            except (OSError,ValueError,RuntimeError) as exc:
                st.error("Camera setup could not be saved: "+str(exc))
    fov = st.number_input("Field of view (degrees)",min_value=20.,max_value=110.,value=float(camera.get("fov",70)),key=key+"-fov",on_change=changed_fov)
    setup = render(spec,key,camera or None,fov=fov,
        on_save=(lambda setup:save_camera(engine,lid,identity,setup,fov)) if engine is not None else None)
    st.caption("Camera position, direction, height, tilt and FOV save automatically on release and are restored when you return. Positive tilt looks up; negative tilt looks down.")
    if setup:
        if engine is not None:
            from .ui_panels import persistent_expander
            preview = persistent_expander("Camera framing preview",key=f"detail-camera-preview-{engine.project_id}-{lid}-{identity}")
            if preview.open:
                with preview:
                    from .detail_pose_guide import render as render_pose,VERSION
                    from .floor_plan_guides import resolved_plan
                    plan,kind = resolved_plan(spec)
                    digest = input_hash({"plan":plan.model_dump(mode="json"),"pose":setup,"fov":fov,"renderer":VERSION})
                    path = engine.path/"references"/"detail_camera_previews"/lid/identity/(digest+".png")
                    try:
                        if not path.is_file():
                            path.parent.mkdir(parents=True,exist_ok=True)
                            render_pose(spec,setup,fov,path)
                        image_preview(str(path),width=360)
                        st.caption("Local perspective preview, with no API charge. Match the framing, not the flat colors or solid proxy shapes. Missing object heights and schematic positions are approximate.")
                    except (OSError,ValueError) as exc:
                        st.warning("Camera preview unavailable: "+str(exc))
        from .detail_object_guidance import view_layout
        layout = view_layout(spec,setup,fov)
        with st.expander("Objects in this camera view",expanded=False):
            rows = [{"Object":item["name"],"Screen area":" / ".join(item["screenRegions"]),
                "Depth":item["depthBand"].replace("_"," "),"Framing":"Partial" if item["partiallyInFrame"] else "Inside FOV"}
                for item in layout["visibleObjects"]]
            if rows:
                st.dataframe(rows,hide_index=True,width="stretch")
            else:
                st.caption("No registered object footprints fall inside this horizontal FOV.")
            excluded = ", ".join(item["name"] for item in layout["excludedObjects"])
            if excluded:
                st.caption("Outside the view: "+excluded)
            st.caption("Calculated from the floor plan, without an AI call. The fan radius illustrates angle, not maximum viewing distance. This list checks horizontal coverage, not vertical visibility; height and tilt guide image generation. Walls and furniture may occlude objects.")
    return dict(camera_setup=setup,fov=fov)


def render(engine,lid,*,only=None,default_expanded=None,scope=""):
    from .location_details import current,generate,add_draft,generation_status,task_key
    from .error_reports import reports
    from .error_report_ui import render_error
    from .dual_panorama import is_key,pair_ready
    state = engine.load()
    spec = state.location_reference_sets[lid]
    ready = not is_key(spec) or pair_ready(engine,state,lid)
    prefix = f"location-details-{engine.project_id}-{lid}"
    if only is not None:
        prefix += "-only-"+only
    if scope:
        prefix += "-"+scope
    failures = {}
    for report in reports(engine.path,"location_detail:"+lid):
        failures.setdefault(report["task"],report)
    with st.container(border=only is None,key=prefix):
        if only is None:
            st.markdown("### Optional detail backgrounds")
            st.caption("Optional details need no separate approval and do not reopen background approval. Only generated, current detail images are available to Animations. Matching scenes use the detail as their primary background reference, with approved masters preserving room continuity.")
        if not ready:
            st.caption("Drafts can be added now. Approve all four directional masters before generating detail images.")
        if only is None:
            with st.expander("Add detail background",expanded=False):
                identity = st.text_input("Stable detail ID",key=prefix+"-id",placeholder="desk_area")
                area = st.text_input("Area name",key=prefix+"-area",placeholder="Desk area")
                description = st.text_area("Detail prompt",key=prefix+"-description",
                    placeholder="Close-up of the printer area viewed from the doorway; keep the same furniture and lighting as the masters.")
                st.caption("This saves a draft only. Configure its camera and generate the image in the created box below.")
                def save_draft():
                    try:
                        # Read the form itself: the scene-card loop below also binds
                        # identity, so capturing that variable can target the wrong draft.
                        add_draft(engine,lid,st.session_state[prefix+"-id"],
                            st.session_state[prefix+"-area"],st.session_state[prefix+"-description"])
                        for field in ("id","area","description"):
                            st.session_state[prefix+"-"+field] = ""
                    except (OSError,ValueError,RuntimeError) as exc:
                        st.error(str(exc))
                st.button("Add draft",key=prefix+"-add-draft",disabled=busy() or not identity.strip() or not area.strip(),on_click=save_draft)
        for identity,reference in spec.get("detail_references",{}).items():
            if only is not None and identity != only:
                continue
            usable = current(engine,state,lid,reference)
            from .location_labels import location_labels,location_label
            label = location_label(reference["key"],location_labels(engine.path))
            draft = reference.get("status") == "draft"
            status = generation_status(engine,lid,identity)
            activity = detail_activity(engine,lid,identity,status)
            card_key = f"background-card-detail-{engine.project_id}-{lid}-{identity}"
            if scope:
                card_key += "-"+scope
            from .ui_panels import persistent_expander,asset_title
            if scope:
                from .storyboard_asset_check import intact
                label = asset_title(label,intact(engine.path,{"file":reference.get("file"),"fileHash":reference.get("hash")}))
            panel = persistent_expander(label,expanded=draft if default_expanded is None else default_expanded,key=card_key)
            if not panel.open:
                continue
            with panel:
                from .location_detail_removal_ui import open_removal
                st.button("Remove detail",key=prefix+"-remove-"+identity,disabled=busy(),
                    on_click=lambda identity=identity,reference=reference:open_removal(engine,lid,identity,reference))
                st.caption(reference.get("area",identity))
                st.markdown(":orange[Draft — not generated]" if draft else ":green[Ready]" if usable else ":orange[Source changed or missing — regenerate / replace]")
                record = state.records.get(reference["key"])
                saved_camera = record.asset.generation_params.get("camera",{}) if record and record.asset else {}
                if record and record.asset:
                    saved_camera = {"yaw":float(reference.get("yaw",0)),**saved_camera}
                from .files import input_hash
                version = input_hash({"description":reference.get("description",""),"draft":reference.get("draftRevision"),
                    "asset":record.asset.asset_id if record and record.asset else None})[:12]
                prompt = st.text_area("Detail prompt",value=reference.get("description",""),key=prefix+"-prompt-"+identity+version,persist_state="session")
                if only is not None:
                    def save_detail_draft(identity=identity,reference=reference,prompt=prompt,card_key=card_key):
                        st.session_state[card_key] = True
                        launch_background(engine,lambda:add_draft(engine,lid,identity,reference.get("area",identity),prompt),
                            "Saving detail draft "+identity,service="local",subject=lid+"__detail_"+identity)
                    st.button("Save detail draft",key=prefix+"-save-draft-"+identity,
                        disabled=busy() or not prompt.strip() or prompt == reference.get("description",""),on_click=save_detail_draft)
                detail_camera = camera_fields(spec,prefix+"-camera-"+identity+version,saved_camera,
                    engine=engine,lid=lid,identity=identity)
                if detail_camera["camera_setup"] and record and record.asset:
                    from .detail_camera import validate
                    previous_pose = saved_camera.get("floorPlanSetup")
                    try:
                        same_camera = previous_pose and validate(spec,previous_pose) == detail_camera["camera_setup"] and saved_camera.get("fov",70) == detail_camera["fov"]
                    except ValueError:
                        same_camera = False
                    if not same_camera:
                        st.caption("Camera setup changed. Regenerate detail to apply the saved height, tilt and framing to the image.")
                def start_detail(identity=identity,reference=reference,prompt=prompt,camera=detail_camera,card_key=card_key):
                    st.session_state[card_key] = True
                    # Component commits can arrive immediately before this click.
                    # Snapshot the latest durable camera rather than an older
                    # render's captured pose/FOV.
                    from .location_details import saved_camera as load_camera
                    latest = load_camera(engine,lid,identity)
                    if latest and latest.get("floorPlanSetup"):
                        camera = {"camera_setup":latest["floorPlanSetup"],"fov":latest.get("fov",70)}
                    launch_background(engine,lambda:generate(engine,lid,identity,reference.get("area",identity),prompt,**camera),
                        "Generating detail background "+identity,service="openai",subject=lid+"__detail_"+identity)
                with st.container(horizontal=True):
                    generation_button("Regenerate detail" if record and record.asset else "Generate detail",key=prefix+"-regenerate-"+identity,
                        activity=activity,
                        disabled=generation_busy() or not ready or not prompt.strip() or not detail_camera["camera_setup"] or not is_key(spec),
                        on_click=start_detail)
                failure = failures.get(task_key(lid,identity))
                error = status.get("error") if status.get("status") == "failed" else None
                if (error or failure) and not activity:
                    message = error or failure["error"]["message"]
                    render_error(message,project_path=engine.path,task_key=task_key(lid,identity),
                        key=prefix+"-error-"+identity)
                    if "size" in message.lower() and ("400" in message or "invalid" in message.lower()):
                        st.caption("The provider rejected the image size. Retry now: requests use 1536×864 with dimensions divisible by 16. The saved image keeps its native resolution.")
                path = engine.local_path(reference["file"]) if reference.get("file") else None
                if activity:
                    from .background_assets_ui import loading_result
                    loading_result("Queuing..." if activity == "queued" else "Generating...")
                elif path and path.is_file():
                    if draft:
                        st.caption("Previous generated image; generate this draft to apply its changes.")
                    image_preview(str(path),width=360)
                    from .detail_zoom_ui import open_zoom
                    st.button("Zoom",key=prefix+"-zoom-"+identity,disabled=not usable or not ready or generation_busy(),
                        help="Select an area and regenerate it as a detailed close-up.",
                        on_click=lambda identity=identity,reference=reference:open_zoom(engine,lid,identity,reference))
                    dimensions = record.asset.generation_params.get("outputSize") if record and record.asset else None
                    if dimensions:
                        st.caption(f"Resolution: {dimensions[0]} × {dimensions[1]}")
                elif reference.get("file"):
                    st.warning("The saved detail image is missing. Regenerate this detail to restore its image.")
                elif not error and not failure and status.get("status") != "running":
                    st.caption("No image generated yet. Generate this draft to display its image here.")
                if not ready:
                    st.caption("Generate is unavailable: approve all four directional masters first.")
                elif not prompt.strip():
                    st.caption("Generate is unavailable: enter a detail prompt.")
                elif not detail_camera["camera_setup"]:
                    st.caption("Generate is unavailable: place the camera on the floor plan, drag to aim, and release.")
                with st.expander("Reference metadata",expanded=False):
                    st.json(reference)
                if record and record.asset and (pose := record.asset.generation_params.get("perspectiveGuide")):
                    from .ui_panels import persistent_expander
                    guide_panel = persistent_expander("Camera framing used for this image",key=card_key+"-pose-guide")
                    if guide_panel.open:
                        with guide_panel:
                            guide_path = engine.local_path(pose["file"])
                            if guide_path.is_file():
                                image_preview(str(guide_path),width=360)
                            camera = pose["camera"]
                            st.caption(f"Height {camera['position']['heightMeters']:g} m · tilt {camera['pitchDegrees']:g}° · FOV {camera['horizontalFovDegrees']:g}°. Geometry guide only; object details and inferred heights are approximate.")
    target = st.session_state.get("remove-location-detail-target")
    if target and target["project_id"] == engine.project_id and target["lid"] == lid:
        from .location_detail_removal_ui import removal_dialog
        removal_dialog(engine,target)
    target = st.session_state.get("detail-zoom-target")
    if target and target["project_id"] == engine.project_id and target["lid"] == lid:
        from .detail_zoom_ui import zoom_dialog
        zoom_dialog(engine,target)
