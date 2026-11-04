import json
from pathlib import Path

import httpx
import pytest

from story_channel.animation import AnimationDirector
from story_channel.audio_tags import speech_tags
from story_channel.balanced_providers import ElevenLabsTTSProvider
from story_channel.markdown_exports import storyboard_markdown
from story_channel.media_utils import silence
from story_channel.models import Dialogue, ProviderConfig
from story_channel.modules import build_prompt
from test_assets import assets


def test_script_instructions_and_legacy_dialogue_compatibility():
    text = build_prompt("script", {})
    for word in ("emotions", "vocalizations", "delivery", "pacing", "character performance/accent", "sfx"):
        assert word in text
    assert "clean spoken words" in text
    assert Dialogue(character_id="keeper", text="Stay here.").audio_tags == []
    assert "Preserve each line's audioTags" in build_prompt("storyboard", {})


def test_script_tags_reach_voice_config_and_direction_but_not_subtitles(assets):
    pipeline = assets.creative
    board = pipeline.artifact("storyboard")
    scene = board.scenes[0]
    scene.dialogue[0].audio_tags = ["nervous", "whispers", "sighs"]
    scene.narration_audio_tags = ["slowly", "somber"]
    scene.sfx = ["Wind rattles a loose window frame"]
    pipeline.edit("storyboard", board.model_dump_json(by_alias=True))
    pipeline.approve("storyboard")
    pipeline.run()
    state = assets.load()
    tasks = assets.tasks(state)
    dialogue = next(task for task in tasks if task.stage == "voices" and task.scene_id == scene.scene_id and task.speaker == "keeper")
    narration = next(task for task in tasks if task.stage == "voices" and task.scene_id == scene.scene_id and task.speaker == "narrator")
    subtitle = next(task for task in tasks if task.stage == "subtitles" and task.scene_id == scene.scene_id and task.speaker == "keeper")
    assert assets._config(dialogue, state).options["audio_tags"] == ["nervous", "whispers", "sighs"]
    assert assets._config(narration, state).options["audio_tags"] == ["slowly", "somber"]
    assert dialogue.prompt == subtitle.prompt == scene.dialogue[0].text
    assert not subtitle.audio_tags
    direction = AnimationDirector(assets.manager, assets.project_id).prepare()
    assert direction.plan.performances[0].audio_tags == scene.dialogue[0].audio_tags
    markdown = storyboard_markdown(board)
    assert "[nervous] [whispers] [sighs]" in markdown
    assert scene.sfx[0] in markdown


@pytest.mark.parametrize("model", ["eleven_v3", "eleven_v4", "eleven_multilingual_v2"])
def test_elevenlabs_receives_explicit_tags_and_keeps_casting(tmp_path, monkeypatch, model):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-only")
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        assert request.url.path.endswith("/saved-voice-id")
        return httpx.Response(200, content=silence(.5))

    provider = ElevenLabsTTSProvider(httpx.Client(transport=httpx.MockTransport(handler)))
    config = ProviderConfig(provider="elevenlabs", model=model, options={
        "output_path": str(tmp_path / "sample.wav"), "audio_tags": ["curious", "[whispers]", "sighs", "curious"],
        "performance": {"emotion": "angry"}})
    result = provider.generate("Where did it go?", "saved-voice-id", config)
    expected = "Where did it go?" if model.endswith("v2") else "[curious] [whispers] [sighs] Where did it go?"
    assert calls[0]["text"] == expected
    assert provider.last_usage["audio_characters"] == len(expected)
    assert Path(result.file_path).exists()


def test_edited_performance_tags_override_script_tags():
    assert speech_tags({"audio_tags": ["angry"], "performance": {"audio_tags": ["calm", "softly"]}}) == ["calm", "softly"]


def test_script_exports_include_tags_sfx_and_draft_acting_intent(assets):
    script = assets.creative.artifact("script")
    # Demo scenes may have narration only; add an explicitly directed line.
    scene = script.scenes[0]
    scene.dialogue = [Dialogue(character_id="keeper", text="Stay close.", audio_tags=["nervous", "whispers"])]
    scene.narration_audio_tags = ["slowly"]
    scene.sfx = ["A wooden door creaks open"]
    assets.creative._write_outputs("script", script)
    markdown = (assets.path / "script.md").read_text(encoding="utf-8")
    assert "[nervous] [whispers]" in markdown
    assert "[slowly]" in markdown
    assert "A wooden door creaks open" in markdown
    intent = json.loads((assets.path / "performance_intent.json").read_text(encoding="utf-8"))
    assert intent["performances"][0]["audioTags"] == ["nervous", "whispers"]
