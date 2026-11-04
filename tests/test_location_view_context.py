import json

import numpy as np
from PIL import Image

from story_channel.location_view_context import view_context,scoped_text
from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.dual_panorama import approve
from test_dual_panorama import Sectors
from test_studio import world


def layout():
    return {"floor_plan":json.dumps({"dimensions":"8x6 metres","cameraCenter":"origin, height 1.6m",
        "north":{"furniture":"NorthOnlyDesk and blue window"},"south":{"furniture":"SouthOnlyCabinet and red door"},
        "east":{"furniture":"SharedEastDesk, oak, 2 metres wide"},"west":{"furniture":"SharedWestShelf"}}),
        "object_layout":{"NorthOnlyDesk":"north wall; oak", "SouthOnlyCabinet":"south wall; red metal",
                         "SharedEastDesk":"east wall; oak; 2 metres wide; desktop height 0.8m",
                         "SharedWestShelf":"west wall; 1.9m tall", "UnplacedProp":"not positioned"}}


def test_opposite_wall_layout_and_objects_are_removed_from_each_view():
    spec = layout()
    north = view_context(spec,"front_panorama",notes="NorthOnlyDesk has a lamp; SouthOnlyCabinet is red; neutral daylight")
    south = view_context(spec,"back_panorama",notes="NorthOnlyDesk has a lamp; SouthOnlyCabinet is red; neutral daylight")
    assert "SouthOnlyCabinet" not in json.dumps(north)
    assert "NorthOnlyDesk" not in json.dumps(south)
    assert set(north["visibleObjects"]) == {"NorthOnlyDesk","SharedEastDesk","SharedWestShelf"}
    assert set(south["visibleObjects"]) == {"SouthOnlyCabinet","SharedEastDesk","SharedWestShelf"}
    assert north["sharedBoundaryObjects"] == south["sharedBoundaryObjects"]
    assert "UnplacedProp" not in json.dumps(north) and north["unplacedObjectCount"] == 1
    assert "1.6m" in north["floorPlan"] and "1.6m" in south["floorPlan"]
    spec["object_layout"]["NorthWestDesk"] = "west wall near north corner; oak"
    assert "NorthWestDesk" not in json.dumps(view_context(spec,"back_panorama"))
    assert "NorthWestDesk" in view_context(spec,"front_panorama")["visibleObjects"]
    spec["object_layout"]["NumericEastDesk"] = "yaw 85; oak; height 0.8m"
    north,south = [view_context(spec,name) for name in ("front_panorama","back_panorama")]
    assert "NumericEastDesk" in north["sharedBoundaryObjects"]["east"]
    assert north["sharedBoundaryObjects"] == south["sharedBoundaryObjects"]


def test_directional_heading_groups_and_chinese_text_are_scoped():
    text = "北牆：\n藍色窗戶\n南牆：\n紅色櫃子\n東牆：\n辦公桌\n西牆：\n書架"
    north = scoped_text(text,"front_panorama")
    south = scoped_text(text,"back_panorama")
    assert "藍色窗戶" in north and "紅色櫃子" not in north
    assert "紅色櫃子" in south and "藍色窗戶" not in south
    assert all(word in north and word in south for word in ("辦公桌","書架"))
    text = "North wall:\nblue window\nSouth wall:\nred cupboard\nEast wall:\noak desk"
    assert "red cupboard" not in scoped_text(text,"front_panorama")
    assert "blue window" not in scoped_text(text,"back_panorama")


def test_long_side_wall_objects_get_distinct_segments_with_one_connection():
    spec = {"floor_plan":json.dumps({"east":{"north":"NorthEndCabinet", "south":"SouthEndCabinet", "continuous":"LongEastDesk"},
                                    "west":{"continuous":"LongWestShelf"}}),
            "object_layout":{
                "NorthEndCabinet":"east wall near north corner",
                "SouthEndCabinet":"east wall near south corner",
                "LongEastDesk":"east wall; spans the full wall; oak; desktop height 0.8m",
                "LongWestShelf":json.dumps({"wall":"west","northSouthRange":[.1,.7],"totalLengthMeters":6,
                    "northSegment":"open shelves at north end", "southSegment":"closed cupboards at south end",
                    "shared":"same oak grain, depth 0.4m, shelf levels 0.5/1.0/1.5m"})}}
    north,south = [view_context(spec,name) for name in ("front_panorama","back_panorama")]
    for side,key in (("east","LongEastDesk"),("west","LongWestShelf")):
        a,b = north["visibleObjects"][key],south["visibleObjects"][key]
        assert a["segment"] == "north" and b["segment"] == "south"
        assert a["connectionId"] == b["connectionId"]
        assert "Never draw the whole" in a["renderInstruction"]
        assert north["sharedBoundaryObjects"][side][key] == south["sharedBoundaryObjects"][side][key]
        assert "no artificial end cap" in north["sharedBoundaryObjects"][side][key]["connectionRule"]
    assert north["visibleObjects"]["LongEastDesk"]["coreWallFractionRange"] == [0,.5]
    assert south["visibleObjects"]["LongEastDesk"]["coreWallFractionRange"] == [.5,1]
    assert north["visibleObjects"]["LongWestShelf"]["coreLengthMeters"] == 4
    assert south["visibleObjects"]["LongWestShelf"]["coreLengthMeters"] == 2
    assert "closed cupboards" not in json.dumps(north)
    assert "open shelves" not in json.dumps(south)
    assert "SouthEndCabinet" not in json.dumps(north)
    assert "NorthEndCabinet" not in json.dumps(south)
    assert json.loads(north["floorPlan"])["east"]["coreRegion"] == "north half"
    assert json.loads(south["floorPlan"])["east"]["coreRegion"] == "south half"


def test_side_object_wholly_in_one_half_does_not_get_a_fake_join():
    spec = {"floor_plan":"Fixed center; east wall cabinets", "object_layout":{
        "NorthernCabinet":json.dumps({"wall":"east","northSouthRange":[.1,.3],"totalLengthMeters":2})}}
    north,south = [view_context(spec,name) for name in ("front_panorama","back_panorama")]
    assert "NorthernCabinet" in north["visibleObjects"]
    assert "connectionId" not in north["visibleObjects"]["NorthernCabinet"]
    assert "NorthernCabinet" not in north["sharedBoundaryObjects"]["east"]
    assert "NorthernCabinet" not in json.dumps(south)


def test_unspecified_long_object_extent_is_not_assumed_to_be_equal_halves():
    spec = {"floor_plan":"One fixed center", "object_layout":{"LongDesk":"east wall; long oak desk; height 0.8m"}}
    north,south = [view_context(spec,name) for name in ("front_panorama","back_panorama")]
    for scope in (north,south):
        segment = scope["visibleObjects"]["LongDesk"]
        assert "coreLengthMeters" not in segment
        assert "exact endpoint is unspecified" in segment["extentRule"]


def test_object_ending_at_split_keeps_its_natural_endpoint_in_the_overlap():
    spec = {"floor_plan":"One fixed center", "object_layout":{
        "LongSouthDesk":json.dumps({"wall":"east","northSouthRange":[.5,.9],"totalLengthMeters":4})}}
    north,south = [view_context(spec,name) for name in ("front_panorama","back_panorama")]
    assert north["visibleObjects"]["LongSouthDesk"]["overlapOnly"]
    assert north["visibleObjects"]["LongSouthDesk"]["coreLengthMeters"] == 0
    assert south["visibleObjects"]["LongSouthDesk"]["coreLengthMeters"] == 4
    contract = north["sharedBoundaryObjects"]["east"]["LongSouthDesk"]
    assert contract == south["sharedBoundaryObjects"]["east"]["LongSouthDesk"]
    assert "natural object endpoint" in contract["connectionRule"]


def test_actual_image_requests_are_view_local_and_back_receives_exact_boundary_crops(world):
    world.generate_characters()
    provider = Sectors()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=provider)
    spec = layout()
    lid = BackgroundLibrary(engine).add("Office","NorthOnlyDesk at north; SouthOnlyCabinet at south; SharedEastDesk at east; SharedWestShelf at west",
        reference_type="KEY_360",floor_plan=spec["floor_plan"],object_layout=spec["object_layout"]).location_id
    from story_channel.floor_plan_guides import publish_guides
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    from test_dual_panorama import approve_bridges
    approve_bridges(engine,lid)
    state = engine.generate("backgrounds",subject=lid)
    north = state.records[f"backgrounds:{lid}__front_panorama"]
    assert len(provider.calls) == 4
    first,second = provider.calls[2:]
    assert "SouthOnlyCabinet" not in first[0] and "NorthOnlyDesk" not in second[0]
    assert "SharedEastDesk" in first[0] and "SharedEastDesk" in second[0]
    assert '"sideWallSegments"' in first[0] and '"sideWallSegments"' in second[0]
    assert '"segment": "north"' in first[0] and '"segment": "south"' not in first[0]
    assert '"segment": "south"' in second[0] and '"segment": "north"' not in second[0]
    assert "APPROVED EAST BRIDGE" in second[0]
    assert "do NOT mirror" in second[0] and "duplicate" in second[0]
    assert len(first[1]) == len(second[1]) == 5
    north_file = engine.local_path(north.asset.file_path)
    assert north_file not in second[1]
    assert second[1][1].name == "back_panorama_camera.png"
    assert second[1][2].name == "back_panorama_visible_floor.png"
    assert first[1][2].name == "front_panorama_visible_floor.png"
    assert "Image 2 is the authoritative camera guide" in second[0]
    assert "EAST left, WEST right" in second[0]
    assert first[1][3:5] == second[1][3:5]
    assert first[1][3].parent.name.endswith("east_bridge")
    assert first[1][4].parent.name.endswith("west_bridge")
