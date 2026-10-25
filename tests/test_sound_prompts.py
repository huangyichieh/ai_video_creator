from types import SimpleNamespace

from story_channel.render_models import SoundCue
from story_channel.sound_prompts import SoundDescription, sound_payload, sound_prompt


def test_non_english_description_is_translated_once_and_preserves_sound_intent(tmp_path, monkeypatch):
    calls = []
    def translate(pipeline, current, instructions, stage):
        calls.append((current.text, instructions, stage))
        return SoundDescription(text="Rapid nonverbal breathing, close and dry")
    monkeypatch.setattr("story_channel.modification.modify_content", translate)
    pipeline = SimpleNamespace(path=tmp_path, demo=False, config=SimpleNamespace(model="test-model"))
    payload = sound_payload(SoundCue(name="呼吸急促聲", duration=3), "cues")
    first = sound_prompt(pipeline, payload)
    assert first == sound_prompt(pipeline, payload)
    assert len(calls) == 1 and calls[0][2] == "sfx_prompt"
    assert "Rapid nonverbal breathing" in first and "no intelligible speech" in first
    assert "呼吸急促聲" not in first
    payload["prompt"] = "房門關閉聲"
    sound_prompt(pipeline, payload)
    assert len(calls) == 2


def test_english_prompt_and_ambience_keep_explicit_description_without_translation(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("An English sound prompt does not need an LLM request")
    monkeypatch.setattr("story_channel.modification.modify_content", forbidden)
    pipeline = SimpleNamespace(path=tmp_path, demo=False)
    cue = SoundCue(name="Background", prompt="Quiet rain tapping on a metal roof", duration=12, loop=True)
    prompt = sound_prompt(pipeline, sound_payload(cue, "ambience"))
    assert prompt.startswith(cue.prompt)
    assert "Continuous environmental ambience" in prompt and "Seamless repeating" in prompt
    assert "no intelligible speech" in prompt
    assert "Background" not in prompt
