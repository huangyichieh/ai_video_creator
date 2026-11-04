import copy
import json
import pytest

from story_channel.detail_object_guidance import view_layout,instructions
from story_channel.floor_plan_guides import source_signature
from story_channel.location_details import generate
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


def layout_spec():
    def box(name,x,y,half=.2):
        return {"source_name":name,"object_id":name.upper()+"_01","footprint":[
            {"x":x-half,"y":y-half},{"x":x+half,"y":y-half},
            {"x":x+half,"y":y+half},{"x":x-half,"y":y+half}]}
    objects = [box("left_desk",-2,4),box("near_prop",0,1),box("right_printer",2,4),
        box("far_window",0,8),box("behind_door",0,-3),box("offscreen",-8,2),box("partial",5,4,1)]
    objects.append({"source_name":"wide_shelf","object_id":"SHELF_01","footprint":[
        {"x":-8,"y":4.5},{"x":8,"y":4.5},{"x":8,"y":5.5},{"x":-8,"y":5.5}]})
    spec = {"floor_plan":"20 metre square room","object_layout":{item["source_name"]:"fixed position" for item in objects}}
    spec["camera_plan"] = {"boundary":[{"x":-10,"y":-10},{"x":10,"y":-10},{"x":10,"y":10},{"x":-10,"y":10}],
        "camera":{"x":0,"y":0},"objects":objects}
    spec["camera_plan_signature"] = source_signature(spec["floor_plan"],spec["object_layout"])
    return spec


def test_camera_composition_reports_screen_regions_depth_and_exclusions():
    spec = layout_spec()
    original = copy.deepcopy(spec)
    result = view_layout(spec,{"position":{"x":0,"y":0},"yaw":0},90)
    objects = {item["name"]:item for item in result["visibleObjects"]}
    assert objects["left_desk"]["screenRegions"] == ["left"]
    assert objects["right_printer"]["screenRegions"] == ["right"]
    assert objects["near_prop"]["screenRegions"] == ["center"]
    assert objects["near_prop"]["depthBand"] == "foreground"
    assert objects["left_desk"]["depthBand"] == "middle_ground"
    assert objects["far_window"]["depthBand"] == "background"
    excluded = {item["name"]:item["reason"] for item in result["excludedObjects"]}
    assert excluded["behind_door"] == "behind_camera"
    assert excluded["offscreen"] == "outside_horizontal_fov"
    assert objects["partial"]["partiallyInFrame"]
    assert objects["wide_shelf"]["screenRegions"] == ["left","center","right"]
    assert objects["wide_shelf"]["partiallyInFrame"]
    assert "Do not show BEHIND_DOOR_01" in instructions(result)
    assert "crop naturally" in instructions(result)
    assert spec == original


def test_rotation_fov_and_camera_position_change_calculated_objects():
    spec = layout_spec()
    setup = {"position":{"x":0,"y":0},"yaw":0}
    north = view_layout(spec,setup,90)
    assert north == view_layout(spec,{**setup,"yaw":360},90)
    south = view_layout(spec,{**setup,"yaw":180},90)
    assert {item["name"] for item in south["visibleObjects"]} == {"behind_door"}
    narrow = view_layout(spec,setup,20)
    assert "right_printer" not in {item["name"] for item in narrow["visibleObjects"]}
    moved = view_layout(spec,{"position":{"x":4,"y":0},"yaw":0},90)
    assert "right_printer" in {item["name"] for item in moved["visibleObjects"]}
    assert next(item for item in moved["visibleObjects"] if item["name"] == "right_printer")["screenRegions"] == ["left"]


def test_schematic_and_unplaced_objects_do_not_claim_metric_certainty():
    result = view_layout({"floor_plan":"Office","object_layout":{"desk":"north wall","unknown":"unspecified"}},
        {"position":{"x":0,"y":0},"yaw":0},70,{"desk":"DESK_01"})
    assert result["geometrySource"] == "schematic" and result["units"] == "schematic_not_to_scale"
    assert result["unresolvedObjects"][0]["name"] == "unknown"
    assert result["visibleObjects"][0]["objectId"] == "DESK_01"
    assert result["occlusion"] == "not_computed"


def test_generation_sends_computed_objects_and_labeled_guide_without_extra_ai_calls(master_pack):
    engine,lid,provider,guides = prepare(master_pack)
    before = len(provider.calls)
    state = generate(engine,lid,"desk_area","Desk area","Desk close-up",camera_setup={"position":{"x":0,"y":0},"yaw":0})
    assert len(provider.calls) == before+1
    prompt,refs = provider.calls[-1]
    assert "CAMERA-DERIVED OBJECT PLACEMENT" in prompt
    assert "Show "+guides["objectIds"]["desk"] in prompt
    assert "Do not show "+guides["objectIds"]["door"] in prompt
    reference = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    params = state.records[reference["key"]].asset.generation_params
    saved = json.loads(engine.local_path(params["objectGuidanceFile"]).read_text(encoding="utf-8"))
    assert saved == params["viewObjectLayout"]
    assert refs[0].name == "camera_perspective.png"
    assert refs[1].name == "camera_setup.png"
    assert saved["excludedObjects"][0]["name"] == "door"


@pytest.mark.parametrize("yaw,orientation",[
    (90,"east wall ahead; north side to screen-left and south side to screen-right"),
    (270,"west wall ahead; south side to screen-left and north side to screen-right"),
])
def test_side_view_camera_overrides_master_framing(master_pack,yaw,orientation):
    engine,lid,provider,_ = prepare(master_pack)
    before = len(provider.calls)
    state = generate(engine,lid,"side_area","Side area","Side wall detail",
        camera_setup={"position":{"x":0,"y":0},"yaw":yaw},fov=70)
    prompt,refs = provider.calls[-1]
    assert len(provider.calls) == before+1
    assert refs[0].name == "camera_perspective.png"
    assert refs[1].name == "camera_setup.png"
    assert orientation in prompt
    assert "override any panorama framing" in prompt
    assert "No master defines this output's framing" in prompt
    assert '"floorPlan":' not in prompt
    assert "Fixed camera center" not in prompt
    detail = state.location_reference_sets[lid]["detail_references"]["side_area"]
    params = state.records[detail["key"]].asset.generation_params
    assert params["compositionAuthority"] == "saved_camera_guide"
    assert params["inputImageRoles"][0] == "perspective_pose_guide"
    assert params["camera"]["yaw"] == yaw
    assert params["camera"]["fov"] == 70


def test_east_west_object_screen_placement_is_not_north_framing():
    spec = layout_spec()
    east = view_layout(spec,{"position":{"x":0,"y":0},"yaw":90},90)
    west = view_layout(spec,{"position":{"x":0,"y":0},"yaw":270},90)
    assert "offscreen" not in {item["name"] for item in east["visibleObjects"]}
    west_objects = {item["name"]:item for item in west["visibleObjects"]}
    # It lies just right of center at this wide FOV, not in the outer right third.
    assert west_objects["offscreen"]["screenRegions"] == ["center"]
    assert west_objects["offscreen"]["horizontalRange"][0] > 0
