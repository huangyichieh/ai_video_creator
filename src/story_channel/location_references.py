"""Location reference sets, camera-aware selection and bounded orbit segments."""
import math
from typing import Literal

from pydantic import Field,field_validator

from .models import Model

ANGLES = tuple(range(0, 360, 45))


def reference_type_label(value):
    return "SIMPLE" if value == "SIMPLE" else "DETAIL"


class LocationReferenceSet(Model):
    reference_type: Literal["KEY_360","SIMPLE"] = "KEY_360"
    simple_prompt: str = ""
    floor_plan: str = ""
    object_layout: dict[str, str] = Field(default_factory=dict)
    detail_references: dict[str, dict] = Field(default_factory=dict)
    removed_detail_ids: list[str] = Field(default_factory=list)
    camera_plan: dict | None = None
    camera_plan_signature: str = ""
    generation_method: Literal["dual_panorama","shared_3d"] = "dual_panorama"
    bridge_warp: dict = Field(default_factory=dict)

    @field_validator("reference_type",mode="before")
    @classmethod
    def normalize_legacy_type(cls,value):
        if value == "SIMPLE":
            return "SIMPLE"
        if value in (None,"KEY","ONE_TIME","KEY_360","DETAIL"):
            return "KEY_360"
        raise ValueError("Unsupported saved location configuration.")


def view_names(spec):
    if (spec or {}).get("reference_type") == "SIMPLE":
        return ["simple_master"]
    from .shared_room import enabled
    if enabled(spec):
        return ["room_panorama", *(f"raw_{angle:03d}" for angle in ANGLES),
                *(f"enhanced_{angle:03d}" for angle in ANGLES)]
    from .dual_panorama import is_key, MASTERS
    return list(MASTERS) if is_key(spec) else ["master_wide", "reverse", "focal"]


def set_keys(state, lid):
    spec = state.location_reference_sets.get(lid)
    return [f"backgrounds:{lid}__{view}" for view in view_names(spec)] if spec else None


def master_digest(engine, state, lid):
    from .dual_panorama import pair_signature
    return pair_signature(engine, state, lid)


def master_approved(engine, state, lid):
    if state.location_reference_sets.get(lid,{}).get("reference_type") == "SIMPLE":
        from .background_library import BackgroundLibrary
        return BackgroundLibrary(engine).approved(lid,state)
    from .shared_room import enabled,current
    if enabled(state.location_reference_sets.get(lid)):
        return bool(current(engine,state,lid))
    from .dual_panorama import pair_ready
    return pair_ready(engine, state, lid)


def approve_master(engine, lid, expected_asset_id, name="front_panorama"):
    from .dual_panorama import approve
    return approve(engine, lid, name, expected_asset_id)


def location_tasks(task_type, lid, prompt, spec):
    import json
    config = LocationReferenceSet.model_validate(spec)
    if config.reference_type == "SIMPLE":
        return [task_type("backgrounds",lid+"__simple_master",
            "Create ONE high-quality scene-ready background master, a normal perspective image, not a panorama, collage or multi-view sheet. "
            "No people, human reflections, labels or visible text. Detail prompt: "+(config.simple_prompt or prompt),
            refs=[],operation="simple_location")]
    from .shared_room import enabled,task
    if enabled(spec):
        from .room_canonical import tasks
        return tasks(lid,spec,task_type)
    # Draft key locations may exist before layout is filled; generation checks
    # readiness separately so browsing an incomplete draft remains possible.
    layout = " Fixed floor plan and object coordinates: " + json.dumps({"floorPlan": config.floor_plan,
        "objectLayout": config.object_layout}, ensure_ascii=False)
    from .dual_panorama import is_key, MASTERS, panorama_prompt, MODEL
    if is_key(spec):
        from .location_view_context import view_context
        from .directional_masters import overlap_range
        brief = prompt.split(" World rules:",1)[0]
        tasks = [task_type("backgrounds",lid+"__"+name,
                           panorama_prompt(name)+"\nVisible view context: "+json.dumps(view_context(spec,name,notes=brief),ensure_ascii=False),
                           refs=[f"backgrounds:{lid}__{previous}" for previous in MASTERS[:index]
                                 if overlap_range(name,previous)],operation="location_panorama")
                 for index,name in enumerate(MASTERS)]
        return tasks
    tasks = []
    names = view_names(spec)
    master = f"backgrounds:{lid}__{names[0]}"
    for name in names:
        if name == "reverse":
            direction = "Reverse establishing view, looking 180 degrees from the master camera; keep the same object layout. "
        elif name == "focal":
            direction = "Focal detail/composition of the main interaction area specified in the location description. "
        else:
            direction = "Master wide establishing reference showing the room geometry, principal objects and spatial relationships. "
        refs = [] if not tasks else [master]
        tasks.append(task_type("backgrounds", lid + "__" + name,
            direction + "Match the master reference's architecture, materials, lighting and style. No people, panels or text. "
            + prompt + layout, refs=refs, operation="location_view"))
    return tasks


def camera_segments(start, end, *, max_degrees=45):
    if not all(math.isfinite(value) for value in (start, end, max_degrees)) or max_degrees <= 0:
        raise ValueError("Camera angles must be finite and the segment limit positive.")
    count = max(1, math.ceil(abs(end - start) / max_degrees))
    if count > 32:
        raise ValueError("Split movements exceeding four revolutions into separate planned shots.")
    return [(start + (end-start)*i/count, start + (end-start)*(i+1)/count) for i in range(count)]


class LocationReferenceResolver:
    def __init__(self, engine, state):
        self.engine, self.state = engine, state

    def resolve(self, location_id, start_angle=0, end_angle=None, target_area="", *, max_references=3,pitch=0,fov=70,
                character_position=None,interaction_target="",prefer_details=False):
        if not 1 <= max_references <= 3:
            raise ValueError("Select between one and three location references.")
        start_angle = 0 if start_angle is None else start_angle
        end_angle = start_angle if end_angle is None else end_angle
        if not all(math.isfinite(value) for value in (start_angle, end_angle)):
            raise ValueError("Camera angles must be finite.")
        state, lid = self.state, location_id
        spec = state.location_reference_sets.get(lid)
        if (spec or {}).get("reference_type") == "SIMPLE":
            from .background_library import BackgroundLibrary
            tasks = self.engine.tasks(state,references_only=True)
            key = f"backgrounds:{lid}__simple_master"
            task = next((task for task in tasks if task.key == key),None)
            return [key] if task and self.engine.record_current(state.records.get(key),task,state) and BackgroundLibrary(self.engine).approved(lid,state,tasks) else []
        from .shared_room import enabled,perspective_keys
        if enabled(spec):
            from .room_canonical import shot_references
            return shot_references(self.engine,state,lid,start_angle,end_angle,pitch=pitch,fov=fov,max_references=max_references)
        from .dual_panorama import is_key,MASTERS,pair_ready
        if is_key(spec):
            if not pair_ready(self.engine,state,lid):
                return []
            from .directional_masters import select
            nearest = lambda angle:select(angle,fov)[0]
            primary = nearest(start_angle)
            selected = [f"backgrounds:{lid}__{primary}"]
            from .location_details import current,relevance,object_ids
            ids = object_ids(self.engine,lid,spec)
            ranked = []
            for identity,reference in spec.get("detail_references",{}).items():
                score = relevance(spec,reference,identity,target_area=target_area,character_position=character_position,
                    interaction_target=interaction_target,ids=ids)
                if score > 0 and current(self.engine,state,lid,reference):
                    angle_difference = abs((float(reference.get("yaw",start_angle))-start_angle+180)%360-180)
                    ranked.append((-score,angle_difference,identity,reference["key"]))
            details = [key for _,_,_,key in sorted(ranked)]
            if prefer_details and details:
                # A scene-ready detail supplies the local composition; retain the
                # master as the spatial anchor whenever the reference budget permits.
                selected = [details[0],*selected,*details[1:]]
            else:
                selected.extend(details)
            if nearest(end_angle) != primary:
                selected.append(f"backgrounds:{lid}__{nearest(end_angle)}")
            for name in dict.fromkeys(select(start_angle,fov)+select(end_angle,fov)):
                key = f"backgrounds:{lid}__{name}"
                if key not in selected:
                    selected.append(key)
            return selected[:max_references]
        selected = []

        def add(key):
            record = state.records.get(key)
            if key not in selected and record and record.asset and record.status == "succeeded" and self.engine._intact(record):
                selected.append(key)

        if spec:
            area = target_area.strip().casefold()
            if area:
                for name in sorted(spec.get("detail_references", {})):
                    reference = spec["detail_references"][name]
                    if not _detail_current(state, reference):
                        continue
                    if name.casefold() in area or area in name.casefold():
                        add(reference["key"])
            for reference in spec.get("detail_references",{}).values():
                if "yaw" in reference and _detail_current(state, reference) and min(abs((float(reference["yaw"])-angle+180)%360-180) for angle in (start_angle,end_angle)) <= 22.5:
                    add(reference["key"])
            if area:
                add(f"backgrounds:{lid}__focal")
            for angle in (start_angle,end_angle):
                add(f"backgrounds:{lid}__"+("reverse" if abs((angle+180)%360-180) > 90 else "master_wide"))
            add(f"backgrounds:{lid}__master_wide")
        else:
            from .background_panorama import reference_keys
            candidates = reference_keys(state, lid)
            # Legacy four-view libraries also receive only angle-relevant views.
            if len(candidates) > 1:
                yaws = {lid: 0, lid+"__left": 270, lid+"__right": 90, lid+"__back": 180}
                candidates.sort(key=lambda key: min(abs((yaws.get(key.split(":", 1)[1], 0)-angle+180) % 360-180)
                                                     for angle in (start_angle, end_angle)))
            for key in candidates:
                add(key)
        return selected[:max_references]


def scene_reference_guidance(state, references):
    details = {reference["key"] for spec in state.location_reference_sets.values()
               for reference in spec.get("detail_references",{}).values()}
    selected = [key for key in references if key.startswith("backgrounds:")]
    if selected and selected[0].endswith("__simple_master"):
        return "The single approved background master defines this location's appearance, composition, materials and lighting. Preserve its visible environment and fixed objects; do not invent additional room geometry."
    if selected and selected[0] in details:
        return ("The first selected location reference is a scene-ready detail background. "
            "Use its local perspective, composition and complete environment appearance as the primary background plate; "
            "place the requested characters and action naturally within it. Adapt framing only as required by the shot. "
            "The accompanying panorama master anchors the same room's world-space layout and fixed objects. "
            "Do not replace the detail view with a wide panorama or redesign the room.")
    return ("The first selected location reference is the primary panorama master, defining room layout and fixed objects. "
        "Use matching detail backgrounds as complete local scene references, not merely texture samples. "
        "Preserve the same furniture positions, materials, lighting and visual style.")


def _detail_current(state, reference):
    import json
    if reference.get("status") == "draft":
        return False
    try:
        sources = reference.get("sourceAssets",{})
        sources = json.loads(sources) if isinstance(sources,str) else sources
    except (TypeError,ValueError):
        return False
    if not isinstance(sources,dict):
        return False
    for key,value in sources.items():
        record = state.records.get(key)
        if not record or not record.asset or record.status != "succeeded" or [record.asset.asset_id,record.file_hash] != value:
            return False
    return True
