from decimal import Decimal
import json

import httpx
from openai import OpenAI
import pytest

from story_channel.costs import CostTracker
from story_channel.files import input_hash
from story_channel.identity import IdentityManager
from story_channel.llm import OpenAILLMProvider, ProviderError, TransientProviderError
from story_channel.pipeline import Pipeline
from story_channel.voice_design import VoiceDesignPrompt, generate_voice_design, load_voice_design, voice_design_context
from test_assets_ui import prepare
from test_openai_adapter import response_payload


def designed_prompt():
    return VoiceDesignPrompt(language="Taiwan Mandarin Chinese", gender="Gender-neutral", age_range="35–45",
                             quality="Studio quality", persona="steadfast lighthouse keeper",
                             emotions=["calm", "resolute", "warm"],
                             delivery=["A warm midrange timbre with measured pacing and clear, natural delivery"])


@pytest.fixture
def voice_project(tmp_path):
    manager, project = prepare(tmp_path)
    identity = IdentityManager(manager, project.project_id)
    library = identity.prepare()
    pipeline = Pipeline(manager, project.project_id)
    pipeline.settings.llm_max_retries = 0
    pipeline.settings.input_cost_per_million = Decimal("1")
    pipeline.settings.output_cost_per_million = Decimal("2")
    return pipeline, next(iter(library.voices))


def test_voice_design_uses_character_context_and_saves_copyable_prompt_with_request_cost(voice_project):
    pipeline, cid = voice_project
    untouched = {name: (pipeline.path / name).read_bytes() for name in ("characters.json", "identity_library.json")}
    before = {key: value.output_hash for key, value in pipeline.manager.load(pipeline.project_id).steps.items()}
    payload = response_payload()
    payload["output"][0]["content"][0]["text"] = designed_prompt().model_dump_json(by_alias=True)
    payload["usage"] = {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}

    def handler(request):
        sent = json.loads(request.content)
        assert sent["model"] == pipeline.settings.llm_model
        assert '"characterId": "' + cid + '"' in sent["input"]
        assert "2–5 word English persona" in sent["input"]
        assert "zh-TW means Taiwan Mandarin Chinese" in sent["input"]
        return httpx.Response(200, json=payload, headers={"x-request-id": "req_voice_design"})

    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        saved = generate_voice_design(pipeline, cid, provider=OpenAILLMProvider(client=client))
    assert saved == load_voice_design(pipeline, cid)
    text = saved.prompt.render()
    assert text.startswith("Native Taiwan Mandarin Chinese. Gender-neutral, 35–45. Studio quality.\n")
    assert "Persona: steadfast lighthouse keeper. Emotion: calm, resolute, warm.\n" in text
    assert text.count("\n") == 2
    assert (pipeline.path / "prompts" / "voice_design" / f"{cid}.md").read_text(encoding="utf-8") == text + "\n"
    record = CostTracker(pipeline.manager, pipeline.project_id).records()[-1]
    assert record.step == f"voice_design:{cid}" and record.outcome == "succeeded"
    assert record.cost == Decimal("0.002") and record.pricing_known
    assert record.request_ids == ["req_voice_design"]
    assert record.client_request_ids[0].startswith(f"story_{pipeline.project_id}_")
    assert all((pipeline.path / name).read_bytes() == data for name, data in untouched.items())
    assert {key: value.output_hash for key, value in pipeline.manager.load(pipeline.project_id).steps.items()} == before


def test_failed_generation_records_cost_and_preserves_previous_prompt(voice_project):
    pipeline, cid = voice_project

    class Provider:
        last_usage = {"input_tokens": 1000, "output_tokens": 500}
        fail = False

        def generate(self, *args):
            if self.fail:
                raise ProviderError("Request failed after metered usage")
            return designed_prompt()

    provider = Provider()
    original = generate_voice_design(pipeline, cid, provider=provider)
    provider.fail = True
    with pytest.raises(ProviderError, match="metered"):
        generate_voice_design(pipeline, cid, provider=provider)
    assert load_voice_design(pipeline, cid) == original
    record = CostTracker(pipeline.manager, pipeline.project_id).records()[-1]
    assert record.outcome == "failed" and record.cost == Decimal("0.002")


def test_context_uses_only_selected_character_and_marks_changed_settings(voice_project):
    pipeline, cid = voice_project
    original = input_hash(voice_design_context(pipeline, cid))
    identity = IdentityManager(pipeline.manager, pipeline.project_id)
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    library.voices[cid].voice_id = "new-provider-voice"
    identity.save(library, expected=expected)
    assert input_hash(voice_design_context(pipeline, cid)) == original
    expected = library.model_dump_json(by_alias=True)
    library.voices[cid].speaking_style = "measured and gentle"
    identity.save(library, expected=expected)
    assert input_hash(voice_design_context(pipeline, cid)) != original
    with pytest.raises(ValueError, match="no longer exists"):
        generate_voice_design(pipeline, "missing-character")


def test_transient_retry_records_each_attempt(voice_project, monkeypatch):
    pipeline, cid = voice_project
    pipeline.settings.llm_max_retries = 1
    monkeypatch.setattr("story_channel.voice_design.time.sleep", lambda _: None)

    class Provider:
        calls = 0
        last_usage = {"input_tokens": 1000, "output_tokens": 500}

        def generate(self, *args):
            self.calls += 1
            if self.calls == 1:
                raise TransientProviderError("Truncated output")
            return designed_prompt()

    generate_voice_design(pipeline, cid, provider=Provider())
    records = [r for r in CostTracker(pipeline.manager, pipeline.project_id).records() if r.step == f"voice_design:{cid}"]
    assert [r.outcome for r in records] == ["failed", "succeeded"]
    assert [r.retry_count for r in records] == [0, 1]


def test_narrator_design_uses_story_context_and_separate_storage(voice_project):
    pipeline, cid = voice_project

    class Provider:
        last_usage = {"input_tokens": 1000, "output_tokens": 500}

        def generate(self, prompt, response_model, config):
            return designed_prompt()

    character_prompt = generate_voice_design(pipeline, cid, provider=Provider())
    context = voice_design_context(pipeline, "narrator", narrator=True)
    assert context["role"] == "narrator" and "character" not in context
    assert context["outline"] == pipeline.manager.read_outline(pipeline.project_id)
    assert context["narrationSamples"]
    narrator_prompt = generate_voice_design(pipeline, "narrator", narrator=True, provider=Provider())
    assert narrator_prompt.role == "narrator"
    assert load_voice_design(pipeline, "narrator", narrator=True) == narrator_prompt
    assert load_voice_design(pipeline, cid) == character_prompt
    assert (pipeline.path / "prompts" / "voice_design" / "narrator" / "prompt.md").read_text(encoding="utf-8").strip() == narrator_prompt.prompt.render()
    record = CostTracker(pipeline.manager, pipeline.project_id).records()[-1]
    assert record.step == "voice_design:narrator:voice" and record.cost == Decimal("0.002")
    assert "narrator" not in IdentityManager(pipeline.manager, pipeline.project_id).load().voices
