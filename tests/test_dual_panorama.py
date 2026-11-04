import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.dual_panorama import approve, assess, pair_ready, root, verify_protected, MASTERS
from story_channel.models import GeneratedAsset
from story_channel.pipeline import ApprovalRequired
from test_studio import world
from story_channel.location_bridges import BRIDGES


class Sectors:
    last_usage = {}
    def __init__(self):
        self.calls = []
        self.conflict = False
    def generate(self, prompt, refs, config):
        path = Path(config.options["output_path"])
        self.calls.append((prompt, list(refs)))
        back = path.parent.name.endswith("back_panorama")
        name = path.parent.name.rsplit("__",1)[-1]
        start,span = {"east_bridge":(60,60),"west_bridge":(240,60)}.get(name,(80 if back else -100,200))
        lon = np.deg2rad(start+(np.arange(720)+.5)*span/720)[None, :]
        lat = np.deg2rad(90-(np.arange(360)+.5)*180/360)[:, None]
        pixels = np.stack(np.broadcast_arrays(120+70*np.sin(lon*3), 130+60*np.cos(lat*2),
                                               130+40*np.sin(lon*5)*np.cos(lat)), axis=-1)
        if back and self.conflict:
            pixels = 255-pixels
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(pixels.astype(np.uint8)).save(path)
        from story_channel.files import input_hash
        return GeneratedAsset(type="image", provider="demo", model="synthetic-sectors", file_path=str(path), prompt_hash=input_hash(prompt))


def approve_bridges(engine,lid):
    for name in BRIDGES:
        state = engine.generate("backgrounds",subject=lid+"__"+name)
        approve(engine,lid,name,state.records[f"backgrounds:{lid}__{name}"].asset.asset_id)


def prepare(world, *, conflict=False):
    world.generate_characters()
    provider = Sectors()
    provider.conflict = conflict
    engine = AssetPipeline(world.manager, world.project_id, demo=True, image_provider=provider)
    loc = BackgroundLibrary(engine).add("Office", "Fixed office", reference_type="KEY_360",
        floor_plan="Fixed center, eye height 1.6m, north windows, west door", object_layout={"desks": "east wall"})
    lid = loc.location_id
    from story_channel.floor_plan_guides import publish_guides
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    from story_channel.location_pairs import approve as approve_pair
    for group,members in (("bridges",BRIDGES),("masters",MASTERS)):
        state = engine.generate("backgrounds",subject=lid)
        approve_pair(engine,lid,group,expected={name:state.records[f"backgrounds:{lid}__{name}"].asset.asset_id for name in members})
    return engine, lid, provider


def test_approved_pair_projects_all_views_without_ai_and_preserves_shared_world_pixels(world):
    engine, lid, provider = prepare(world)
    assert pair_ready(engine, engine.load(), lid)
    assert len(provider.calls) == 4 and len(provider.calls[0][1]) == 2
    assert "faces NORTH" in provider.calls[2][0] and "faces SOUTH" in provider.calls[3][0]
    assert len(provider.calls[2][1]) == len(provider.calls[3][1]) == 5
    state = engine.generate("backgrounds", subject=lid)
    assert len(provider.calls) == 4
    folder = root(engine, lid)
    assert (folder / "panorama/front.png").exists()
    qa = json.loads((folder / "panorama/seam_metadata.json").read_text())
    assert qa["passed"] and qa["score"] > .95 and len(qa["overlaps"]) == 2
    common = []
    for yaw in range(0, 360, 45):
        record = state.records[f"backgrounds:{lid}__view_{yaw:03d}"]
        assert record.asset.provider == "local" and record.asset.cost == 0
        info = json.loads((folder / "yaw" / f"yaw_{yaw:03d}.json").read_text())
        assert info["yaw"] == yaw and info["fov"] == 70 and info["pitch"] == 0
        assert info["sourcePanorama"] in ("front","back","front+back")
        assert not info["aiYawGeneration"] and info["cameraCenter"] == "location_center"
        if yaw in (0, 45):
            pixels = np.asarray(Image.open(folder / "yaw" / f"yaw_{yaw:03d}.png"))
            height, width = pixels.shape[:2]
            x = round((np.tan(np.deg2rad(22.5-yaw))/np.tan(np.deg2rad(35))+1)*width/2-.5)
            common.append(pixels[height//2, x].astype(int))
    assert np.max(np.abs(common[0]-common[1])) <= 2
    before = state.records[f"backgrounds:{lid}__view_090"].asset.asset_id
    state = engine.generate("backgrounds", subject=lid+"__view_045", force=True)
    assert len(provider.calls) == 4 and state.records[f"backgrounds:{lid}__view_090"].asset.asset_id == before
    with pytest.raises(ValueError, match="deterministic"):
        engine.modify_with_openai("backgrounds", lid+"__view_045", "change everything")


def test_conflicting_model_output_cannot_change_known_bridge_pixels(world):
    engine,lid,provider = prepare(world,conflict=True)
    assert pair_ready(engine,engine.load(),lid)
    for name in MASTERS:
        metadata = engine.load().records[f"backgrounds:{lid}__{name}"].asset.generation_params["masterPrefill"]
        assert metadata["protectedPixelsVerified"]
    engine.generate("backgrounds",subject=lid+"__view_045")
    assert len(provider.calls) == 4


def test_removed_combination_is_replaced_with_master_pixels_and_history_is_retained(world):
    from story_channel.files import atomic_write
    from story_channel.media_utils import file_hash
    engine,lid,provider = prepare(world)
    folder = root(engine,lid)/"panorama"
    original = file_hash(folder/"atlas.png")
    saved = folder/"combinations"/"old"/"panorama.png"
    saved.parent.mkdir(parents=True)
    Image.new("RGB",(2048,1024),"red").save(saved)
    Image.open(saved).save(folder/"atlas.png")
    qa = json.loads((folder/"seam_metadata.json").read_text())
    qa.update(combinationId="old",humanLayoutReviewed=True,atlasHash=file_hash(folder/"atlas.png"))
    atomic_write(folder/"seam_metadata.json",json.dumps(qa))
    assert not pair_ready(engine,engine.load(),lid)
    state = engine.inspect()
    assert pair_ready(engine,state,lid)
    assert file_hash(folder/"atlas.png") == original
    assert saved.exists() and len(provider.calls) == 4


def test_master_approval_gates_and_old_key_alias(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True, image_provider=Sectors())
    lid = BackgroundLibrary(engine).add("Room", "Room", reference_type="KEY", floor_plan="Fixed center", object_layout={"door":"west"}).location_id
    from story_channel.floor_plan_guides import publish_guides
    publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    state = engine.generate("backgrounds", subject=lid)
    assert f"backgrounds:{lid}__east_bridge" in state.records
    assert f"backgrounds:{lid}__front_panorama" not in state.records
    for name in MASTERS:
        with pytest.raises(ApprovalRequired,match="East Bridge"):
            engine.generate("backgrounds",subject=lid+"__"+name)
    with pytest.raises(ApprovalRequired):
        engine.generate("backgrounds",subject=lid)
    approve_bridges(engine,lid)
    state = engine.generate("backgrounds",subject=lid)
    assert f"backgrounds:{lid}__front_panorama" in state.records
    assert f"backgrounds:{lid}__master" not in state.records
    with pytest.raises(ApprovalRequired):
        engine.generate("backgrounds",subject=lid+"__view_045")
    assert len(engine.image_provider.calls) == 4


def test_master_revisions_revoke_pair_and_clear_published_projections(world):
    engine, lid, provider = prepare(world)
    state = engine.generate("backgrounds", subject=lid)
    previous = state.records[f"backgrounds:{lid}__back_panorama"].asset.asset_id
    state = engine.generate("backgrounds", subject=lid+"__back_panorama", force=True)
    assert not pair_ready(engine, state, lid)
    assert not (root(engine, lid) / "yaw/yaw_045.png").exists()
    assert all(state.records[f"backgrounds:{lid}__view_{angle:03d}"].status == "stale" for angle in range(0,360,45))
    engine.apply_revision("backgrounds", lid+"__back_panorama", previous)
    assert not pair_ready(engine, engine.load(), lid)
    approve(engine, lid, "back_panorama", previous)
    assert pair_ready(engine, engine.load(), lid)
    assert len(provider.calls) == 5


def test_background_ui_shows_dual_approvals_seam_qa_and_local_actions(world):
    from streamlit.testing.v1 import AppTest
    engine, lid, _ = prepare(world)
    engine.generate("backgrounds", subject=lid)
    def display(engine, lid):
        from story_channel.background_assets_ui import background_outputs
        background_outputs(engine, lid)
    app = AppTest.from_function(display, args=(engine, lid)).run()
    assert not app.exception
    for group in ("bridges","masters"):
        assert app.button(key=f"approve-location-pair-{lid}-{group}").disabled
        assert app.button(key=f"approve-location-pair-{lid}-{group}").label == "Approved"
    assert app.button(key=f"background-regenerate-{engine.project_id}-{lid}__view_045").label == "Reproject"
    assert app.button(key="recheck-panorama-pair-"+lid)
    assert any("Panorama seam QA" in message.value for message in app.success)


def test_repair_rejects_any_changed_protected_pixel(tmp_path):
    original, repaired, mask = [tmp_path / name for name in ("a.png", "b.png", "mask.png")]
    Image.new("RGB", (10,10), "white").save(original)
    Image.new("L", (10,10), 0).save(mask)
    Image.new("RGB", (10,10), "white").save(repaired)
    assert verify_protected(original, repaired, mask)
    pixels = np.asarray(Image.open(repaired)).copy()
    pixels[0,0] = 0
    Image.fromarray(pixels).save(repaired)
    with pytest.raises(ValueError, match="protected"):
        verify_protected(original, repaired, mask)


def test_corresponding_overlap_regions_must_match():
    front = np.full((100,200,3),100,dtype=np.uint8)
    back = front.copy()
    assert assess(front,back)["passed"]
    back[:,:20] = 255
    back[:,-20:] = 0
    assert not assess(front,back)["passed"]


def test_openai_master_prompt_keeps_full_layout_and_sector_coverage(world, monkeypatch):
    from story_channel.visual_generation import VisualGeneration
    engine, lid, _ = prepare(world)
    state = engine.load()
    inputs = VisualGeneration(engine)
    spec = inputs.get("backgrounds", lid, state)
    engine.demo = False
    monkeypatch.setattr("story_channel.dual_panorama.approved", lambda *args: True)
    def no_compaction(*args, **kwargs):
        pytest.fail("OpenAI panorama prompts must retain full context instead of Runway compaction")
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt", no_compaction)
    for name in MASTERS:
        prompt, refs = inputs.request("backgrounds", lid+"__"+name, state, spec=spec, shorten=True)
        assert 1000 < len(prompt) <= 32000
        assert "equirectangular panorama sector" in prompt
        assert "latitude +90 to -90" in prompt and "No people, characters, reflections, visible text" in prompt
        assert "floorPlan" in prompt and "east wall" in prompt
        if name == "front_panorama":
            assert "faces NORTH" in prompt and len(refs) == 5
        else:
            assert "faces SOUTH" in prompt and len(refs) == 5
            assert "Image 4 is the APPROVED EAST BRIDGE" in prompt
            assert refs[0].name == "canvas.png"
            assert refs[1].name == "back_panorama_camera.png"


def test_missing_atlas_blocks_views_until_local_recheck(world):
    from story_channel.dual_panorama import recheck
    engine, lid, provider = prepare(world)
    engine.generate("backgrounds", subject=lid)
    (root(engine, lid) / "panorama/atlas.png").unlink()
    state = engine.inspect()
    assert state.records[f"backgrounds:{lid}__view_045"].status == "stale"
    recheck(engine, lid)
    assert pair_ready(engine, engine.load(), lid) and len(provider.calls) == 4
    missing = root(engine, lid) / "yaw/yaw_045.png"
    missing.unlink()
    assert engine.inspect().records[f"backgrounds:{lid}__view_045"].status == "stale"
    state = engine.generate("backgrounds", subject=lid+"__view_045")
    assert missing.exists() and state.records[f"backgrounds:{lid}__view_045"].status == "succeeded"
    assert len(provider.calls) == 4
