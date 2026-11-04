import pytest
from PIL import Image

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.floor_plan_guides import publish_guides
from story_channel.location_references import view_names,LocationReferenceResolver
from story_channel import location_pairs as pairs
from story_channel.dual_panorama import MASTERS,approved,pair_ready,next_subjects
from test_dual_panorama import Sectors
from test_studio import world


@pytest.fixture
def master_pack(world):
    world.generate_characters()
    provider = Sectors()
    engine = AssetPipeline(world.manager,world.project_id,demo=True,image_provider=provider)
    lid = BackgroundLibrary(engine).add("Office","North windows, south entrance",reference_type="KEY_360",
        floor_plan="Fixed camera center",object_layout={"desk":"north wall","door":"south wall"}).location_id
    guides = publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    return engine,lid,provider,guides


def approve_pack(engine,lid):
    state = engine.load()
    return pairs.approve(engine,lid,"masters",expected={name:state.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in MASTERS})


def test_directional_master_set_ends_at_four_images_and_can_approve_background(master_pack):
    engine,lid,provider,guides = master_pack
    assert set(guides["views"]) == set(MASTERS)
    assert view_names(engine.load().location_reference_sets[lid]) == list(MASTERS)
    state = engine.generate("backgrounds",subject=lid)
    assert len(provider.calls) == 4
    assert {t.subject for t in engine.tasks(state) if t.stage == "backgrounds" and t.subject.startswith(lid+"__")} == {lid+"__"+n for n in MASTERS}
    for name,(prompt,images) in zip(MASTERS,provider.calls):
        assert len(images) >= 2
        assert images[0] == engine.local_path(guides["views"][name]["cameraGuide"])
        assert "bridge" not in prompt.casefold()
        asset = state.records[f"backgrounds:{lid}__{name}"].asset
        assert "masterPrefill" not in asset.generation_params
        assert "edit_mask" not in asset.generation_params["config"]
        assert asset.generation_params["backgroundContext"]["generation_mode"] == "camera_region_master"
    state = approve_pack(engine,lid)
    assert pair_ready(engine,state,lid)
    assert next_subjects(engine,state,lid) == []
    state = BackgroundLibrary(engine).approve(lid)
    assert BackgroundLibrary(engine).approved(lid,state)
    assert not (engine.path/"locations"/lid/"panorama"/"atlas.png").exists()
    with pytest.raises(ValueError,match="only four directional"):
        engine.generate("backgrounds",subject=lid+"__view_045")


def test_pair_revision_application_reopens_approval_without_bridges(master_pack):
    engine,lid,provider,_ = master_pack
    pairs.generate(engine,lid,"masters","Warm style")
    old = pairs.versions(engine,lid,"masters")[0]
    approve_pack(engine,lid)
    pairs.generate(engine,lid,"masters","Cool style")
    state = pairs.apply(engine,lid,"masters",old["id"])
    assert not any(approved(engine,state,lid,n) for n in MASTERS)
    assert pair_ready(engine,approve_pack(engine,lid),lid)
    assert len(provider.calls) == 8


def test_master_ui_has_no_bridge_prefill_or_projection_controls(master_pack):
    from streamlit.testing.v1 import AppTest
    engine,lid,_,_ = master_pack
    def display(engine,lid):
        from story_channel.dual_panorama_ui import outputs
        outputs(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    assert not app.exception
    assert len([b for b in app.button if b.key == "generate-location-pair-"+lid+"-masters"]) == 1
    assert not any("bridge" in str(b.key) or "yaw" in str(b.key) for b in app.button)
