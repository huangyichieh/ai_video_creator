import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from story_channel.effect_providers import ElevenLabsMusicProvider
from story_channel.models import ProviderConfig,GeneratedAsset
from story_channel.storyboard_audio import generate,status,task_key
from story_channel.storyboard_asset_check import check,document
from story_channel.costs import CostTracker
from story_channel.media_utils import silence,write_bytes
from story_channel.error_reports import reports
from test_scene_production import episode
from test_studio import world


def test_music_provider_uses_documented_endpoint_and_instrumental_flag(tmp_path,monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY","test-music-key")
    def handler(request):
        assert request.url.path == "/v1/music"
        assert request.headers["xi-api-key"] == "test-music-key"
        assert json.loads(request.content) == {"prompt":"Gentle piano","music_length_ms":3000,"model_id":"music_v2","force_instrumental":True}
        return httpx.Response(200,content=b"music",headers={"song-id":"song-123","character-cost":"12"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = ElevenLabsMusicProvider(client)
        asset = provider.generate("Gentle piano",ProviderConfig(provider="elevenlabs",model="music_v2",options={"duration":3,"output_path":str(tmp_path/"audio.mp3")}))
        assert Path(asset.file_path).read_bytes() == b"music"
        assert provider.last_usage["provider_job_id"] == "song-123"
        assert provider.last_usage["billing_units"] == 12


class Audio:
    last_usage = {}
    def __init__(self,fail=False):
        self.fail,self.prompts = fail,[]
    def generate(self,prompt,config):
        self.prompts.append(prompt)
        self.last_usage = {"audio_seconds":config.options["duration"]}
        if self.fail:
            raise RuntimeError("Audio request failed")
        write_bytes(Path(config.options["output_path"]),silence(config.options["duration"]))
        return GeneratedAsset(type="audio",provider="elevenlabs",model=config.model,file_path=config.options["output_path"],prompt_hash="a"*64)


@pytest.mark.parametrize("kind",["SFX","BGM"])
def test_generated_audio_updates_binding_records_cost_and_preserves_previous_file(episode,kind):
    world,project,pipeline = episode
    row = next(row for row in check(world.manager,project.project_id) if row["Category"] == kind)
    world.manager.settings.sfx_cost_per_second = Decimal(".02")
    world.manager.settings.music_cost_per_minute = Decimal(".60")
    filename = "storyboard_sfx.json" if kind == "SFX" else "storyboard_bgm.json"
    old = document(pipeline.path,filename)["entries"][row["ID"]]
    provider = Audio()
    relative = generate(world.manager,project.project_id,kind,row["ID"],"Rain" if kind == "SFX" else "Gentle piano",3,provider=provider)
    assert (pipeline.path/relative).is_file() and (pipeline.path/old["file"]).is_file()
    record = CostTracker(world.manager,project.project_id).records()[-1]
    assert record.outcome == "succeeded"
    assert record.cost == (Decimal(".06") if kind == "SFX" else Decimal(".03"))
    binding = document(pipeline.path,filename)["entries"][row["ID"]]
    assert binding["file"] == relative and binding["provenance"]["provider"] == "elevenlabs"
    assert status(world.manager,project.project_id,kind,row["ID"])["status"] == "succeeded"
    if kind == "SFX":
        assert "no intelligible speech" in provider.prompts[0]
    else:
        assert "no vocals" in provider.prompts[0]


def test_audio_failure_records_report_and_success_clears_it(episode):
    world,project,pipeline = episode
    row = next(row for row in check(world.manager,project.project_id) if row["Category"] == "SFX")
    before = document(pipeline.path,"storyboard_sfx.json")["entries"][row["ID"]]
    with pytest.raises(RuntimeError,match="Audio request failed"):
        generate(world.manager,project.project_id,"SFX",row["ID"],"Rain",3,provider=Audio(fail=True))
    assert document(pipeline.path,"storyboard_sfx.json")["entries"][row["ID"]] == before
    assert reports(pipeline.path,task_key("SFX",row["ID"]))
    assert CostTracker(world.manager,project.project_id).records()[-1].outcome == "failed"
    generate(world.manager,project.project_id,"SFX",row["ID"],"Rain",3,provider=Audio())
    assert not reports(pipeline.path,task_key("SFX",row["ID"]))


def test_check_assets_exposes_both_elevenlabs_actions(episode,monkeypatch):
    from streamlit.testing.v1 import AppTest
    world,project,pipeline = episode
    (pipeline.path/"storyboard_asset_approvals.json").unlink(missing_ok=True)
    calls = []
    monkeypatch.setattr("story_channel.storyboard_audio_ui.start",lambda manager,pid,callback,*args,**kwargs:callback())
    monkeypatch.setattr("story_channel.storyboard_audio.generate",lambda manager,pid,kind,key,prompt,duration,**kwargs:calls.append((pid,kind,key,duration)))
    def display(manager,project_id):
        from story_channel.storyboard_asset_check_ui import render
        render(manager,project_id)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    assert not app.exception
    for panel in app.expander:
        if panel.key and panel.key.startswith("check-sfx-") and panel.key.endswith("-card"):
            app.session_state[panel.key] = True
    app.run()
    next(b for b in app.button if b.label == "Generate with ElevenLabs (eleven_text_to_sound_v2)").click().run()
    assert not app.exception
    next(b for b in app.button if b.label == "Generate with ElevenLabs (music_v2)").click().run()
    assert not app.exception
    assert {item[1] for item in calls} == {"SFX","BGM"}
    assert next(b for b in app.button if b.label == "AI choose from Pixabay Music").disabled
