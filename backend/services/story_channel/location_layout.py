"""Generate editable spatial planning drafts with tracked OpenAI usage."""
from pydantic import Field,PrivateAttr
from .floor_plan_guides import CameraPlan,generate_guides,source_signature

from .models import Model
from .modification import modify_content


class LayoutObject(Model):
    name: str = Field(min_length=1)
    position: str = Field(min_length=1)


class LocationLayout(Model):
    floor_plan: str = Field(description="Complete room layout, dimensions, camera position and fixed-object placement; never blank in a generated draft.")
    objects: list[LayoutObject] = Field(default_factory=list,description="Named physical fixtures/furnishings with concrete world-space positions. Populate for a normal furnished location, including initial drafts with an empty object list.")
    camera_plan: CameraPlan | None = Field(default=None,description="One spatial room/camera/object model matching the floor plan and every listed object. Required for shared 3D room generation.")
    references_match: bool | None = None
    _guides: dict = PrivateAttr(default_factory=dict)

    @property
    def camera_guides(self):
        return self._guides

    def object_layout(self):
        from .object_orientation import orientation,with_orientation,has_orientation
        geometry = {item.source_name:item for item in self.camera_plan.objects} if self.camera_plan else {}
        result = {}
        for item in self.objects:
            yaw,definition = orientation(item.position)
            spatial = geometry.get(item.name)
            if not has_orientation(item.position) and spatial and spatial.front_yaw is not None:
                yaw,definition = spatial.front_yaw,spatial.front_definition
            result[item.name] = with_orientation(item.position,yaw,definition) if yaw is not None else item.position
        return result


def generate_location_layout(engine, subject, reference_type, floor_plan="", object_layout=None, *, provider=None, update="both",camera_plan=None,render_guides=True,instructions=""):
    from .background_library import locations
    location = next((item for item in locations(engine.creative) if item.location_id == subject), None)
    if location is None:
        raise ValueError("Background no longer exists.")
    reference_type = "KEY_360"  # Normalize saved legacy callers to the only workflow.
    manifest = engine.manager.load(engine.project_id)
    shared_3d = engine.load().location_reference_sets.get(subject,{}).get("generation_method") == "shared_3d"
    if update not in ("both","objects","floor","check","guide","modify"):
        raise ValueError("Choose a layout generation, reference check or guide operation.")
    if update in ("check","guide","modify") and (not floor_plan.strip() or not object_layout):
        raise ValueError("Provide both a floor plan and object layout first.")
    if update == "modify" and not instructions.strip():
        raise ValueError("Enter a modification prompt first.")
    if update == "objects" and not floor_plan.strip():
        raise ValueError("Enter a floor plan before updating object layout.")
    if update == "floor" and not object_layout:
        raise ValueError("Enter an object layout before updating the floor plan.")
    current = LocationLayout(floor_plan=floor_plan, objects=[LayoutObject(name=k, position=v)
                                                          for k, v in (object_layout or {}).items()],camera_plan=camera_plan)
    context = {"location": location.model_dump(mode="json", by_alias=True),
               "language": manifest.language,
               "projectStyleRules":engine.load().project_style_rules,
               "world": engine.creative.artifact("world").model_dump(mode="json", by_alias=True)}

    def validate(output):
        if update == "check":
            if output.references_match is None:
                raise ValueError("The reference check must report whether the current references match.")
            if output.references_match:
                output.floor_plan = floor_plan
                output.objects = [item.model_copy(deep=True) for item in current.objects]
        if update == "objects":
            output.floor_plan = floor_plan
        elif update == "floor":
            output.objects = [item.model_copy(deep=True) for item in current.objects]
        elif update == "guide":
            if output.references_match is not True:
                raise ValueError("Check reference to reconcile the floor plan and object layout before generating images.")
            output.floor_plan = floor_plan
            output.objects = [item.model_copy(deep=True) for item in current.objects]
        if (shared_3d or update in ("check","guide","modify")) and not output.camera_plan:
            raise ValueError("The reference needs spatial camera-plan metadata. Try again.")
        if not output.floor_plan.strip():
            raise ValueError("The generated layout needs a floor plan and object positions: floorPlan was blank. Return a complete room description and spatial model.")
        if not output.objects:
            raise ValueError("The generated layout needs a floor plan and object positions: objects was empty. Add named fixtures/furnishings appropriate to the location, each with a concrete position and matching cameraPlan footprint.")
        names = [item.name.strip().casefold() for item in output.objects]
        if len(names) != len(set(names)) or any(not item.name.strip() or not item.position.strip() for item in output.objects):
            raise ValueError("The generated layout needs unique object names and nonempty positions.")
        if output.camera_plan:
            from .object_orientation import orientation,has_orientation
            originals = {item.source_name:item for item in current.camera_plan.objects} if current.camera_plan else {}
            for item in output.camera_plan.objects:
                yaw,definition = orientation(current.object_layout().get(item.source_name,""))
                previous = originals.get(item.source_name)
                if update == "modify":
                    value = output.object_layout().get(item.source_name,"")
                    if has_orientation(value):
                        item.front_yaw,item.front_definition = orientation(value)
                elif has_orientation(current.object_layout().get(item.source_name,"")):
                    item.front_yaw,item.front_definition = yaw,definition
                elif previous and previous.front_yaw is not None:
                    item.front_yaw,item.front_definition = previous.front_yaw,previous.front_definition
                else:
                    yaw,definition = orientation(output.object_layout().get(item.source_name,""))
                    if yaw is not None:
                        item.front_yaw,item.front_definition = yaw,definition
            allowed = set(output.object_layout())
            names = [item.source_name for item in output.camera_plan.objects]
            if len(names) != len(set(names)) or set(names) != allowed:
                raise ValueError("Camera-plan objects must match the object layout exactly, once per physical object.")
            if shared_3d:
                from .room_geometry import scene_from_spec
                scene_from_spec({"floor_plan":output.floor_plan,"object_layout":output.object_layout(),
                    "camera_plan":output.camera_plan.model_dump(mode="json"),
                    "camera_plan_signature":source_signature(output.floor_plan,output.object_layout())})
        if update in ("both","modify"):
            import json
            from .object_orientation import orientation
            # Keep the visible JSON self-contained even if the provider put
            # orientation only in camera_plan. No second AI call is needed.
            values = output.object_layout()
            for item in output.objects:
                value = values[item.name]
                yaw,definition = orientation(value)
                try:
                    data = json.loads(value)
                except (TypeError,ValueError):
                    data = None
                if not isinstance(data,dict):
                    data = {"position":value}
                data.update(frontYaw=yaw,frontDefinition=definition)
                item.position = json.dumps(data,ensure_ascii=False)
        return output

    authority = ("Modify the CURRENT object layout according to the user's request below. Update floor_plan and camera_plan to match any requested "
                 "object additions/removals, positions or facing directions. Preserve unrelated facts, room geometry, object IDs, sizes, materials and orientations. "
                 "Keep existing physical-object names and stable IDs unless explicitly asked to rename/remove them. "
                 "A requested rotation may change frontYaw; do not restore the original orientation against the user's request. "
                 "Return the complete updated layout, not a patch. User modification request:\n"+instructions.strip()+"\n" if update == "modify" else
                 "Check whether the current floor plan and object layout match. Preserve matching facts and object IDs. "
                 "If they conflict, reconcile both with the location description and World; prefer explicit floor-plan geometry for conflicting positions. "
                 "Remove duplicate physical objects, not legitimate distinct objects. Set references_match=true only if the original drafts already match; otherwise false. "
                 "If true keep both source texts unchanged. Return consistent floor_plan, objects and camera_plan. " if update == "check" else
                 "Generate spatial camera-plan data for the CURRENT floor plan and object layout. Keep both source texts exactly unchanged. "
                 "Set references_match=true only when these original texts consistently describe the same room; otherwise false. "
                 "Do not silently resolve conflicting source facts; fail rather than guess if they cannot describe one consistent room. " if update == "guide" else
                 "Update BOTH drafts consistently. " if update == "both" else
                 "The manually edited FLOOR PLAN is authoritative: keep its text unchanged, update object positions/list and camera_plan to match it. " if update == "objects" else
                 "The manually edited OBJECT LAYOUT is authoritative: keep every object name and position string unchanged, update floor-plan text and camera_plan to match it. ")
    result = modify_content(engine.creative, current,
        authority+
        "Create a practical floor plan and object layout for this background, consistent with the location description "
        "and World. Complete missing spatial details while preserving supplied facts and useful draft choices. "
        "Write in the project's language. Describe one fixed camera center, numeric eye height and lens focal length, used unchanged across all views, room dimensions or outdoor boundaries, "
        "entrances, walls and orientation: 0 degrees front/north, 90 right/east, 180 rear/south, 270 left/west. "+
        ("INITIAL DRAFT: an empty floor plan/object list means the layout has not been designed yet. Design plausible fixtures and furnishings "
         "suited to the location description, such as desks/chairs/storage for an office and sensible doors/windows. These are editable design proposals, "
         "not additions to approved World canon. Keep supplied fixtures, IDs and constraints; do not add people. Return a nonempty objects list, "
         "with one matching camera_plan footprint per object; never return only room text and an empty object list. " if update == "both" else
         "Use the supplied physical objects and fixed facts as the source of truth. Preserve existing names/IDs. In check mode reconcile missing "
         "entries mentioned in one representation with the other; in guide mode keep both source texts unchanged. ")+
        "Use doors, windows, desks, cabinets and important props as named world-space anchors. Give principal objects unique names and concrete positions relative to that center; keep every view spatially "
        "consistent. Support four directional panorama masters from fixed camera regions around "
        "the same room center. Keep one complete world-space layout; camera visibility selects its portions. "
        "For long desks, bookshelves, counters or other crossing furniture keep ONE object ID, not two objects. "
        "When useful, encode its position as a JSON STRING with wall, position, northSouthRange [start,end] (0=north end of wall, 1=south end), "
        "northSegment, southSegment, shared join/material/height/depth details, and totalLengthMeters only if known. "
        "Use the same endpoints and cross-section on both sides; do not invent equal half-lengths. Include the shared overlap and logical continuation, with no artificial end cap or extra leg at the view split. "
        "Also produce camera_plan spatial metadata: room boundary polygon and camera position in metres (x=east, y=north), camera height, "
        "and a top-down footprint polygon for EVERY listed object. source_name must match its object-layout name exactly. "
        "Include room_height_m, floor_color, wall_color and ceiling_color as #RRGGBB material colors. For every object provide "
        "height_m, elevation_m and material_color. Model a simple 3D blockout with physically plausible heights; windows are raised above the floor. "
        "For directional furniture/appliances, preserve or propose front_yaw in [0,360), clockwise from north (0 north, 90 east, 180 south, 270 west), "
        "and front_definition explaining the physical front: seated person's gaze for a chair, seating/drawer side for a desk, door side for a cabinet. "
        "Generate a sensible facing direction for EVERY directional object, including chairs, desks and cabinets, as part of this layout request. "
        "Each objects entry's position must be a JSON STRING containing position, frontYaw and frontDefinition, so facing is visible in Object layout JSON. "
        "frontYaw is a number, never a string. Use null only for genuinely non-directional objects or unresolved orientation; do not leave normal furniture orientation unspecified. "
        "Keep these JSON fields identical to front_yaw/front_definition in the matching camera_plan object. Preserve supplied known orientations unless the user's modification request changes them. "
        "Object fronts are fixed world-space orientations, not directions toward the camera; never rotate furniture to face each master. "
        "Keep the camera outside solid objects and below the ceiling. Do not add people or character geometry. "
        "Give each physical object one stable uppercase ID such as PRINTER_01, DOOR_01, WINDOW_01; preserve supplied IDs and never duplicate a unique object. "
        "Use one shared camera center and geometry for four North/East/South/West master guides at yaw 0/90/180/270, each with horizontal FOV 110 degrees and 20-degree adjacent overlaps. "
        "No bridge images are required. Keep front directions and shared object appearances consistent in overlapping camera regions. "
        "The renderer will draw the top-down draft, stable labels, camera arrow and visible region locally; return spatial metadata, not images or certification of 3D geometry.",
        "location_layout", context=context, provider=provider,
        task_key=f"assets:backgrounds:{subject}:layout", validate_output=validate,allow_new_ids=True,
        creation=update == "both",retry_validation=True)
    spec = {"floor_plan":result.floor_plan,"object_layout":result.object_layout(),
            "camera_plan":result.camera_plan.model_dump(mode="json",by_alias=True) if result.camera_plan else None,
            "camera_plan_signature":source_signature(result.floor_plan,result.object_layout())}
    from .floor_plan_guides import stable_ids
    ids = stable_ids(engine,subject,result.object_layout(),result.camera_plan)
    if render_guides:
        result._guides = generate_guides(engine,subject,spec)
    if result.camera_plan:
        for item in result.camera_plan.objects:
            item.object_id = ids[item.source_name]
    from .floor_plan_revisions import capture
    capture(engine,subject,{**spec,"camera_plan":result.camera_plan.model_dump(mode="json",by_alias=True) if result.camera_plan else None},
        source="AI layout: "+update,guides=result.camera_guides or None)
    return result


def save_layout_result(engine,subject,result,*,expected_layout=None):
    """Persist a successful layout action without another provider request."""
    from .background_library import BackgroundLibrary
    return BackgroundLibrary(engine).configure_references(subject,"KEY_360",result.floor_plan,result.object_layout(),
        camera_plan=result.camera_plan.model_dump(mode="json",by_alias=True) if result.camera_plan else None,
        expected_layout=expected_layout)


def generate_floor_plan_images(engine,subject,reference_type,floor_plan,object_layout,*,camera_plan=None,provider=None,expected_layout=None):
    """Explicitly save the current reference and publish the two camera guide pairs."""
    from .background_library import BackgroundLibrary
    from .floor_plan_guides import publish_guides
    if camera_plan:
        plan = CameraPlan.model_validate(camera_plan)
        if {item.source_name for item in plan.objects} != set(object_layout):
            raise ValueError("Check reference to reconcile the object layout and camera plan first.")
        result = LocationLayout(floor_plan=floor_plan,objects=[LayoutObject(name=k,position=v) for k,v in object_layout.items()],camera_plan=plan)
    else:
        result = generate_location_layout(engine,subject,reference_type,floor_plan,object_layout,
            update="guide",provider=provider,render_guides=False)
    from .floor_plan_guides import stable_ids
    ids = stable_ids(engine,subject,result.object_layout(),result.camera_plan)
    for item in result.camera_plan.objects:
        item.object_id = ids[item.source_name]
    state = save_layout_result(engine,subject,result,expected_layout=expected_layout)
    result._guides = publish_guides(engine,subject,state.location_reference_sets[subject])
    return result
