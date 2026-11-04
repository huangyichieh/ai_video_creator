import numpy as np
import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.dual_panorama import approved,approve,assess,root
from story_channel.floor_plan_guides import publish_guides
from story_channel.location_bridges import BRIDGES
from story_channel.media_utils import file_hash
from story_channel.pipeline import ApprovalRequired
from test_dual_panorama import prepare,approve_bridges,Sectors
from test_studio import world


def test_both_masters_receive_same_approved_bridges_and_revisions_reopen_dependents(world):
    engine,lid,provider = prepare(world)
    state = engine.load()
    assert provider.calls[2][1][3:5] == provider.calls[3][1][3:5]
    for index,name in enumerate(BRIDGES):
        record = state.records[f"backgrounds:{lid}__{name}"]
        assert provider.calls[2][1][index+3] == engine.local_path(record.asset.file_path)
        assert file_hash(root(engine,lid)/(name+".png")) == record.file_hash
        assert approved(engine,state,lid,name)
    assert (root(engine,lid)/"master_north.png").is_file()
    assert (root(engine,lid)/"master_south.png").is_file()
    west = state.records[f"backgrounds:{lid}__west_bridge"].asset.asset_id
    state = engine.generate("backgrounds",subject=lid+"__east_bridge",force=True)
    assert not approved(engine,state,lid,"east_bridge")
    assert not approved(engine,state,lid,"west_bridge")
    assert not approved(engine,state,lid,"front_panorama")
    assert not approved(engine,state,lid,"back_panorama")
    assert state.records[f"backgrounds:{lid}__west_bridge"].asset.asset_id == west
    with pytest.raises(ApprovalRequired,match="East Bridge"):
        engine.generate("backgrounds",subject=lid+"__back_panorama")
    record = state.records[f"backgrounds:{lid}__east_bridge"]
    approve(engine,lid,"east_bridge",record.asset.asset_id)
    # Both masters reference the new anchor revision and need generation/review again.
    assert engine.inspect().records[f"backgrounds:{lid}__front_panorama"].status == "stale"


def test_south_can_generate_directly_after_bridge_approval_without_north_master(world):
    world.generate_characters()
    provider = Sectors()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=provider)
    lid = BackgroundLibrary(engine).add("Office","East desk",reference_type="KEY_360",
        floor_plan="Fixed center",object_layout={"desk":"east wall"}).location_id
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    approve_bridges(engine,lid)
    state = engine.generate("backgrounds",subject=lid+"__back_panorama")
    assert f"backgrounds:{lid}__front_panorama" not in state.records
    record = state.records[f"backgrounds:{lid}__back_panorama"]
    assert record.status == "succeeded"
    assert "APPROVED EAST BRIDGE" in provider.calls[-1][0]
    assert len(provider.calls[-1][1]) == 5
    approve(engine,lid,"back_panorama",record.asset.asset_id)


def test_qa_flags_masters_that_match_each_other_but_conflict_with_bridges():
    pixels = np.full((100,200,3),150,dtype=np.uint8)
    assert assess(pixels,pixels)["passed"]
    qa = assess(pixels,pixels,bridges={"east":np.zeros_like(pixels),"west":pixels})
    assert not qa["passed"] and qa["score"] < 1
    east = qa["overlaps"][0]
    assert east["geometryOrObjectConflictSuspected"]
    assert all(not result["passed"] for result in east["bridgeComparisons"])


def test_pending_bridge_disables_master_generation_in_ui(world):
    from streamlit.testing.v1 import AppTest
    engine,lid,_ = prepare(world)
    engine.generate("backgrounds",subject=lid+"__east_bridge",force=True)
    def display(engine,lid):
        from story_channel.dual_panorama_ui import outputs
        outputs(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert app.button(key=f"generate-location-pair-{lid}-masters").disabled
    assert app.button(key=f"approve-location-pair-{lid}-bridges").disabled


def test_bridge_requests_use_own_camera_region_and_shared_style_image(world):
    from test_floor_plan_guides import setup,plan
    from story_channel.style_rules import StyleRules
    from test_character_visual_costumes import image_bytes
    engine,library,lid = setup(world)
    library.configure_references(lid,"KEY_360","North printer; south door; east window",
        {"printer":"north","door":"south","window":"east"},camera_plan=plan().model_dump())
    StyleRules(engine).set_image(image_bytes("blue"))
    state = engine.load()
    guides = publish_guides(engine,lid,state.location_reference_sets[lid])
    east = guides["views"]["east_bridge"]
    assert east["yaw"] == 90 and east["horizontalFov"] == 60
    assert east["visibleObjectIds"] == ["WINDOW_01"]
    state = engine.generate("backgrounds",subject=lid+"__east_bridge")
    params = state.records[f"backgrounds:{lid}__east_bridge"].asset.generation_params
    assert len(params["inputImages"]) == 3
    assert params["inputImages"][0] == east["cameraGuide"]
    assert params["inputImages"][1] == east["visibleFloorPlan"]
    assert params["inputImages"][2] == state.project_style_image["file"]
    assert "STYLE ONLY" in params["prompt"]
    assert "DOOR_01" not in params["prompt"] and "PRINTER_01" not in params["prompt"]
