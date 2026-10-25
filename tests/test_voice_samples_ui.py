import pytest

from story_channel.assets import AssetPipeline
from story_channel.identity import IdentityManager
from test_assets_ui import prepare
from test_identity_ui import open_assets
from test_ui import finish_background


@pytest.mark.parametrize("narrator", [False, True])
def test_play_uses_saved_voice_preserves_draft_and_replays_without_refetch(tmp_path, monkeypatch, narrator):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    cid = next(iter(library.voices))
    library.voices[cid].voice_id = "nova"
    identity.save(library, expected=expected)
    speaker = "narrator" if narrator else cid
    subject = "_narrator" if narrator else cid
    saved_id = AssetPipeline(manager, project.project_id).load().voice_map.get("narrator", "alloy") if narrator else "nova"
    calls = []

    def sample(manager, project_id, speaker_id, voice_id, **kwargs):
        calls.append((speaker_id, voice_id, kwargs))
        return {"voice_id": voice_id, "source": "https://storage.googleapis.com/test.mp3", "format": "audio/mpeg",
                "name": "Sample voice", "kind": "ElevenLabs library sample"}

    monkeypatch.setattr("story_channel.voice_samples_ui.saved_voice_sample", sample)
    app = open_assets(manager, monkeypatch)
    input_key = f"asset-narrator-{project.project_id}" if narrator else f"animation-preview-voice-{project.project_id}-{cid}"
    app.text_input(key=input_key).set_value("echo").run()
    key = f"voice-sample-play-{project.project_id}-{subject}"
    app.button(key=key).click().run()
    finish_background(app)
    assert not app.exception
    assert calls[0][0:2] == (speaker, saved_id)
    assert calls[0][2]["narrator"] is narrator
    assert app.text_input(key=input_key).value == "echo"
    assert any(item.proto.url == "https://storage.googleapis.com/test.mp3" and item.proto.autoplay for item in app.get("audio"))
    app.button(key=key).click().run()
    assert len(calls) == 1
    assert app.session_state[f"asset-voice-sample-{project.project_id}-{subject}-play"] == 2
    save_key = f"save-narrator-voice-{project.project_id}" if narrator else f"animation-voice-save-{project.project_id}-{cid}"
    app.button(key=save_key).click().run()
    finish_background(app)
    assert not app.exception
    assert not any(item.proto.url == "https://storage.googleapis.com/test.mp3" for item in app.get("audio"))


def test_play_is_disabled_until_character_voice_saved(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    library = IdentityManager(manager, project.project_id).prepare()
    cid = next(iter(library.voices))
    app = open_assets(manager, monkeypatch)
    key = f"voice-sample-play-{project.project_id}-{cid}"
    assert app.button(key=key).disabled
    app.text_input(key=f"animation-preview-voice-{project.project_id}-{cid}").set_value("draft-only").run()
    assert app.button(key=key).disabled


def test_shared_character_and_narrator_sample_players_do_not_collide(tmp_path, monkeypatch):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    cid = next(iter(library.voices))
    library.voices[cid].voice_id = "nova"
    identity.save(library, expected=expected)
    AssetPipeline(manager, project.project_id).set_voice("narrator", "nova")

    def sample(manager, project_id, speaker_id, voice_id, **kwargs):
        return {"voice_id": voice_id, "source": "https://storage.googleapis.com/shared.mp3", "format": "audio/mpeg",
                "name": "Shared voice", "kind": "ElevenLabs library sample"}

    monkeypatch.setattr("story_channel.voice_samples_ui.saved_voice_sample", sample)
    app = open_assets(manager, monkeypatch)
    character_key = f"voice-sample-play-{project.project_id}-{cid}"
    narrator_key = f"voice-sample-play-{project.project_id}-_narrator"
    for key in (character_key, narrator_key, character_key, narrator_key):
        app.button(key=key).click().run()
        finish_background(app)
        assert not app.exception
    players = [item.proto for item in app.get("audio")]
    assert len(players) == 2
    assert len({player.id for player in players}) == 2
    assert {player.url for player in players} == {"https://storage.googleapis.com/shared.mp3"}
    app.button(key="nav-final").click().run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert len(app.get("audio")) == 2
