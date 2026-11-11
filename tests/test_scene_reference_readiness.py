import pytest

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.scene_production import SceneProduction
from story_channel.render_models import RenderOptions
from story_channel.storyboard_editor import save
from test_scene_production import episode
from test_studio import world


def test_character_free_scene_uses_its_approved_background_without_global_character_gate(episode):
    _,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    board = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    lid = board["scenes"][0]["locationId"]
    character_scene = board["scenes"][0]["sceneId"]
    from story_channel.models import Scene
    cutaway = Scene(scene_id="clock_cutaway",order=1,duration_sec=1,location_id=lid,
        visual_description="Close-up of the office clock, with no characters.",action="The second hand advances.",shot_type="Close-up",camera_movement="Static camera")
    for scene in board["scenes"]:
        scene["order"] += 1
    board["scenes"].insert(0,cutaway.model_dump(mode="json",by_alias=True))
    save(pipeline,board,source,auto_approve=True)
    assets = AssetPipeline(pipeline.manager,project.project_id)
    assets.generate("scene_images")
    director = AnimationDirector(pipeline.manager,project.project_id)
    director.prepare()
    # A missing episode-wide character approval must not block a clock shot.
    state = assets.load()
    state.character_approvals.clear()
    assets._save(state)
    manifest = pipeline.manager.load(project.project_id)
    manifest.asset_approvals.pop("characters",None)
    pipeline.manager.save(manifest)
    assert director.require_ready("clock_cutaway")
    # A usable visual and matching snapshot are sufficient even without the
    # full visual/voice/costume approval bundle.
    assert director.require_ready(character_scene)
    character_id = next(scene.character_ids[0] for scene in pipeline.artifact("storyboard").scenes if scene.scene_id == character_scene)
    reference = state.records["character_references:"+character_id]
    state.records["character_references:"+character_id] = reference.model_copy(update={"status":"stale"})
    assets._save(state)
    with pytest.raises(ValueError,match="current character visual"):
        director.require_ready(character_scene)
    state.records["character_references:"+character_id] = reference
    snapshot = state.records["scene_images:"+character_scene]
    original_hash = snapshot.asset.generation_params["referenceHashes"]["character_references:"+character_id]
    snapshot.asset.generation_params["referenceHashes"]["character_references:"+character_id] = "old-character-image"
    assets._save(state)
    with pytest.raises(ValueError,match="Regenerate the scene image"):
        director.require_ready(character_scene)
    snapshot.asset.generation_params["referenceHashes"]["character_references:"+character_id] = original_hash
    assets._save(state)
    service = SceneProduction(pipeline.manager,project.project_id)
    clip = service.generate_visual("clock_cutaway",RenderOptions(width=320,height=180,fps=12,music=False,sfx=False,subtitles="none"))
    assert clip["status"] == "succeeded"
    # Required background approval and snapshot integrity are still enforced.
    state = assets.load()
    state.background_approvals.pop(lid,None)
    assets._save(state)
    with pytest.raises(ValueError,match="Approve background .* on Assets"):
        director.require_ready("clock_cutaway")
    state.records["scene_images:clock_cutaway"].status = "stale"
    assets._save(state)
    with pytest.raises(ValueError,match="current scene image"):
        director.require_ready("clock_cutaway")
