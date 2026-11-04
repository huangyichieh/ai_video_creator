import pytest

from story_channel import provider_prompts
from story_channel.balanced_providers import RunwayImageProvider, KlingVideoProvider, RunwayMedia
from story_channel.llm import ProviderError
from story_channel.models import ProviderConfig
from story_channel.runway_prompt_limits import prompt_units, require_prompt_budget


@pytest.mark.parametrize("text", ["x" * 999, "中" * 999, "😀" * 499 + "x"])
def test_strict_budget_accepts_999_units(text):
    assert prompt_units(text) == 999
    assert require_prompt_budget(text) == text


@pytest.mark.parametrize("kind", ["image", "video", "task"])
@pytest.mark.parametrize("text", ["x" * 1000, "😀" * 500])
def test_no_oversized_runway_request_or_upload(tmp_path, monkeypatch, kind, text):
    provider = {"image": RunwayImageProvider, "video": KlingVideoProvider, "task": RunwayMedia}[kind]()
    def forbidden(*args, **kwargs):
        pytest.fail("Over-budget prompts must be rejected before any network call")
    monkeypatch.setattr(provider, "_request", forbidden)
    monkeypatch.setattr(provider, "upload", forbidden)
    config = ProviderConfig(provider="runway", model="gen4_image",
        options={"output_path": str(tmp_path / "output.png"), "size": "1280x720", "seconds": 3})
    with pytest.raises(ProviderError, match="shorter than 1000"):
        if kind == "image":
            provider.generate(text, [tmp_path / "reference.png"], config)
        elif kind == "video":
            provider.generate_clip(text, [tmp_path / "reference.png"], config)
        else:
            provider.task("/future_endpoint", {"promptText": text}, config, "image_count", 1)


def test_video_prompts_are_compiled_at_1000_not_2500(monkeypatch):
    calls = []
    def modify(pipeline, current, instructions, stage, **kwargs):
        calls.append(instructions)
        result = type(current)(text="Preserve identity. Slow pan right. No text.")
        return kwargs["validate_output"](result)
    monkeypatch.setattr(provider_prompts, "modify_content", modify)
    short = "x" * 999
    assert provider_prompts.compile_prompt(None, short, video=True) == short
    assert not calls
    assert provider_prompts.compile_prompt(None, "x" * 1000, video=True) == "Preserve identity. Slow pan right. No text."
    assert "999 UTF-16" in calls[0]


def test_compiler_rejects_supplementary_unicode_over_budget(monkeypatch):
    def modify(pipeline, current, instructions, stage, **kwargs):
        return kwargs["validate_output"](type(current)(text="😀" * 500))
    monkeypatch.setattr(provider_prompts, "modify_content", modify)
    with pytest.raises(ValueError, match="exceeds the provider limit"):
        provider_prompts.compile_prompt(None, "x" * 1000, video=True)
