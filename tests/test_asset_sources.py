import pytest
from copy import deepcopy
from types import SimpleNamespace
from pydantic import create_model,ValidationError

from story_channel.asset_sources import edit_source, read_source
from test_assets_ui import prepare


@pytest.mark.parametrize("stage,subject", [("backgrounds", "lighthouse"), ("scene_images", "scene_1"), ("voices", "scene_1_0000_0000")])
def test_edit_related_source_updates_only_target_and_rejects_stale_edits(tmp_path, stage, subject):
    manager, project = prepare(tmp_path)
    from story_channel.pipeline import Pipeline
    flow = Pipeline(manager, project.project_id)
    source = read_source(flow, stage, subject)
    baseline = source.model_dump_json(by_alias=True)
    if stage == "backgrounds":
        source.description = "A lighthouse beneath winter stars"
    elif stage == "scene_images":
        source.prompt.image_prompt = "Moonlight on the lighthouse, long shadows"
    else:
        source.scene.narration = "The keeper watches the moonrise."
    unrelated = flow.artifact("storyboard").scenes[1].model_copy(deep=True)
    edit_source(flow, stage, subject, source.model_dump_json(by_alias=True), expected=baseline)
    assert read_source(flow, stage, subject) == source
    assert flow.artifact("storyboard").scenes[1] == unrelated
    with pytest.raises(ValueError, match="changed in another editor"):
        edit_source(flow, stage, subject, baseline, expected=baseline)


@pytest.mark.parametrize("stage,subject,with_prompt",[("scene_images","scene_13",True),("scene_images","scene_13",False),("voices","scene_13_0000_0000",True)])
def test_source_revalidates_equivalent_models_from_another_module_generation(stage,subject,with_prompt):
    from story_channel.models import Model,Scene
    from story_channel.creative import PromptItem
    def reloaded(model,values):
        other=create_model(model.__name__,__base__=Model,
            **{name:(field.annotation,deepcopy(field)) for name,field in model.model_fields.items()})
        return other.model_validate(values)
    scene=reloaded(Scene,{"sceneId":"scene_13","order":13,"durationSec":4,"locationId":"office",
        "action":"Raise a hand","dialogue":[{"characterId":"azhe","text":"Hello"}]})
    prompt=reloaded(PromptItem,{"sceneId":"scene_13","provider":"openai","model":"image-model",
        "imagePrompt":"Office scene","videoPrompt":"Raise a hand","negativePrompt":"Text, watermark"})
    assert type(scene).__name__=="Scene" and not isinstance(scene,Scene)
    assert type(prompt).__name__=="PromptItem" and not isinstance(prompt,PromptItem)
    pipeline=SimpleNamespace(project_id="project",
        manager=SimpleNamespace(load=lambda pid:SimpleNamespace(steps={"prompts":SimpleNamespace(status="succeeded")} if with_prompt else {})),
        artifact=lambda name:SimpleNamespace(scenes=[scene]) if name=="storyboard" else SimpleNamespace(prompts=[prompt]))
    source=read_source(pipeline,stage,subject)
    assert isinstance(source.scene,Scene) and source.scene.scene_id=="scene_13"
    assert source.scene.dialogue[0].text=="Hello"
    if stage=="scene_images":
        assert isinstance(source.prompt,PromptItem) if with_prompt else source.prompt is None
    # Real schema errors must remain visible rather than bypassing validation.
    scene=scene.model_copy(update={"duration_sec":-1})
    with pytest.raises(ValidationError,match="greater than 0"):
        read_source(pipeline,stage,subject)
