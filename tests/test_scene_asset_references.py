from pathlib import Path
from PIL import Image
from story_channel.assets import AssetPipeline
from story_channel.library_media import LibraryMedia
from story_channel.media_providers import DemoImageProvider
from story_channel.media_utils import file_hash
from story_channel.studio import approve_content,content_digest
from story_channel.scene_image_references import request,LOCK
from story_channel.runway_prompt_limits import prompt_units
from test_scene_production import episode
from test_studio import world


def prepare(episode):
    world,project,pipeline = episode
    from test_storyboard_prop_removal import setup
    setup(pipeline)
    pipeline.approve("storyboard")
    pipeline.generate("prompts")
    approve_content(pipeline,"prompts",expected=content_digest(pipeline,"prompts"))
    media = LibraryMedia(world.manager,world.project_id)
    for mode in ("open","closed"):
        media.image("prop:briefcase:"+mode,"Leather case "+mode)
    return world,project,pipeline,media


def test_scene_generation_refreshes_and_sends_assets_and_tracks_prop_changes(episode):
    world,project,pipeline,media = prepare(episode)
    from story_channel.prop_revisions import apply
    from story_channel.prop_removal import snapshot
    media.image("prop:briefcase:open","Another version")
    apply(media,"briefcase","open",1,expected=snapshot(media.load(),"briefcase","open"))
    calls = []
    class Capture(DemoImageProvider):
        def generate(self,prompt,references,config):
            calls.append((prompt,list(references),dict(config.options)))
            return super().generate(prompt,references,config)
    engine = AssetPipeline(world.manager,project.project_id,image_provider=Capture())
    scene = pipeline.artifact("storyboard").scenes[0]
    result = engine.generate("scene_images",subject=scene.scene_id)
    assert len(calls) == 1
    prompt,references,options = calls[0]
    entries = options["sceneAssetReferences"]
    assert {item["role"] for item in entries} == {"character","background","prop"}
    assert {item["id"] for item in entries if item["role"] == "character"} == set(scene.character_ids)
    props = [item for item in entries if item["role"] == "prop"]
    assert {item["id"] for item in props} == {"briefcase/open","briefcase/closed"}
    assert next(item for item in props if item["id"] == "briefcase/open")["revision"] == 1
    assert [path.relative_to(engine.path).as_posix() for path in references] == [item["file"] for item in entries]
    assert all(file_hash(engine.local_path(item["file"])) == item["fileHash"] for item in entries)
    assert LOCK in prompt
    saved = result.records["scene_images:"+scene.scene_id].asset.generation_params
    assert saved["config"]["sceneAssetReferences"] == entries
    task = next(task for task in engine.tasks(result) if task.key == "scene_images:"+scene.scene_id)
    old_hash = engine._hash(task,result)
    media.image("prop:briefcase:open","Updated applied prop")
    from story_channel.check_asset_sync import synchronize
    synchronize(world.manager,project.project_id)
    assert engine._hash(task,engine.load()) != old_hash
    assert "storyboard" in pipeline.inspect().approvals


def test_shortened_runway_scene_prompt_keeps_asset_lock_and_roles(episode,monkeypatch):
    world,project,pipeline,media = prepare(episode)
    from story_channel.check_asset_sync import synchronize
    synchronize(world.manager,project.project_id)
    engine = AssetPipeline(world.manager,project.project_id)
    state = engine.load()
    task = next(task for task in engine.tasks(state) if task.stage == "scene_images")
    config = engine._config(task,state)
    limits = []
    def compact(pipeline,text,**kwargs):
        limits.append(kwargs["max_units"])
        return "A character opens the case beside the desk."
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt",compact)
    prompt,refs = request(engine,state,task,config,runway=True)
    assert limits and prompt.endswith(LOCK)
    assert "prop:briefcase/open" in prompt
    assert prompt_units(prompt) < 830  # room remains for packing instructions
    assert len(refs) == len(config.options["scene_reference_roles"])


def test_scene_image_modification_uses_supported_edit_model_and_preserves_references(episode):
    world,project,pipeline = episode
    from story_channel.visual_generation import EDIT_MODEL
    engine = AssetPipeline(world.manager,project.project_id)
    scene = pipeline.artifact("storyboard").scenes[0]
    state = engine.generate("scene_images",subject=scene.scene_id)
    original = state.records["scene_images:"+scene.scene_id].asset
    calls = []
    class Editor(DemoImageProvider):
        def generate(self,prompt,references,config):
            calls.append((prompt,list(references),config))
            asset = super().generate(prompt,references,config)
            asset.provider,asset.model = "openai",config.model
            return asset
    engine.modification_image_provider = Editor()
    result = engine.modify_with_openai("scene_images",scene.scene_id,"Keep the actor's face unchanged; raise the right hand.",expected_asset_id=original.asset_id)
    assert len(calls) == 1
    prompt,references,config = calls[0]
    assert config.provider == "openai" and config.model == EDIT_MODEL == "gpt-image-2.5-sunburst"
    assert references[-1] == engine.local_path(original.file_path)
    assert {item["role"] for item in config.options["scene_reference_roles"]} >= {"character","background","current"}
    assert LOCK in prompt and "raise the right hand" in prompt
    assert engine.local_path(original.file_path).is_file()
    assert result.records["scene_images:"+scene.scene_id].asset.model == EDIT_MODEL


def test_runway_scene_packing_separates_cast_layout_and_objects(tmp_path):
    from story_channel.balanced_providers import RunwayImageProvider
    from story_channel.models import ProviderConfig
    paths,roles = [],[]
    for index,role in enumerate(("character","character","background","prop","style")):
        path = tmp_path/f"input-{index}.png"
        Image.new("RGB",(80,80),(index*40,100,120)).save(path)
        paths.append(path)
        roles.append({"role":role,"id":str(index),"file":str(path),"fileHash":file_hash(path)})
    calls = []
    class Provider(RunwayImageProvider):
        def upload(self,path):
            return "runway://"+Path(path).name
        def task(self,endpoint,body,config,*args):
            calls.append(body)
            Image.new("RGB",(80,80),"blue").save(config.options["output_path"])
            return "fake-task"
    config = ProviderConfig(provider="runway",model="gen4_image",options={"output_path":str(tmp_path/"out.png"),"scene_reference_roles":roles})
    asset = Provider().generate("Compose the scene. "+LOCK,paths,config)
    assert [item["tag"] for item in calls[0]["referenceImages"]] == ["cast","location","objects"]
    assert prompt_units(calls[0]["promptText"]) < 1000
    receipt = asset.generation_params["runwayRequest"]
    assert receipt["sceneReferenceInputs"] == roles
    packed = receipt["sceneReferencePacking"]
    assert packed[0]["inputs"] == [1,2] and packed[1]["inputs"] == [3] and packed[2]["inputs"] == [4,5]
    with Image.open(packed[0]["file"]) as sheet:
        assert sheet.width == 2048
