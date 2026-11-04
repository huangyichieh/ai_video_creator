import json

import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.floor_plan_guides import CameraPlan,generate_guides,source_signature,resolved_plan
from story_channel.location_layout import LocationLayout,LayoutObject,generate_location_layout
from test_studio import world


def plan():
    def box(x,y):
        return [{"x":x-.2,"y":y-.2},{"x":x+.2,"y":y-.2},
                {"x":x+.2,"y":y+.2},{"x":x-.2,"y":y+.2}]
    return CameraPlan(boundary=[
        {"x":-4,"y":-4},{"x":4,"y":-4},{"x":4,"y":4},{"x":-4,"y":4}],
        camera={"x":0,"y":0},objects=[
            {"source_name":"printer","object_id":"PRINTER_01","footprint":box(0,3)},
            {"source_name":"door","object_id":"DOOR_01","footprint":box(0,-3)},
            {"source_name":"window","object_id":"WINDOW_01","footprint":box(3,0)}])


def setup(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    library = BackgroundLibrary(engine)
    lid = library.add("Office","Printer north, door south and window east",reference_type="KEY_360").location_id
    return engine,library,lid


def test_guides_share_ids_but_clip_offscreen_objects_and_invalidate_manual_changes(world):
    engine,library,lid = setup(world)
    floor,objects = "8m square",{"printer":"north","door":"south","window":"east"}
    library.configure_references(lid,"KEY_360",floor,objects,camera_plan=plan().model_dump())
    spec = engine.load().location_reference_sets[lid]
    guides = generate_guides(engine,lid,spec)
    north,south = [guides["views"][name] for name in ("front_panorama","back_panorama")]
    assert set(guides["floorPlanObjectIds"]) == {"PRINTER_01","DOOR_01","WINDOW_01"}
    assert north["floorPlan"] == south["floorPlan"] == guides["floorPlan"]
    assert len(list(engine.local_path(guides["floorPlan"]).parent.glob("*.png"))) == 9
    assert north["visibleFloorPlan"] != south["visibleFloorPlan"]
    assert set(north["visibleObjectIds"]) == {"PRINTER_01","WINDOW_01"}
    assert set(south["visibleObjectIds"]) == {"DOOR_01","WINDOW_01"}
    assert north["yaw"] == 0 and south["yaw"] == 180
    for view in (north,south):
        assert view["horizontalFov"] == 200
        assert engine.local_path(view["floorPlan"]).is_file()
        assert engine.local_path(view["cameraGuide"]).is_file()
        assert engine.local_path(view["visibleFloorPlan"]).is_file()
        assert view["renderLayers"].index("labels") > view["renderLayers"].index("cameraOverlay")
        from PIL import Image
        diagram = Image.open(engine.local_path(view["cameraGuide"]))
        assert diagram.width > 900  # Separate legend does not crowd room geometry.
        for label in view["labels"]:
            x0,y0,x1,y1 = label["box"]
            assert diagram.getpixel((int(x0)+9,int(y1)-4)) == (255,255,255)
            for other in view["labels"]:
                if other is not label:
                    a,b,c,d = other["box"]
                    assert x1 <= a or c <= x0 or y1 <= b or d <= y0
    assert len({tuple(obj["diagramColor"]) for obj in guides["objects"].values()}) == 3
    # Reorder the authoritative list without changing physical identities.
    reordered = {key:objects[key] for key in reversed(objects)}
    other = generate_guides(engine,lid,{**spec,"object_layout":reordered,
                            "camera_plan_signature":source_signature(floor,reordered)})
    assert other["objectIds"] == guides["objectIds"]
    library.configure_references(lid,"KEY_360","Changed room",objects)
    assert resolved_plan(engine.load().location_reference_sets[lid])[1] == "schematic"


@pytest.mark.parametrize("update",["floor","objects"])
def test_update_preserves_edited_side_and_returns_unsaved_guides(world,update):
    engine,library,lid = setup(world)
    floor = "Manually edited north printer, south door, east window"
    objects = {"printer":"north","door":"south","window":"east"}
    class Provider:
        last_usage = {}
        def generate(self,prompt,schema,config):
            assert "authoritative" in prompt and "camera_plan" in prompt
            return LocationLayout(floor_plan="AI revised floor",objects=[
                LayoutObject(name=key,position=value+" changed") for key,value in objects.items()],camera_plan=plan())
    result = generate_location_layout(engine,lid,"KEY_360",floor,objects,update=update,provider=Provider())
    assert result.floor_plan == (floor if update == "objects" else "AI revised floor")
    assert result.object_layout() == (objects if update == "floor" else {key:value+" changed" for key,value in objects.items()})
    assert result.camera_guides["geometrySource"] == "metric"
    assert engine.load().location_reference_sets[lid]["floor_plan"] == ""
    assert json.loads((engine.path/"usage.json").read_text(encoding="utf-8"))[-1]["outcome"] == "succeeded"


def test_invalid_camera_plan_rejected():
    data = plan().model_dump()
    data["camera"] = {"x":20,"y":20}
    with pytest.raises(ValueError,match="inside"):
        CameraPlan.model_validate(data)


def test_check_reference_reconciles_drafts_without_unlocking_masters(world):
    from story_channel.floor_plan_guides import current_guides
    engine,library,lid = setup(world)
    class Provider:
        last_usage = {"input_tokens":100,"output_tokens":100}
        def generate(self,prompt,schema,config):
            assert "Check whether" in prompt and "prefer explicit floor-plan geometry" in prompt
            return LocationLayout(floor_plan="Printer north; door south; window east",objects=[
                LayoutObject(name="printer",position="north"),LayoutObject(name="door",position="south"),
                LayoutObject(name="window",position="east")],camera_plan=plan(),references_match=False)
    result = generate_location_layout(engine,lid,"KEY_360","Printer north; door south; window east",
        {"printer":"south","door":"south","window":"east"},update="check",provider=Provider(),render_guides=False)
    assert result.object_layout()["printer"] == "north"
    assert not result.camera_guides
    assert current_guides(engine,lid,engine.load().location_reference_sets[lid]) is None
    assert engine.load().location_reference_sets[lid]["object_layout"] == {}


def test_explicit_image_action_publishes_current_guides_and_invalidates_on_edit_or_damage(world):
    from story_channel.location_layout import generate_floor_plan_images
    from story_channel.floor_plan_guides import current_guides,require_guides
    engine,library,lid = setup(world)
    objects = {"printer":"north","door":"south","window":"east"}
    class Forbidden:
        def generate(self,*args):
            pytest.fail("Current spatial metadata must render locally without an AI call")
    result = generate_floor_plan_images(engine,lid,"KEY_360","8m square",objects,camera_plan=plan(),provider=Forbidden())
    spec = engine.load().location_reference_sets[lid]
    guides = require_guides(engine,lid,spec)
    assert result.floor_plan == "8m square" and result.object_layout() == objects
    assert guides["geometrySource"] == "metric"
    assert current_guides(engine,lid,{**spec,"floor_plan":"10m square"}) is None
    engine.local_path(guides["views"]["front_panorama"]["cameraGuide"]).write_bytes(b"damaged")
    with pytest.raises(ValueError,match="Generate floor plan image"):
        require_guides(engine,lid,spec)


def test_matching_references_are_not_rewritten_by_check(world):
    engine,_,lid = setup(world)
    floor = "Exact manual text: printer north, door south, window east."
    objects = {"printer":"north","door":"south","window":"east"}
    class Provider:
        last_usage = {}
        def generate(self,*args):
            return LocationLayout(floor_plan="Unnecessary rewrite",objects=[
                LayoutObject(name=k,position=v+" rewritten") for k,v in objects.items()],camera_plan=plan(),references_match=True)
    result = generate_location_layout(engine,lid,"KEY_360",floor,objects,update="check",provider=Provider(),render_guides=False)
    assert result.references_match
    assert result.floor_plan == floor and result.object_layout() == objects


def test_image_action_rejects_conflicting_text_without_saving(world):
    from story_channel.location_layout import generate_floor_plan_images
    engine,_,lid = setup(world)
    class Provider:
        last_usage = {}
        def generate(self,*args):
            return LocationLayout(floor_plan="Printer north",objects=[LayoutObject(name="printer",position="south")],
                camera_plan=plan(),references_match=False)
    with pytest.raises(ValueError,match="Check reference"):
        generate_floor_plan_images(engine,lid,"KEY_360","Printer north",{"printer":"south"},provider=Provider())
    assert engine.load().location_reference_sets[lid]["floor_plan"] == ""


def test_master_requests_require_explicit_current_guides_and_only_show_visible_objects(world):
    from story_channel.floor_plan_guides import publish_guides
    from story_channel.dual_panorama import approve
    from test_dual_panorama import Sectors
    engine,library,lid = setup(world)
    provider = Sectors()
    engine.image_provider = engine.panorama_image_provider = provider
    objects = {"printer":"north","door":"south","window":"east"}
    library.configure_references(lid,"KEY_360","North printer; south door; east window",objects,camera_plan=plan().model_dump())
    spec = engine.load().location_reference_sets[lid]
    generate_guides(engine,lid,spec)  # A draft preview does not unlock a master.
    with pytest.raises(ValueError,match="Generate floor plan image"):
        engine.generate("backgrounds",subject=lid)
    assert not provider.calls
    publish_guides(engine,lid,spec)
    from test_dual_panorama import approve_bridges
    approve_bridges(engine,lid)
    state = engine.generate("backgrounds",subject=lid)
    north = provider.calls[2]
    assert "PRINTER_01" in north[0] and "DOOR_01" not in north[0]
    assert "WINDOW_01" in north[0] and len(north[1]) == 5
    assert "SAME immutable bridge images" in north[0]
    south = provider.calls[3]
    assert "DOOR_01" in south[0] and "PRINTER_01" not in south[0]
    assert "WINDOW_01" in south[0] and len(south[1]) == 5
    assert south[1][1].name == "back_panorama_camera.png"
    assert '"yaw": 180' in south[0]
    assert "persistent object identities" in south[0]
