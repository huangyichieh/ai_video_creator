"""Regenerate a single saved shot without rewriting neighboring scenes."""
import json

from .creative import Storyboard
from .storyboard_editor import inventory,save


def regenerate(pipeline,scene_id,expected,instructions="",*,provider=None):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    if source != expected:
        raise ValueError("Content changed in another session. Reload before regenerating this scene.")
    board = Storyboard.model_validate_json(source)
    original = next((scene for scene in board.scenes if scene.scene_id == scene_id),None)
    if original is None:
        raise ValueError("This storyboard scene no longer exists.")
    context = pipeline._context("storyboard")
    catalog = inventory(pipeline)
    context["storyboard"] = board.model_dump(mode="json",by_alias=True)
    context["availableBackgrounds"] = catalog["backgrounds"]
    from .storyboard_details import reference_context
    context.update(reference_context(pipeline.manager,pipeline.project_id))
    from .storyboard_assets import asset_catalog
    owner = pipeline.manager.load(pipeline.project_id).world_project_id or pipeline.project_id
    context["assetCatalog"] = asset_catalog(pipeline.manager,owner,include_reusable=True)
    from .storyboard_planning import MINIMAL_ASSETS
    request = (
        MINIMAL_ASSETS+"Regenerate ONLY this storyboard scene as a fresh, coherent cinematic shot, grounded in the approved script and surrounding scenes. "
        "Improve visualDescription, action, shotType, cameraMovement, camera angles, performance staging, SFX and BGM mood as appropriate. "
        "Keep sceneId, order, durationSec, locationId, characterIds, dialogue, narration, narrationAudioTags, "
        "targetArea, propIds and bgmId EXACTLY unchanged. Do not rewrite neighboring scenes or change approved spoken content. "
        "Return one scene only, with status draft and empty assetRefs. User direction: "+(instructions.strip() or "Create a stronger visual treatment of the same story beat."))
    protected = ("scene_id","order","duration_sec","location_id","character_ids","dialogue","narration",
        "narration_audio_tags","target_area","prop_ids","bgm_id")
    def validate(scene):
        if any(getattr(scene,key) != getattr(original,key) for key in protected):
            raise ValueError("Scene regeneration changed protected timing, IDs or script content. Retry while preserving the original scene.")
        from .modules import validate_output
        candidate = board.model_copy(deep=True)
        candidate.scenes = [scene if item.scene_id == scene_id else item for item in candidate.scenes]
        validate_output("storyboard",candidate,context)
        return scene
    from .modification import modify_content
    replacement = modify_content(pipeline,original,request,"storyboard",context=context,provider=provider,
        creation=True,task_key="storyboard_scene:"+scene_id,validate_output=validate)
    data = json.loads(source)
    data["scenes"] = [replacement.model_dump(mode="json",by_alias=True) if item["sceneId"] == scene_id else item for item in data["scenes"]]
    return save(pipeline,data,expected)
