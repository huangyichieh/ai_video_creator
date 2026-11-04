import json

import pytest

from story_channel.files import atomic_write,input_hash
from story_channel.file_cache import read_json
from story_channel.library_media import LibraryMedia
from story_channel.prop_drafts import save
from story_channel.check_prop_assets import sync
from story_channel.episode_list_ui import episode_status
from test_scene_production import episode
from test_studio import world


@pytest.mark.parametrize("snapshot_kind",["modern","legacy","absent"])
def test_check_prop_updates_preserve_story_status_and_approvals(episode,snapshot_kind):
    world,project,pipeline = episode
    snapshot = pipeline.path/"storyboard_reference_context.json"
    if snapshot_kind != "modern":
        if snapshot_kind == "legacy":
            data = read_json(snapshot)
            data.pop("assetCatalog",None)
            atomic_write(snapshot,json.dumps(data))
        else:
            snapshot.unlink()
        manifest = world.manager.load(project.project_id)
        manifest.steps["storyboard"].retained_input_hash = pipeline._fingerprint("storyboard")
        world.manager.save(manifest)
    before = pipeline.inspect()
    assert episode_status(world.manager,before) == "READY TO ANIMATE"
    stages = {name:stage.model_dump(mode="json") for name,stage in before.steps.items()}
    approvals = dict(before.approvals)
    content_approvals = dict(before.content_approvals)
    files = {name:(pipeline.path/name).read_bytes() for name in ("story.json","script.json","scenes.json","prompts/prompts.json")}
    old_catalog = pipeline._context("storyboard")["assetCatalog"]
    media = LibraryMedia(world.manager,world.project_id)
    prop = save(media,"inspection_case","Inspection case","Brown case",["closed"],expected=input_hash(None))
    sync(media,project.project_id,"inspection_case")
    assert read_json(snapshot)["assetCatalog"] == old_catalog
    save(media,"inspection_case","Inspection case","Black case",["closed","open"],expected=input_hash(prop))
    sync(media,project.project_id,"inspection_case")
    after = pipeline.inspect()
    assert episode_status(world.manager,after) == "READY TO ANIMATE"
    assert {name:stage.model_dump(mode="json") for name,stage in after.steps.items()} == stages
    assert after.approvals == approvals and after.content_approvals == content_approvals
    assert all((pipeline.path/name).read_bytes() == content for name,content in files.items())
    assert LibraryMedia(world.manager,project.project_id).load()["props"]["inspection_case"]["description"] == "Black case"
    # Explicit regeneration still sees current registered assets.
    assert pipeline._context("storyboard",fresh_assets=True)["assetCatalog"]["props"]["inspection_case"]["description"] == "Black case"


def test_legacy_pin_does_not_accept_real_storyboard_changes_or_restore_approvals(episode):
    world,project,pipeline = episode
    snapshot = pipeline.path/"storyboard_reference_context.json"
    data = read_json(snapshot)
    data.pop("assetCatalog",None)
    atomic_write(snapshot,json.dumps(data))
    manifest = world.manager.load(project.project_id)
    manifest.steps["storyboard"].retained_input_hash = pipeline._fingerprint("storyboard")
    world.manager.save(manifest)
    board = pipeline.artifact("storyboard")
    board.scenes[0].action = "A changed story action"
    atomic_write(pipeline.path/"scenes.json",board.model_dump_json(by_alias=True))
    media = LibraryMedia(world.manager,world.project_id)
    save(media,"inspection_case","Inspection case","Brown case",["closed"],expected=input_hash(None))
    sync(media,project.project_id,"inspection_case")
    assert "assetCatalog" not in read_json(snapshot)
    after = pipeline.inspect()
    assert after.steps["storyboard"].status == "stale"
    assert episode_status(world.manager,after) != "READY TO ANIMATE"
    assert "storyboard" not in after.approvals
