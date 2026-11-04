from copy import deepcopy

import numpy as np
from PIL import Image

from story_channel.detail_pose_guide import render,instructions
from test_detail_object_guidance import layout_spec


def test_pitch_moves_floor_ceiling_and_horizon_in_the_actual_input_pixels(tmp_path):
    spec = layout_spec()
    original = deepcopy(spec)
    metadata = {}
    for label,pitch in (("down",-35),("level",0),("up",35)):
        metadata[label] = render(spec,{"position":{"x":0,"y":0},"yaw":0,"pitch":pitch,
            "heightMeters":1.6},80,tmp_path/(label+".png"),size=(384,216))
    down,level,up = (metadata[name] for name in ("down","level","up"))
    assert down["surfacePixelCounts"].get("ROOM_FLOOR",0) > level["surfacePixelCounts"].get("ROOM_FLOOR",0) > up["surfacePixelCounts"].get("ROOM_FLOOR",0)
    assert up["surfacePixelCounts"].get("ROOM_CEILING",0) > level["surfacePixelCounts"].get("ROOM_CEILING",0) > down["surfacePixelCounts"].get("ROOM_CEILING",0)
    assert down["horizonYNormalized"] < 0 < level["horizonYNormalized"] == .5 < 1 < up["horizonYNormalized"]
    assert "above the frame" in instructions(down)
    assert "below the frame" in instructions(up)
    assert spec == original


def test_height_changes_parallax_without_changing_pitch_or_world_geometry(tmp_path):
    spec = layout_spec()
    original = deepcopy(spec)
    guides = []
    for label,height in (("low",.3),("high",2.7),("same_high",2.7)):
        metadata = render(spec,{"position":{"x":0,"y":0},"yaw":0,"pitch":0,
            "heightMeters":height},80,tmp_path/(label+".png"),size=(384,216))
        assert metadata["horizonYNormalized"] == .5
        assert metadata["camera"]["forwardWorld"] == [0,1,0]
        guides.append(np.array(Image.open(tmp_path/(label+".png"))))
    assert np.mean(guides[0] != guides[1]) > .1
    assert np.array_equal(guides[1],guides[2])
    assert spec == original


def test_high_upward_tilt_does_not_force_floor_objects_into_image(tmp_path):
    from story_channel.detail_object_guidance import view_layout,instructions as placement
    spec = layout_spec()
    setup = {"position":{"x":0,"y":0},"yaw":0,"pitch":55,"heightMeters":2.7}
    metadata = render(spec,setup,80,tmp_path/"up.png",size=(384,216))
    layout = view_layout(spec,setup,80)
    layout["poseVisibility"] = metadata["objectVisibility"]
    assert metadata["objectVisibility"]["left_desk"] is False
    assert "Do not force it into frame" in placement(layout)
    assert "Show LEFT_DESK_01" not in placement(layout)
