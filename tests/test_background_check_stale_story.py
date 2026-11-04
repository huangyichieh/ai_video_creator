import pytest

from story_channel.assets import AssetPipeline
from story_channel.dual_panorama import MASTERS,pair_ready
from story_channel.storyboard_asset_check import check
from test_scene_production import episode
from test_studio import world


@pytest.mark.parametrize("reason",["stale","unapproved","missing_prompts"])
def test_background_reference_validation_is_independent_of_story_readiness(episode,reason):
    world,project,pipeline = episode
    manifest = world.manager.load(project.project_id)
    if reason == "stale":
        for name in ("storyboard","prompts"):
            manifest.steps[name].status = "stale"
    elif reason == "unapproved":
        manifest.approvals.clear()
        manifest.content_approvals.clear()
    else:
        (pipeline.path/"prompts"/"prompts.json").unlink()
    world.manager.save(manifest)
    saved_manifest = (pipeline.path/"manifest.json").read_bytes()
    engine = AssetPipeline(world.manager,project.project_id)
    state = engine.load()
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    tasks = engine.tasks(state,references_only=True)
    assert {task.subject for task in tasks if task.stage == "backgrounds" and task.subject.startswith(lid+"__")} == {lid+"__"+name for name in MASTERS}
    assert pair_ready(engine,state,lid)
    rows = [row for row in check(world.manager,project.project_id) if row["Category"] == "Background"]
    assert rows and all(row["Ready"] and row["Status"] == "Exists" for row in rows)
    assert all(row["FileExists"] for row in rows)
    assert not engine.can_generate("scene_images",state)
    assert (pipeline.path/"manifest.json").read_bytes() == saved_manifest


def test_stale_story_does_not_hide_corrupted_background_images(episode):
    world,project,pipeline = episode
    manifest = world.manager.load(project.project_id)
    manifest.steps["storyboard"].status = "stale"
    manifest.steps["prompts"].status = "stale"
    world.manager.save(manifest)
    engine = AssetPipeline(world.manager,project.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    state = engine.load()
    file = engine.local_path(state.records[f"backgrounds:{lid}__{MASTERS[0]}"].asset.file_path)
    original = file.read_bytes()
    try:
        file.write_bytes(b"damaged image")
        assert not pair_ready(engine,state,lid)
        row = next(row for row in check(world.manager,project.project_id) if row["ID"] == lid)
        assert not row["Ready"] and not row["Approved"]
        assert not row["FileExists"]
    finally:
        file.write_bytes(original)
    assert pair_ready(engine,state,lid)
