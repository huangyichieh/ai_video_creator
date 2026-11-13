"""Optional local visual detail; panorama masters always own the spatial layout."""
import json
import math
import re
from uuid import uuid4

from .files import input_hash

DETAIL_IMAGE_SIZE = "1536x864"


def validate_image_size(size,*,demo=False):
    if not demo and (size[0] < 1280 or size[1] < 720):
        raise ValueError(f"The provider returned a {size[0]}×{size[1]} detail image, below the 1280×720 minimum. Retry generation; the image was not enlarged or applied.")


def identifier(lid,area,value=None):
    if value is not None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",value):
            raise ValueError("Detail ID must start with a lowercase letter and use up to 64 lowercase letters, numbers or underscores.")
        return value
    slug = re.sub(r"[^a-z0-9]+","_",area.lower()).strip("_")
    return slug if re.fullmatch(r"[a-z][a-z0-9_]{0,63}",slug) else "detail_"+input_hash([lid,area])[:16]


def layout_hash(spec):
    return input_hash({key:spec.get(key) for key in ("floor_plan","object_layout","camera_plan")})


def object_ids(engine,lid,spec):
    from .floor_plan_guides import current_guides
    guides = current_guides(engine,lid,spec)
    return guides["objectIds"] if guides else {name:name for name in spec.get("object_layout",{})}


def validate_links(engine,lid,spec,links):
    known = object_ids(engine,lid,spec)
    result = []
    for value in links:
        identity = known.get(value,value)
        if identity not in known.values():
            raise ValueError("Link details only to objects registered in this location's layout.")
        if identity not in result:
            result.append(identity)
    return result


def current(engine,state,lid,reference):
    from .location_references import _detail_current
    if reference.get("status") == "draft":
        return False
    record = state.records.get(reference.get("key"))
    return bool(record and record.asset and engine._intact(record) and record.status == "succeeded"
        and reference.get("hash") == record.file_hash and _detail_current(state,reference)
        and (not reference.get("layoutHash") or reference["layoutHash"] == layout_hash(state.location_reference_sets[lid])))


def _tokens(value):
    return " ".join(re.sub(r"[_\W]+"," ",str(value).casefold()).split())


def relevance(spec,reference,identity,*,target_area="",character_position=None,interaction_target="",ids=None):
    """Rank explicit interaction first, named focus second, then local proximity."""
    ids = ids or {}
    links = reference.get("objectIds",[])
    terms = [identity,reference.get("area",identity),*links,
             *(name for name,object_id in ids.items() if object_id in links)]
    normalized = [_tokens(value) for value in terms if value]
    score = 0
    for query,points in ((interaction_target,100),(target_area,80),
                         (character_position if isinstance(character_position,str) else "",60)):
        text = _tokens(query)
        if text and any(term and (f" {term} " in f" {text} " or f" {text} " in f" {term} ") for term in normalized):
            score = max(score,points)
    if isinstance(character_position,dict):
        try:
            x,y = float(character_position["x"]),float(character_position["y"])
            if not all(math.isfinite(v) for v in (x,y)):
                return score
        except (KeyError,ValueError,TypeError):
            return score
        centers = []
        if reference.get("position"):
            centers.append(reference["position"])
        from .floor_plan_guides import CameraPlan
        if spec.get("camera_plan"):
            plan = CameraPlan.model_validate(spec["camera_plan"])
            for item in plan.objects:
                if item.source_name in links or ids.get(item.source_name) in links or item.object_id in links:
                    centers.append({"x":sum(p.x for p in item.footprint)/len(item.footprint),
                                    "y":sum(p.y for p in item.footprint)/len(item.footprint)})
        for center in centers:
            distance = math.hypot(x-center["x"],y-center["y"])
            if distance <= 2:
                score = max(score,40-distance)
    return score


def add_draft(engine,lid,identity,area,description=""):
    """Register editable metadata only; retain any previous generated revision."""
    from .background_library import BackgroundLibrary
    from .locking import project_lock
    BackgroundLibrary(engine)._root()
    identity = identifier(lid,area,identity.strip())
    if not area.strip():
        raise ValueError("Enter an area name for the detail draft.")
    with project_lock(engine.path):
        state = engine.load()
        if lid not in state.location_reference_sets:
            raise ValueError("Choose an existing background location.")
        references = state.location_reference_sets[lid].setdefault("detail_references",{})
        previous = references.get(identity,{})
        references[identity] = {**previous,"id":identity,"area":area.strip(),"description":description.strip(),
            "key":f"backgrounds:{lid}__detail_{identity}","role":"local_detail_only","status":"draft",
            "draftRevision":previous.get("draftRevision",0)+1}
        spec = state.location_reference_sets[lid]
        if identity in spec.get("removed_detail_ids",[]):
            spec["removed_detail_ids"].remove(identity)
        engine._save(state)
        return state


def task_key(lid,identity):
    return "location_detail:"+lid+":"+identity


def saved_camera(engine,lid,identity):
    from .file_cache import read_json
    file = engine.path/"detail_cameras.json"
    return read_json(file).get(task_key(lid,identity),{}).get("camera") if file.exists() else None


def save_camera(engine,lid,identity,setup,fov=70):
    """Persist draft camera settings independently of image revisions and sessions."""
    from .background_library import BackgroundLibrary
    from .detail_camera import validate
    from .file_cache import read_json
    from .files import atomic_write
    from .locking import project_lock
    from .models import utc_now
    BackgroundLibrary(engine)._root()
    if not math.isfinite(fov) or not 20 <= fov <= 110:
        raise ValueError("Use a field of view between 20 and 110 degrees.")
    with project_lock(engine.path):
        state = engine.load()
        spec = state.location_reference_sets[lid]
        if identity not in spec.get("detail_references",{}):
            raise ValueError("This detail background no longer exists.")
        normalized = validate(spec,setup)
        camera = {"floorPlanSetup":normalized,"yaw":normalized["yaw"],"pitch":normalized["pitch"],"heightMeters":normalized["heightMeters"],"fov":fov}
        file = engine.path/"detail_cameras.json"
        data = read_json(file) if file.exists() else {}
        data[task_key(lid,identity)] = {"camera":camera,"layoutHash":layout_hash(spec),"updatedAt":utc_now().isoformat()}
        atomic_write(file,json.dumps(data,ensure_ascii=False,indent=2))
    return camera


def generation_status(engine,lid,identity):
    from .file_cache import read_json
    file = engine.path/"detail_generation.json"
    return read_json(file).get(task_key(lid,identity),{}) if file.exists() else {}


def _generation_status(engine,lid,identity,status,**details):
    from .file_cache import read_json
    from .locking import project_lock
    from .files import atomic_write
    from .models import utc_now
    with project_lock(engine.path):
        file = engine.path/"detail_generation.json"
        data = read_json(file) if file.exists() else {}
        data[task_key(lid,identity)] = {"status":status,"updatedAt":utc_now().isoformat(),**details}
        atomic_write(file,json.dumps(data,ensure_ascii=False,indent=2))


def generate(engine,lid,identity,area,description,links=(),**camera):
    """Retain task-local status and errors through validation, API and file commit."""
    from .error_reports import record_failure,clear_reports,redact
    key = task_key(lid,identity)
    _generation_status(engine,lid,identity,"running")
    try:
        state = _generate(engine,lid,identity,area,description,links,**camera)
    except Exception as exc:
        if getattr(exc,"error_report_task",None) != key:
            record_failure(engine.path,key,exc)
        _generation_status(engine,lid,identity,"failed",error=redact(str(exc)))
        raise
    else:
        reference = state.location_reference_sets[lid]["detail_references"][identity]
        _generation_status(engine,lid,identity,"succeeded",file=reference["file"])
        clear_reports(engine.path,key)
        return state


def _generate(engine,lid,identity,area,description,links=(),*,yaw=0,pitch=0,fov=70,camera_position="",camera_setup=None,zoom=None):
    if zoom is not None:
        from .detail_zoom import generate
        return generate(engine,lid,identity,zoom)
    from .background_library import BackgroundLibrary
    from .dual_panorama import MASTERS,PANORAMA_MODEL,pair_ready
    from .models import ProviderConfig
    from .floor_plan_guides import require_guides
    from .style_rules import style_reference
    from .library_media import LibraryMedia
    library = BackgroundLibrary(engine)
    library._root()
    identity = identifier(lid,area,identity)
    state = engine.load()
    spec = state.location_reference_sets[lid]
    links = validate_links(engine,lid,spec,links)
    if not pair_ready(engine,state,lid):
        raise ValueError("Approve all four directional masters before generating details.")
    setup = None
    if camera_setup is not None:
        from .detail_camera import validate
        setup = validate(spec,{**camera_setup,"pitch":camera_setup.get("pitch",pitch)})
        yaw = setup["yaw"]
        pitch = setup["pitch"]
        camera_position = (f"Floor plan x={setup['position']['x']}, y={setup['position']['y']}; "
            f"camera height {setup['heightMeters']} m. Coordinates: x=east, y=north. "
            + ("Schematic coordinates, not measured distances." if setup["geometrySource"] == "schematic" else "Metric coordinates in metres."))
    if not all(math.isfinite(value) for value in (yaw,pitch,fov)) or not -60 <= pitch <= 60 or not 20 <= fov <= 110:
        raise ValueError("Use a finite camera yaw, pitch between -60 and 60, and FOV between 20 and 110 degrees.")
    yaw = yaw % 360
    camera = {"yaw":yaw,"pitch":pitch,"fov":fov,
        "positionDescription":camera_position.strip() or "Same camera center and height as the masters"}
    if setup:
        camera["floorPlanSetup"] = setup
        camera["heightMeters"] = setup["heightMeters"]
    path = engine.path/"references"/"detail_candidates"/uuid4().hex/"image.png"
    path.parent.mkdir(parents=True,exist_ok=True)
    from .directional_masters import select,LABELS
    selected_masters = select(yaw,fov)
    name = selected_masters[0]
    record = state.records[f"backgrounds:{lid}__{name}"]
    guides = require_guides(engine,lid,spec)
    guide = guides["views"][name]
    refs = [engine.local_path(guide["cameraGuide"]),engine.local_path(record.asset.file_path)]
    object_view = None
    pose_guide = None
    if setup:
        from .detail_camera import guide as draw_guide
        from .detail_object_guidance import view_layout
        from .files import atomic_write
        object_view = view_layout(spec,setup,fov,guides.get("objectIds"))
        refs[0] = draw_guide(spec,setup,path.with_name("camera_setup.png"),fov,object_view=object_view)
        from .detail_pose_guide import render as render_pose
        pose_guide = render_pose(spec,setup,fov,path.with_name("camera_perspective.png"))
        refs.insert(0,path.with_name("camera_perspective.png"))
        object_view["poseVisibility"] = pose_guide["objectVisibility"]
        object_view["verticalVisibility"] = "approximate_solid_blockout_with_occlusion"
        atomic_write(path.with_name("camera_objects.json"),json.dumps(object_view,ensure_ascii=False,indent=2))
    for other in selected_masters[1:]:
        refs.append(engine.local_path(state.records[f"backgrounds:{lid}__{other}"].asset.file_path))
    content_count = len(refs)
    sources = {f"backgrounds:{lid}__{n}":[state.records[f"backgrounds:{lid}__{n}"].asset.asset_id,
        state.records[f"backgrounds:{lid}__{n}"].file_hash] for n in MASTERS}
    prompt = ("Create ONE finished, scene-ready local background plate suitable for generating production scene images, not a new room design. "
        "Render a complete coherent perspective view with clean edges, detailed surfaces and usable space for later character placement. "
        "No collage, reference sheet, diagram, camera-guide markings or unfinished regions. "
        + ("Image 1 is the exact perspective camera blockout; Image 2 is the top-down floor-plan and side-view pose diagram. " if setup else "Image 1 is the floor-plan camera guide. ")
        + " ".join(f"Image {index+(3 if setup else 2)} is the approved {LABELS[master]} directional master." for index,master in enumerate(selected_masters))+
        " These masters describe the SAME room and have equal authority for existing object identity, materials, lighting and physical layout ONLY. "
        "No master defines this output's framing, viewing direction or screen-space object placement. "
        "Create a normal rectilinear perspective view using the requested camera, not a panorama or a copy of the master's framing. "
        + ("The saved camera pose is mandatory: do not choose a different position, height, yaw, pitch or field of view for a more attractive composition. " if setup else
           "A different viewpoint, close-up, pitch, lens or camera position is allowed; move only the camera, never the room's objects. ")
        + ("COMPOSITION PRIORITY: image 1 and the calculated camera/object placement below override any panorama framing. "
           "Image 1 already projects the requested height and pitch. In image 2, the red dot is the requested camera position; the red fan shows its field of view, with the viewing direction along the fan's center. "
           "Render from this exact position and direction, not the master's camera. The guide is top-down planning data, never part of the output. " if setup else
           "Image 1 describes a master's camera region for layout context only; the requested camera below defines the output composition. ") +
        "Reproject consistent world-space geometry and show only objects visible from the requested viewpoint. "
        "Focus on this area without changing the room geometry or moving, resizing, duplicating or removing fixed objects. "
        "Preserve materials, lighting, object identity and world-space placement. "
        f"Any additional images after image {content_count} are STYLE ONLY; never copy their objects or room layout. "
        f"Output one high-quality {DETAIL_IMAGE_SIZE} landscape scene image. No people, reflections, labels or visible text. "
        +json.dumps({"detailId":identity,"area":area,"description":description,"linkedObjectIds":links,
            **({"floorPlan":spec.get("floor_plan","")} if object_view is None else {}),"objectLayout":{key:value for key,value in spec.get("object_layout",{}).items()
                if object_view is None or key in {item["name"] for item in object_view["visibleObjects"]}},
            "cameraYaw":yaw,"camera":camera,"viewObjectLayout":object_view,"styleRules":state.project_style_rules},ensure_ascii=False))
    from .detail_object_guidance import camera_orientation
    prompt += "\n"+camera_orientation(yaw)
    if object_view:
        from .detail_object_guidance import instructions
        prompt += "\n"+instructions(object_view)
    prompt,refs = style_reference(engine,state,prompt,refs)
    from .background_constraints import EMPTY_BACKGROUND
    prompt += "\n"+EMPTY_BACKGROUND
    projection = None
    if setup:
        from .detail_camera import perspective_camera
        projection = perspective_camera(spec,setup,fov,tuple(map(int,DETAIL_IMAGE_SIZE.split("x"))))
        from .detail_pose_guide import instructions as pose_instructions
        prompt = pose_instructions(pose_guide)+"\n"+prompt
        prompt += (f"\nCAMERA POSE: height above floor {setup['heightMeters']:g} metres, tilt/pitch {pitch:g} degrees "
            "(positive tilts upward, negative downward), roll 0. Use this selected height and tilt instead of the masters' eye height. "
            "The lower side-view guide shows the camera height and optical-axis tilt; it is planning data, not part of the rendered room. "
            "Change only the viewpoint; preserve object heights, orientations, positions and gravity.")
        prompt += ("\nFINAL PERSPECTIVE CAMERA CONTRACT: create one normal rectilinear perspective photograph/frame from the EXACT camera below. "
            "The floor-plan position, height, yaw, tilt and FOV override the master framing, style reference composition and any conflicting detail description. "
            "Use the masters to identify the same room, materials and objects, not as images to crop, flatten or copy. "
            "No panorama, equirectangular/fisheye projection, orthographic view, collage or top-down planning diagram. "
            "Apply pitch as an optical-axis rotation in 3D, not by rotating the picture or tilting furniture. Keep roll zero and gravity fixed. "
            "At zero pitch the horizon is centered; upward pitch lowers the horizon and reveals more upper room/ceiling, downward pitch raises it and reveals more floor/top surfaces. "
            "Keep the registered object fronts and positions fixed while their perspective changes with this camera. Render an empty room, no people, arrows or labels. "
            +json.dumps(projection,ensure_ascii=False))
    config = ProviderConfig(provider="demo" if engine.demo else "openai",model=PANORAMA_MODEL,
        # Native 16:9; both dimensions meet the provider's multiple-of-16 rule.
        options={"output_path":str(path),"size":DETAIL_IMAGE_SIZE})
    media = LibraryMedia(engine.manager,engine.project_id)
    provider = engine.panorama_image_provider
    asset = media._call(provider,provider.generate,(prompt,refs),config,"location_detail:"+lid+":"+identity)
    from PIL import Image
    with Image.open(path) as image:
        source_size = image.size
        validate_image_size(source_size,demo=engine.demo)
        normalized = image.convert("RGB")
    normalized.save(path,format="PNG")
    return library.add_detail_reference(lid,area,path.read_bytes(),detail_id=identity,object_ids=links,
        description=description,source_yaw=yaw,source_assets=sources,generation={
            "provider":asset.provider,"model":asset.model,"cost":asset.cost,"prompt":prompt,
            "inputImages":[p.relative_to(engine.path).as_posix() for p in refs],"camera":camera,
            "compositionAuthority":"saved_camera_guide" if setup else "requested_camera",
            **({"perspectiveGuide":{**pose_guide,"file":path.with_name("camera_perspective.png").relative_to(engine.path).as_posix()}} if pose_guide else {}),
            **({"cameraProjection":projection} if projection else {}),
            "inputImageRoles":[*(["perspective_pose_guide"] if setup else []),"camera_guide",*(master+"_appearance_only" for master in selected_masters)]
                + (["style_only"] if len(refs) > content_count else []),
            "requestedSize":DETAIL_IMAGE_SIZE,"providerRequestSize":DETAIL_IMAGE_SIZE,"outputSize":list(source_size),"providerImageSize":list(source_size),
            **({"viewObjectLayout":object_view,"objectGuidanceFile":path.with_name("camera_objects.json").relative_to(engine.path).as_posix()} if object_view else {}),
            "sceneReadyBackground":True,"role":"local_detail_only"})


def remove(engine,lid,identity,*,confirmation,expected=None):
    from .locking import project_lock
    from .background_library import BackgroundLibrary
    BackgroundLibrary(engine)._root()
    if confirmation != identity:
        raise ValueError("Type the exact detail ID to delete this detail background.")
    with project_lock(engine.path):
        state = engine.load()
        reference = state.location_reference_sets[lid].get("detail_references",{}).get(identity)
        if reference is None:
            raise ValueError("This detail background no longer exists.")
        if expected is not None and input_hash(reference) != expected:
            raise ValueError("This detail changed. Close and reopen the deletion dialog.")
        state.location_reference_sets[lid].setdefault("detail_references",{}).pop(identity,None)
        # A storyboard can still request this ID. Do not recreate an explicitly
        # deleted draft during the next automatic Assets-page synchronization.
        removed = state.location_reference_sets[lid].setdefault("removed_detail_ids",[])
        if identity not in removed:
            removed.append(identity)
        from .file_cache import read_json
        from .files import atomic_write
        cameras_file = engine.path/"detail_cameras.json"
        if cameras_file.exists():
            cameras = read_json(cameras_file)
            cameras.pop(task_key(lid,identity),None)
            atomic_write(cameras_file,json.dumps(cameras,ensure_ascii=False,indent=2))
        engine._save(state)
        return engine._refresh()[0]
