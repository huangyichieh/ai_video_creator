"""Approved east/west visual anchors shared by both master panoramas."""
import json

BRIDGES = ("east_bridge","west_bridge")
CAMERAS = {"front_panorama":(0,110),"east_panorama":(90,110),"back_panorama":(180,110),"west_panorama":(270,110),
           "east_bridge":(90,60),"west_bridge":(270,60)}


def context(spec,name,notes=""):
    from .floor_plan_guides import resolved_plan,visible_names
    from .location_view_context import object_bearings,visible,scoped_text
    plan,kind = resolved_plan(spec)
    names = visible_names(spec,name)
    if names is None:
        names = {key for key,value in spec.get("object_layout",{}).items() if
                 object_bearings(value,key) and visible(object_bearings(value,key),name)}
    hidden = [key for key in spec.get("object_layout",{}) if key not in names]
    hidden += [item.object_id for item in plan.objects if item.source_name not in names and item.object_id]
    return {"cameraYaw":CAMERAS[name][0],"horizontalFov":CAMERAS[name][1],
        "floorPlan":scoped_text(spec.get("floor_plan",""),name,excluded=hidden),
        "visibleObjects":{key:value for key,value in spec.get("object_layout",{}).items() if key in names},
        "objectGeometry":{item.source_name:item.model_dump(mode="json") for item in plan.objects if item.source_name in names},
        "camera":plan.camera.model_dump(),"cameraHeight":plan.camera_height_m,
        "appearance":scoped_text(notes,name,excluded=hidden),"geometrySource":kind}


def prompt(name):
    yaw,fov = CAMERAS[name]
    return (f"Create ONE {fov}-degree equirectangular sector visual anchor facing {'EAST' if yaw == 90 else 'WEST'}, "
        f"yaw {yaw-fov/2:g}..{yaw+fov/2:g}, centered yaw {yaw}. Latitude +90..-90, horizon at half height. "
        "Same saved camera center, eye height and lighting as all later masters. This is a fixed visual anchor for "
        "the shared side region, not a new room design. Preserve every visible persistent object, wall, window, "
        "furniture footprint, scale, material and light source from the floor plan and stable object IDs. "
        "Render only objects in this camera region. One ID means one physical object; never duplicate it. "
        "The central yaw +/-10 degrees will anchor BOTH north and south master overlaps. Show natural continuation "
        "of any crossing object beyond this central region. Style references control style only, never objects/layout. "
        "No people, reflections, labels, arrows or visible text.")


def require_approved(engine,state,lid):
    from .dual_panorama import approved
    missing = [name for name in BRIDGES if not approved(engine,state,lid,name)]
    if missing:
        from .pipeline import ApprovalRequired
        raise ApprovalRequired("Generate and approve East Bridge and West Bridge before generating either master image.")


def request(engine,state,lid,name,spec):
    from .floor_plan_guides import require_guides
    from .style_rules import style_reference
    from .background_library import locations
    brief = next((location.description for location in locations(engine.creative) if location.location_id == lid),"")
    guide = require_guides(engine,lid,state.location_reference_sets[lid])["views"][name]
    refs = [engine.local_path(guide["cameraGuide"]),engine.local_path(guide["visibleFloorPlan"])]
    text = prompt(name)+" Image 1 is the camera guide; image 2 is the view-only floor plan. "
    text += json.dumps(context(state.location_reference_sets[lid],name,brief),ensure_ascii=False)
    text += "\nStyle rules: "+context(state.location_reference_sets[lid],name,state.project_style_rules)["appearance"]
    text += "\nShared pair prompt: "+context(state.location_reference_sets[lid],name,
        state.location_pair_prompts.get(lid,{}).get("bridges",""))["appearance"]
    if name == "west_bridge":
        previous = state.records.get(f"backgrounds:{lid}__east_bridge")
        if previous and engine._intact(previous):
            refs.append(engine.local_path(previous.asset.file_path))
            text += ("\nImage 3 is the East Bridge from this pair, STYLE ONLY. Match its rendering, palette, "
                     "materials and lighting treatment exactly; do not copy its east-facing objects or composition into the west region.")
    if len(text) > 31000:
        raise ValueError("Bridge prompt is too long. Shorten layout/style notes before generating.")
    return style_reference(engine,state,text,refs)
