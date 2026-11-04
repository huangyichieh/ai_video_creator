import pytest
from streamlit.testing.v1 import AppTest

from story_channel.audio_assets import add_sfx,items,binding,upload
from story_channel.bgm_library import add,ready
from story_channel.storyboard_audio import generate
from story_channel.media_utils import silence
from test_storyboard_audio_generation import Audio
from test_studio import world


@pytest.mark.parametrize("kind",["SFX","BGM"])
def test_reusable_audio_can_generate_before_any_episode_with_costs_and_player(world,kind):
    manager,pid = world.manager,world.project_id
    if kind == "SFX":
        identity = add_sfx(manager,pid,"Printer click","A short mechanical printer click")
    else:
        identity = "office_music"
        add(manager,pid,identity,"Office music","Light instrumental piano")
    provider = Audio()
    relative = generate(manager,pid,kind,identity,"Printer click" if kind == "SFX" else "Calm piano",3,provider=provider)
    assert len(provider.prompts) == 1 and (manager.project_path(pid)/relative).is_file()
    entry = binding(manager,pid,kind,identity)
    assert ready(manager.project_path(pid),entry)
    assert entry["provenance"]["provider"] == "elevenlabs"
    from story_channel.costs import CostTracker
    assert CostTracker(manager,pid).records()[-1].outcome == "succeeded"
    if kind == "BGM":
        assert items(manager,pid,kind)[identity]["file"] == relative
        assert identity not in items(manager,pid,"SFX")
    def display(manager,pid,kind):
        from story_channel.audio_asset_cards import library
        library(manager,pid,kind)
    app = AppTest.from_function(display,args=(manager,pid,kind),default_timeout=25).run()
    assert not app.exception
    assert not app.get("audio")  # Closed cards do not build players or upload controls.
    app.session_state[f"audio-library-{pid}-{kind}-{identity}-card"] = True
    app.run()
    assert not app.exception
    assert len(app.get("audio")) >= 1
    assert any(item.label == ("Upload BGM" if kind == "BGM" else "Upload SFX") for item in app.get("file_uploader"))
    assert any(item.label == ("Music prompt (optional)" if kind == "BGM" else "Sound effect prompt (optional)") for item in app.text_area)
    assert any(button.label == ("Generate with ElevenLabs (music_v2)" if kind == "BGM" else "Generate with ElevenLabs (eleven_text_to_sound_v2)") and not button.disabled for button in app.button)


@pytest.mark.parametrize("kind",["SFX","BGM"])
def test_upload_replaces_audio_and_preserves_old_file(world,kind):
    manager,pid = world.manager,world.project_id
    if kind == "SFX":
        identity = add_sfx(manager,pid,"Door click","A short wooden door click")
    else:
        identity = "piano"
        add(manager,pid,identity,"Piano","Calm piano")
    old = upload(manager,pid,kind,identity,silence(1),"old.wav","Own recording")
    new = upload(manager,pid,kind,identity,silence(2),"new.wav","Own recording")
    assert old != new
    assert (manager.project_path(pid)/old).is_file()
    assert binding(manager,pid,kind,identity)["file"] == new
    with pytest.raises(ValueError,match="Upload WAV"):
        upload(manager,pid,kind,identity,b"invalid","bad.txt","Own recording")
    assert binding(manager,pid,kind,identity)["file"] == new
    if kind == "BGM":
        assert items(manager,pid,kind)[identity]["revision"] == 2


def test_empty_optional_prompt_uses_description(world,monkeypatch):
    manager,pid = world.manager,world.project_id
    identity = add_sfx(manager,pid,"Clock","A quiet wall clock tick")
    calls = []
    monkeypatch.setattr("story_channel.storyboard_audio_ui.start",lambda manager,pid,callback,*args,**kwargs:callback())
    monkeypatch.setattr("story_channel.storyboard_audio.generate",lambda manager,pid,kind,key,prompt,*args,**kwargs:calls.append(prompt))
    def display(manager,pid):
        from story_channel.audio_asset_cards import library
        library(manager,pid,"SFX")
    app = AppTest.from_function(display,args=(manager,pid),default_timeout=25).run()
    assert not app.exception
    app.session_state[f"audio-library-{pid}-SFX-{identity}-card"] = True
    app.run()
    assert next(item for item in app.text_area if item.label == "Sound effect prompt (optional)").value == ""
    next(button for button in app.button if button.label == "Generate with ElevenLabs (eleven_text_to_sound_v2)").click().run()
    assert not app.exception and calls == ["A quiet wall clock tick"]


def test_reusable_bgm_pixabay_library_supports_manual_and_ai_selection(world):
    from story_channel.pixabay_music import register,use_track,choose
    from test_pixabay_music import Chooser
    manager,pid = world.manager,world.project_id
    track = register(manager,pid,"Office piano","Test artist","https://pixabay.com/music/office-piano-12345/",
        silence(3),"piano.wav",b"License evidence","license.txt",commercial_confirmed=True,content_id_status="not_registered")
    add(manager,pid,"office","Office","Light piano")
    use_track(manager,pid,"office",track)
    assert ready(world.path,binding(manager,pid,"BGM","office"))
    assert items(manager,pid,"BGM")["office"]["provenance"]["track"]["id"] == track
    assert not items(manager,pid,"SFX")
    add(manager,pid,"office_ai","Office AI","Soft hopeful instrumental piano")
    choose(manager,pid,"office_ai",provider=Chooser(track))
    assert ready(world.path,binding(manager,pid,"BGM","office_ai"))
    def display(manager,pid):
        from story_channel.audio_asset_cards import library
        library(manager,pid,"BGM")
    app = AppTest.from_function(display,args=(manager,pid),default_timeout=25).run()
    assert not app.exception
    for identity in ("office","office_ai"):
        app.session_state[f"audio-library-{pid}-BGM-{identity}-card"] = True
    app.run()
    assert len([item for item in app.selectbox if item.label == "Pixabay track"]) == 2
    assert all(not button.disabled for button in app.button if button.label == "Use selected Pixabay track")
