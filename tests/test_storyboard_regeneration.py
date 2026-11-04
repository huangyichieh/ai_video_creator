import json
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.storyboard_regeneration import regenerate
from story_channel.costs import CostTracker
from test_scene_production import episode
from test_studio import world


class Editor:
    last_usage = {"input_tokens":100,"output_tokens":40}
    def __init__(self,change):
        self.change,self.calls = change,[]
        self.prompts = []
    def generate(self,prompt,response_model,config):
        self.prompts.append(prompt)
        data = json.loads(prompt.rsplit("\n",1)[-1])
        self.calls.append(data)
        self.change(data["current"])
        return response_model.model_validate(data["current"])


def test_regenerates_only_selected_scene_and_records_usage(episode):
    world,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    before = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    target = before["scenes"][-1]["sceneId"]
    provider = Editor(lambda scene:scene.update(visualDescription="A tight close-up framed by the window",shotType="close-up"))
    output = regenerate(pipeline,target,source,"Emphasize hesitation",provider=provider)
    after = output.model_dump(mode="json",by_alias=True)
    assert after["scenes"][:-1] == before["scenes"][:-1]
    assert after["scenes"][-1]["visualDescription"] == "A tight close-up framed by the window"
    for key in ("sceneId","order","durationSec","locationId","characterIds","dialogue","narration","targetArea"):
        assert after["scenes"][-1][key] == before["scenes"][-1][key]
    assert len(provider.calls) == 1
    assert provider.calls[0]["current"]["sceneId"] == target
    assert "Emphasize hesitation" in provider.calls[0]["modification_request"]
    assert "New prop descriptions are VISUAL ONLY" in provider.prompts[0]
    assert "Every new scene sfx entry is SOUND ONLY" in provider.prompts[0]
    assert "storyboard" not in world.manager.load(project.project_id).approvals
    assert world.manager.load(project.project_id).steps["prompts"].status == "stale"
    assert CostTracker(world.manager,project.project_id).records()[-1].outcome == "succeeded"


def test_invalid_regeneration_and_stale_request_preserve_storyboard(episode):
    world,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    target = pipeline.artifact("storyboard").scenes[0].scene_id
    provider = Editor(lambda scene:scene.update(durationSec=9999))
    with pytest.raises(ValueError,match="protected timing"):
        regenerate(pipeline,target,source,provider=provider)
    assert (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") == source
    assert CostTracker(world.manager,project.project_id).records()[-1].outcome == "failed"
    with pytest.raises(ValueError,match="another session"):
        regenerate(pipeline,target,source+" ",provider=provider)
    assert len(provider.calls) == 1


def test_scene_buttons_target_their_scene_and_block_unsaved_edits(episode,monkeypatch):
    world,project,pipeline = episode
    calls = []
    monkeypatch.setattr("story_channel.storyboard_regeneration.regenerate",
        lambda pipeline,sid,source,instructions:calls.append((sid,instructions)))
    monkeypatch.setattr("story_channel.ui_tasks.launch",lambda callback,*args,**kwargs:callback())
    def display(pipeline):
        from story_channel.storyboard_editor_ui import render
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    scenes = pipeline.artifact("storyboard").scenes
    buttons = [b for b in app.button if b.key and b.key.endswith("regenerate-scene")]
    assert len(buttons) == len(scenes)
    target = buttons[-1].key
    prompts = [w for w in app.text_input if w.label == "Regeneration prompt (optional)"]
    prompts[-1].input("Make the framing more intimate").run()
    app.button(key=target).click().run()
    assert not app.exception
    assert calls == [(scenes[-1].scene_id,"Make the framing more intimate")]
    next(w for w in app.text_area if w.label == "Action").set_value("Unsaved action change").run()
    app.button(key=target).click().run()
    assert not app.exception
    assert len(calls) == 1
    assert any("Save or revert" in w.value for w in app.warning)
