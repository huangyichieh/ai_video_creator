"""Split one saved story beat and renumber the complete storyboard atomically."""
import math

from pydantic import Field

from .creative import Storyboard
from .models import Model, Scene
from .storyboard_editor import save


class SceneSplit(Model):
    scenes: list[Scene] = Field(min_length=2,max_length=20)


def split(pipeline,scene_id,expected,instructions,*,provider=None):
    prompts = [text.strip() for text in instructions]
    if not 2 <= len(prompts) <= 20 or not all(prompts):
        raise ValueError("Provide a prompt for each of 2–20 resulting scenes.")
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    if source != expected:
        raise ValueError("Content changed in another session. Reload before splitting this scene.")
    board = Storyboard.model_validate_json(source)
    index = next((i for i,scene in enumerate(board.scenes) if scene.scene_id == scene_id),None)
    if index is None:
        raise ValueError("This storyboard scene no longer exists.")
    original = board.scenes[index]
    context = pipeline._context("storyboard")
    context["manualStoryboardEdit"] = True
    context["storyboard"] = board.model_dump(mode="json",by_alias=True)
    context["sceneToSplit"] = original.model_dump(mode="json",by_alias=True)
    seeds = []
    for number in range(len(prompts)):
        scene = original.model_copy(deep=True)
        scene.scene_id,scene.order = f"scene_{number+1:02d}",number+1
        scene.duration_sec = original.duration_sec/len(prompts)
        scene.status,scene.asset_refs = "draft",[]
        if number:
            scene.dialogue,scene.narration,scene.narration_audio_tags = [],"",[]
        seeds.append(scene)

    def candidate(result):
        scenes = [*board.scenes[:index],*result.scenes,*board.scenes[index+1:]]
        scenes = [scene.model_copy(deep=True) for scene in scenes]
        for number,scene in enumerate(scenes,1):
            scene.scene_id,scene.order = f"scene_{number:02d}",number
            scene.status,scene.asset_refs = "draft",[]
        return Storyboard.model_validate({**board.model_dump(),"scenes":scenes})

    def validate(result):
        if len(result.scenes) != len(prompts):
            raise ValueError("Return exactly the requested number of split scenes.")
        if not math.isclose(sum(scene.duration_sec for scene in result.scenes),original.duration_sec,abs_tol=.001):
            raise ValueError("Split durations must add up to the original scene duration.")
        if [line for scene in result.scenes for line in scene.dialogue] != original.dialogue:
            raise ValueError("Keep every original dialogue line exactly once, in its original order, including audio tags.")
        narration = "".join("".join(scene.narration for scene in result.scenes).split())
        if narration != "".join(original.narration.split()):
            raise ValueError("Keep the original narration exactly once and in order.")
        for scene in result.scenes:
            if (scene.location_id != original.location_id or scene.target_area != original.target_area
                    or scene.bgm_id != original.bgm_id
                    or not set(scene.character_ids) <= set(original.character_ids)
                    or not set(scene.prop_ids) <= set(original.prop_ids)):
                raise ValueError("Split scenes must reuse the original background, target area, characters, props and BGM.")
            if scene.narration and scene.narration_audio_tags != original.narration_audio_tags:
                raise ValueError("Preserve narration audio tags in each narrated split scene.")
        from .modules import validate_output
        validate_output("storyboard",candidate(result),context)
        return result

    from .modification import modify_content
    request = (
        "Split ONLY sceneToSplit into consecutive cinematic scenes following these ordered prompts: "
        +"; ".join(f"{i}. {prompt}" for i,prompt in enumerate(prompts,1))
        +". Return exactly "+str(len(prompts))+" scenes. Preserve the story beat, language and total durationSec "
        +str(original.duration_sec)+". Distribute the complete original dialogue lines and narration across the scenes "
        "exactly once and in order, without rewriting spoken text or its audio tags. Adjust action, visualDescription, "
        "shotType and camera movement to match each prompt. Reuse the original locationId, targetArea and bgmId; "
        "characters and props may only be subsets of the original. Do not introduce new asset requirements. "
        "Use status draft and empty assetRefs. Do not rewrite neighboring scenes. Scene IDs and orders will be assigned by the app.")
    result = modify_content(pipeline,SceneSplit(scenes=seeds),request,"storyboard",context=context,provider=provider,
        allow_new_ids=True,task_key="storyboard_split:"+scene_id,validate_output=validate,retry_validation=True)
    return save(pipeline,candidate(result).model_dump(mode="json",by_alias=True),expected)
