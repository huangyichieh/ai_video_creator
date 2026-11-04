from types import SimpleNamespace
import pytest
from story_channel.models import Scene
from story_channel.sfx_duration import scene_settings
from story_channel.check_asset_sync import audio_settings,save_audio_settings
from story_channel.storyboard_asset_check import sound_id
from test_scene_production import episode
from test_studio import world


@pytest.mark.parametrize("cue,expected,loop",[("Mouse click",.5,False),("Door closing",2.,False),("Ambient rain",12.,True)])
def test_legacy_cues_use_sound_specific_defaults(cue,expected,loop):
    scene = Scene(scene_id="s",order=1,duration_sec=12,location_id="office",sfx=[cue])
    assert scene_settings(scene,cue) == {"duration":expected,"loop":loop}
    assert "sfxTiming" not in scene.model_dump(mode="json",by_alias=True)  # old snapshots retain their hashes


def test_storyboard_timing_drives_both_pages_and_manual_override_wins(episode):
    world,project,pipeline = episode
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    data = pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)
    data["scenes"][0]["sfx"] = ["Door latch clicks"]
    data["scenes"][0]["sfxTiming"] = [{"description":"Door latch clicks","durationSec":.75,"loop":False}]
    from story_channel.storyboard_editor import save
    save(pipeline,data,source)
    identity = sound_id("Door latch clicks")
    for pid in (world.project_id,project.project_id):
        assert audio_settings(world.manager,pid,"SFX",identity) == {"duration":.75,"loop":False}
    save_audio_settings(world.manager,project.project_id,"SFX",identity,"My custom prompt",1.5,False)
    assert audio_settings(world.manager,world.project_id,"SFX",identity)["duration"] == 1.5
    assert audio_settings(world.manager,project.project_id,"SFX",identity)["duration"] == 1.5


def test_cues_are_capped_by_scene_length_and_provider_limit():
    scene = Scene(scene_id="s",order=1,duration_sec=4,location_id="office",sfx=["Wind"],
        sfx_timing=[{"description":"Wind","durationSec":20,"loop":True}])
    assert scene_settings(scene,"Wind")["duration"] == 4
    with pytest.raises(ValueError):
        Scene(scene_id="s",order=1,duration_sec=4,location_id="office",sfx_timing=[{"description":"Wind","durationSec":60}])


def test_ai_schema_exposes_timing_entries_as_a_strict_list():
    from story_channel.llm import strict_schema
    from story_channel.creative import Storyboard
    schema = strict_schema(Storyboard)
    timing = schema["$defs"]["Scene"]["properties"]["sfxTiming"]
    assert timing["type"] == "array"
    assert set(schema["$defs"]["SFXTiming"]["required"]) == {"description","durationSec","loop"}


def test_batch_uses_saved_recommendation_even_if_item_was_never_expanded(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from concurrent.futures import Future
    calls,callbacks = [],[]
    monkeypatch.setattr("story_channel.storyboard_audio_ui.missing_sfx",lambda manager,pid,rows:rows)
    monkeypatch.setattr("story_channel.check_asset_sync.audio_settings",lambda *args:{"duration":.75,"loop":False})
    monkeypatch.setattr("story_channel.check_asset_sync.save_audio_settings",lambda *args:None)
    monkeypatch.setattr("story_channel.audio_assets.binding",lambda *args:{})
    monkeypatch.setattr("story_channel.storyboard_audio.generate",lambda *args,**kwargs:calls.append(args[5]))
    def start(manager,pid,callback,*args):
        callbacks.append(callback)
        return {"future":Future()}
    monkeypatch.setattr("story_channel.storyboard_audio_ui.start",start)
    manager = SimpleNamespace(project_path=lambda pid:__import__("pathlib").Path("unused"))
    def display(manager):
        from story_channel.storyboard_audio_ui import queue_all_sfx
        queue_all_sfx(manager,"episode",[{"Category":"SFX","ID":"click","Asset":"Click"}])
    app = AppTest.from_function(display,args=(manager,)).run()
    assert not app.exception
    callbacks[0]()
    assert calls == [.75]
