import json

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.files import input_hash,atomic_write
from story_channel.media_providers import DemoImageProvider
from story_channel.storyboard_editor import save
from test_scene_production import episode
from test_studio import world


def update_scene(pipeline,visual="Updated visual: the keeper stands beside the window"):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    data["scenes"][0].update(visualDescription=visual,action="Updated action: raise the left hand beside the window",
        shotType="close-up",cameraMovement="slow push in",cameraStartAngle=90,cameraEndAngle=90,
        cameraPitch=15,cameraFov=55,characterPosition="beside the east window",interactionTarget="window")
    save(pipeline,data,source)
    pipeline.approve("storyboard")
    return pipeline.artifact("storyboard").scenes[0]


def test_same_asset_engine_regenerates_from_latest_saved_storyboard_and_records_provenance(episode):
    _,project,pipeline = episode
    calls = []
    class Capture(DemoImageProvider):
        def generate(self,prompt,references,config):
            calls.append((prompt,list(references),config))
            return super().generate(prompt,references,config)
    engine = AssetPipeline(pipeline.manager,project.project_id,image_provider=Capture())
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    engine.generate("scene_images",subject=sid)
    old_prompt = calls[-1][0]
    updated = update_scene(pipeline)
    result = engine.generate("scene_images",subject=sid,force=True)
    prompt,refs,config = calls[-1]
    assert len(calls) == 2 and old_prompt != prompt
    assert updated.visual_description in prompt and updated.action in prompt
    assert updated.camera_movement in prompt and updated.character_position in prompt
    assert '"cameraPitch": 15' in prompt and '"cameraFov": 55' in prompt
    assert "CURRENT STORYBOARD SCENE" in prompt
    assert config.options["storyboardScene"] == updated.model_dump(mode="json",by_alias=True)
    assert config.options["storyboardSceneHash"] == input_hash(config.options["storyboardScene"])
    saved = result.records["scene_images:"+sid].asset.generation_params["config"]
    assert saved["storyboardSceneHash"] == config.options["storyboardSceneHash"]
    assert refs


def test_old_prompt_compiler_receipt_is_refreshed_locally_from_current_storyboard(episode,monkeypatch):
    _,_,pipeline = episode
    path = pipeline.path/"prompts"/"preparation.json"
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["compilerVersion"] = "legacy-without-action"
    atomic_write(path,json.dumps(receipt))
    before = pipeline.manager.load(pipeline.project_id).steps["prompts"].revision
    monkeypatch.setattr(pipeline,"_generate",lambda *a,**k: (_ for _ in ()).throw(AssertionError("No AI call expected")))
    pipeline.inspect()
    from story_channel.animation_prompt_preparation import COMPILER_VERSION,preparation_current
    manifest = pipeline.manager.load(pipeline.project_id)
    assert manifest.steps["prompts"].revision == before+1
    assert preparation_current(pipeline,manifest)
    assert json.loads(path.read_text(encoding="utf-8"))["compilerVersion"] == COMPILER_VERSION
    scene = pipeline.artifact("storyboard").scenes[0]
    assert scene.action in pipeline.artifact("prompts").prompts[0].image_prompt


def test_scene_details_refresh_clean_cache_but_preserve_unsaved_draft(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    from story_channel.character_assets_ui import detail_prefix
    prefix = detail_prefix(engine,"scene_images",sid)
    def display(engine,sid):
        from story_channel.scene_image_cards_ui import card
        card(engine,sid,"sync-scene-card")
    app = AppTest.from_function(display,args=(engine,sid),default_timeout=25).run()
    assert not app.exception
    original = app.session_state[prefix+"-draft"]
    updated = update_scene(pipeline)
    app.run()
    assert not app.exception
    assert app.session_state[prefix+"-draft"] != original
    assert json.loads(app.session_state[prefix+"-draft"])["scene"]["visualDescription"] == updated.visual_description
    assert app.session_state[prefix+"-baseline"] == app.session_state[prefix+"-draft"]
    draft = json.loads(app.session_state[prefix+"-draft"])
    draft["scene"]["action"] = "Unsaved local draft to keep"
    app.session_state[prefix+"-draft"] = json.dumps(draft,ensure_ascii=False)
    pending = app.session_state[prefix+"-draft"]
    latest = update_scene(pipeline,"Another saved visual: close-up at the door")
    app.run()
    assert not app.exception
    assert app.session_state[prefix+"-draft"] == pending
    assert app.button(key="retry-asset-scene_images:"+sid).disabled
    assert any("saved source changed" in item.value for item in app.warning)
    app.session_state[prefix+"-details-panel"] = True
    app.run()
    next(b for b in app.button if b.label == "Revert all changes").click().run()
    assert not app.exception
    assert json.loads(app.session_state[prefix+"-draft"])["scene"]["visualDescription"] == latest.visual_description
    assert app.session_state[prefix+"-draft"] == app.session_state[prefix+"-baseline"]
