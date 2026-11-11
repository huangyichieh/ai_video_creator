import pytest

from story_channel.assets import AssetPipeline
from story_channel.asset_models import AssetModification
from story_channel.check_asset_sync import synchronize
from test_scene_production import episode
from test_studio import world


def test_sync_removes_obsolete_shared_edits_and_scene_retry_reuses_current_character_image(episode):
    world,project,pipeline = episode
    parent = AssetPipeline(world.manager,world.project_id)
    engine = AssetPipeline(world.manager,project.project_id)
    state = engine.load()
    char_key = next(key for key,r in state.records.items() if r.stage == "character_references" and r.asset)
    bg_key = next(key for key,r in state.records.items() if r.stage == "backgrounds" and r.asset)
    character = state.records[char_key]
    original_id,revision = character.asset.asset_id,character.revision
    edit = AssetModification(prompt="An obsolete edit removed from the World",
        source_path=character.asset.file_path,source_hash=character.file_hash,source_asset_id=original_id)
    state.asset_modifications[char_key] = edit
    state.asset_modifications[bg_key] = edit.model_copy(deep=True)
    state.style_prompts[char_key] = "An obsolete shared style"
    state.style_prompts[bg_key] = "An obsolete shared background style"
    sid,other = (scene.scene_id for scene in pipeline.artifact("storyboard").scenes[:2])
    scene_key = "scene_images:"+other
    state.asset_modifications[scene_key] = edit.model_copy(update={"prompt":"Keep this episode-only image edit"})
    state.style_prompts["scene_images:"+sid] = "Keep this episode-only style"
    engine._save(state)
    task = next(task for task in engine.tasks(state) if task.key == char_key)
    assert character.input_hash != engine._hash(task,state)
    assert char_key not in parent.load().asset_modifications
    before_approvals = world.manager.load(project.project_id).approvals
    synchronize(world.manager,project.project_id)
    refreshed = engine.inspect()
    assert char_key not in refreshed.asset_modifications and bg_key not in refreshed.asset_modifications
    assert char_key not in refreshed.style_prompts and bg_key not in refreshed.style_prompts
    assert refreshed.asset_modifications[scene_key].prompt == "Keep this episode-only image edit"
    assert refreshed.style_prompts["scene_images:"+sid] == "Keep this episode-only style"
    assert refreshed.records[char_key].asset.asset_id == original_id
    assert refreshed.records[char_key].status == "succeeded"
    assert engine.record_current(refreshed.records[char_key],task,refreshed)
    assert world.manager.load(project.project_id).approvals == before_approvals
    generated = engine.generate("scene_images",subject=sid,force=True)
    assert generated.records["scene_images:"+sid].status == "succeeded"
    assert generated.records[char_key].asset.asset_id == original_id
    assert parent.load().records[char_key].revision == revision


def test_sync_still_rejects_genuinely_missing_character_reference(episode):
    world,project,pipeline = episode
    parent = AssetPipeline(world.manager,world.project_id)
    engine = AssetPipeline(world.manager,project.project_id)
    state = engine.load()
    char_key = next(key for key,r in state.records.items() if r.stage == "character_references" and r.asset)
    relative = state.records[char_key].asset.file_path
    engine.local_path(relative).unlink()
    parent.local_path(relative).unlink()
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    with pytest.raises(ValueError,match="required reference"):
        engine.generate("scene_images",subject=sid,force=True)
