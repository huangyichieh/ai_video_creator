import json

import numpy as np
import pytest
from PIL import Image

from story_channel.object_orientation import with_orientation,orientation,visible_face
from story_channel.floor_plan_guides import CameraPlan,PlanObject,generate_guides,source_signature,resolved_plan
from story_channel.detail_object_guidance import view_layout,instructions
from story_channel.location_view_context import object_bearings
from story_channel.location_details import generate
from test_location_master_pack import master_pack,approve_pack
from test_studio import world


def box(x,y):
    return [{"x":x-.2,"y":y-.2},{"x":x+.2,"y":y-.2},{"x":x+.2,"y":y+.2},{"x":x-.2,"y":y+.2}]


def spec():
    objects = {"chair":with_orientation("northeast corner",225,"Direction a seated person faces")}
    plan = CameraPlan(boundary=[{"x":-5,"y":-5},{"x":5,"y":-5},{"x":5,"y":5},{"x":-5,"y":5}],
        camera={"x":0,"y":0},objects=[PlanObject(source_name="chair",object_id="CHAIR_01",footprint=box(2,2))])
    return {"reference_type":"KEY_360","floor_plan":"10m square room","object_layout":objects,
        "camera_plan":plan.model_dump(),"camera_plan_signature":source_signature("10m square room",objects)}


def test_front_is_independent_of_location_and_camera_yaw():
    item = PlanObject(source_name="chair",footprint=box(0,3),front_yaw=180)
    assert visible_face(item,{"x":0,"y":0})["visibleFace"] == "front"
    assert visible_face(item,{"x":2,"y":3})["visibleFace"] == "left side"
    assert visible_face(item,{"x":0,"y":5})["visibleFace"] == "back"
    value = with_orientation("east wall",0,"north-facing chair")
    assert object_bearings(value) == {90}  # Facing north must never move it north.
    assert orientation(with_orientation(value,None)) == (None,"")
    for bad in (True,float("nan"),float("inf"),"east"):
        with pytest.raises(ValueError,match="finite"):
            orientation({"frontYaw":bad})
    legacy = PlanObject(source_name="unknown",footprint=box(1,1)).model_dump(by_alias=True)
    assert "frontYaw" not in legacy and "frontDefinition" not in legacy


def test_arrows_follow_world_directions_and_guide_metadata(master_pack):
    engine,lid,_,_ = master_pack
    data = spec()
    plan,kind = resolved_plan(data)
    assert kind == "metric" and plan.objects[0].front_yaw == 225
    guides = generate_guides(engine,lid,data)
    assert guides["objects"]["CHAIR_01"]["front_yaw"] == 225
    label = guides["floorPlanLabels"][0]
    assert label["frontYaw"] == 225
    with Image.open(engine.local_path(guides["floorPlan"])) as image:
        x0,y0,x1,y1 = map(int,label["box"])
        pixels = np.asarray(image)[y0:y1,x1-28:x1]
        assert np.all(pixels == (40,92,168),axis=-1).sum() > 20
    for yaw in (0,90):
        layout = view_layout(data,{"position":{"x":0,"y":0},"yaw":yaw},110)
        assert layout["visibleObjects"][0]["orientation"]["visibleFace"] == "front"
        assert "Never rotate the object" in instructions(layout)


def test_master_and_detail_requests_preserve_fixed_object_front(master_pack):
    from story_channel.background_library import BackgroundLibrary
    from story_channel.floor_plan_guides import publish_guides
    engine,lid,provider,_ = master_pack
    data = spec()
    library = BackgroundLibrary(engine)
    library.configure_references(lid,"KEY_360",data["floor_plan"],data["object_layout"],camera_plan=data["camera_plan"])
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    engine.generate("backgrounds",subject=lid)
    for index in (0,1):
        prompt,_ = provider.calls[index]
        assert "front faces yaw 225" in prompt and '"visibleFace": "front"' in prompt
        assert "Never rotate the object" in prompt
        assert "FINAL SPATIAL PRIORITY" in prompt
        assert prompt.rfind("fixed frontYaw=225") > prompt.rfind("FINAL BACKGROUND REQUIREMENT")
        assert "override overlap crops" in prompt
    approve_pack(engine,lid)
    state = generate(engine,lid,"chair_area","Chair area","Show the chair",camera_setup={"position":{"x":0,"y":0},"yaw":45})
    assert "front faces yaw 225" in provider.calls[-1][0]
    detail = state.location_reference_sets[lid]["detail_references"]["chair_area"]
    params = state.records[detail["key"]].asset.generation_params
    assert params["viewObjectLayout"]["visibleObjects"][0]["orientation"]["frontYaw"] == 225


def test_ai_layout_puts_camera_plan_fronts_into_visible_object_json(master_pack):
    from story_channel.location_layout import LocationLayout,LayoutObject,generate_location_layout,generate_floor_plan_images
    engine,lid,_,_ = master_pack
    data = spec()
    camera_plan = CameraPlan.model_validate(data["camera_plan"])
    camera_plan.objects[0].front_yaw = 225
    camera_plan.objects[0].front_definition = "Direction a seated person faces"
    class Provider:
        calls = 0
        last_usage = {}
        def generate(self,prompt,schema,config):
            self.calls += 1
            assert "frontYaw" in prompt and "frontDefinition" in prompt
            assert "EVERY directional object" in prompt
            return LocationLayout(floor_plan=data["floor_plan"],
                objects=[LayoutObject(name="chair",position="northeast corner")],camera_plan=camera_plan)
    provider = Provider()
    result = generate_location_layout(engine,lid,"KEY_360",provider=provider,render_guides=False)
    values = result.object_layout()
    chair = json.loads(values["chair"])
    assert chair["position"] == "northeast corner"
    assert chair["frontYaw"] == 225 and chair["frontDefinition"] == "Direction a seated person faces"
    assert provider.calls == 1
    saved = generate_floor_plan_images(engine,lid,"KEY_360",result.floor_plan,values,
        camera_plan=result.camera_plan.model_dump(),provider=provider)
    assert provider.calls == 1
    assert saved.camera_guides["floorPlanLabels"][0]["frontYaw"] == 225
    assert orientation(engine.load().location_reference_sets[lid]["object_layout"]["chair"])[0] == 225


def test_explicit_null_in_object_json_clears_camera_plan_front():
    data = spec()
    data["camera_plan"]["objects"][0]["front_yaw"] = 90
    data["object_layout"]["chair"] = json.dumps({"position":"northeast corner","frontYaw":None})
    data["camera_plan_signature"] = source_signature(data["floor_plan"],data["object_layout"])
    plan,_ = resolved_plan(data)
    assert plan.objects[0].front_yaw is None


def test_background_settings_has_no_separate_facing_panel(master_pack):
    from streamlit.testing.v1 import AppTest
    engine,lid,_,_ = master_pack
    def display(engine,lid):
        from story_channel.background_assets_ui import reference_set_settings
        from story_channel.background_library import BackgroundLibrary
        reference_set_settings(engine,BackgroundLibrary(engine),lid,engine.load())
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=20).run()
    assert not app.exception
    assert not any(item.label == "Object facing directions" for item in app.expander)
    assert any(item.label == "Object layout JSON" for item in app.text_area)
