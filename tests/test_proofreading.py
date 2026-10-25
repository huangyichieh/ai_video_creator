import json

import pytest

from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.llm import ProviderError
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager
from story_channel.proofreading import TypoReport, TypoSuggestion, apply_suggestion, check_typos, draft_fields


@pytest.fixture
def pipeline(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", llm_max_retries=0))
    source = tmp_path / "outline.md"
    source.write_text("A story", encoding="utf-8")
    project = manager.create("Typo check", source)
    return Pipeline(manager, project.project_id, demo=True)


def test_checks_unsaved_text_with_openai_and_tracks_usage_without_saving(pipeline):
    class Fake:
        last_usage = {"input_tokens": 100, "output_tokens": 50}

        def generate(self, prompt, response_model, config):
            assert config.provider == "openai"
            assert response_model is TypoReport
            assert "teh lighthouse" in prompt
            return TypoReport(summary="One typo", suggestions=[dict(source="story", field="hook", original="teh",
                              replacement="the", explanation="Correct spelling")])

    drafts = {"story": '{"hook":"teh lighthouse"}', "outline": "A story"}
    report = check_typos(pipeline, drafts, "story", provider=Fake())
    assert report.suggestions[0].replacement == "the"
    assert drafts["story"] == '{"hook":"teh lighthouse"}'
    assert not (pipeline.path / "story.json").exists()
    assert not pipeline.manager.load(pipeline.project_id).steps
    record = CostTracker(pipeline.manager, pipeline.project_id).records()[0]
    assert record.step == "typo_check:story"
    assert record.provider == "openai"
    assert record.input_tokens == 100
    assert not record.pricing_known


def test_field_extraction_preserves_language_and_ignores_identifiers():
    fields = draft_fields({"characters": json.dumps({"characters": [{"characterId": "dont-edit", "name": "小光",
                                                "voiceId": "voice-1", "immutableTraits": ["棕色眼睛"]}]}),
                           "outline": "繁體中文故事"})
    assert {field["text"] for field in fields} == {"小光", "棕色眼睛", "繁體中文故事"}
    assert any(field["field"] == "characters[0].name" for field in fields)
    with pytest.raises(ValueError, match="Fix invalid JSON"):
        draft_fields({"story": "{"})


def test_apply_suggestion_changes_only_target_field_and_keeps_valid_json():
    original = {"characters": [{"name": "teh name", "background": "teh name"}], "other": "untouched"}
    drafts = {"characters": json.dumps(original)}
    suggestion = TypoSuggestion(source="characters", field="characters[0].name", original="teh",
                                replacement='the "correct"', explanation="Spelling")
    updated = json.loads(apply_suggestion(drafts, suggestion))
    assert updated["characters"][0]["name"] == 'the "correct" name'
    assert updated["characters"][0]["background"] == "teh name"
    assert json.loads(drafts["characters"]) == original


def test_apply_outline_and_reject_stale_or_identifier_suggestions():
    suggestion = TypoSuggestion(source="outline", field="text", original="teh", replacement="the", explanation="Spelling")
    assert apply_suggestion({"outline": "teh lighthouse"}, suggestion) == "the lighthouse"
    with pytest.raises(ValueError, match="changed"):
        apply_suggestion({"outline": "the lighthouse"}, suggestion)
    identifier = TypoSuggestion(source="characters", field="voiceId", original="teh", replacement="the", explanation="Bad ID")
    with pytest.raises(ValueError):
        apply_suggestion({"characters": '{"voiceId":"teh"}'}, identifier)


def test_invalid_suggestion_does_not_get_presented(pipeline):
    class Fake:
        last_usage = {}

        def generate(self, *args):
            return TypoReport(summary="Wrong quote", suggestions=[dict(source="story", field="hook", original="absent",
                              replacement="other", explanation="Incorrect suggestion")])

    with pytest.raises(ProviderError, match="does not match the draft"):
        check_typos(pipeline, {"story": '{"hook":"A lighthouse"}'}, "story", provider=Fake())
    assert CostTracker(pipeline.manager, pipeline.project_id).records()[0].outcome == "failed"


def test_billing_failure_is_recorded_without_retry(pipeline):
    class Fake:
        last_usage = {}
        calls = 0

        def generate(self, *args):
            self.calls += 1
            raise ProviderError("Check API billing")

    provider = Fake()
    with pytest.raises(ProviderError, match="billing"):
        check_typos(pipeline, {"outline": "A story"}, "world", provider=provider)
    assert provider.calls == 1
    assert CostTracker(pipeline.manager, pipeline.project_id).records()[0].outcome == "failed"


@pytest.mark.parametrize("failed", [False, True])
def test_typo_check_records_known_cost_even_if_response_fails(pipeline, failed):
    from decimal import Decimal
    pipeline.settings.input_cost_per_million = Decimal("1")
    pipeline.settings.output_cost_per_million = Decimal("2")

    class Metered:
        last_usage = {"input_tokens": 1000, "output_tokens": 500}

        def generate(self, *args):
            if failed:
                raise ProviderError("Response failed after metered usage")
            return TypoReport(summary="No typos", suggestions=[])

    if failed:
        with pytest.raises(ProviderError):
            check_typos(pipeline, {"outline": "A story"}, "world", provider=Metered())
    else:
        check_typos(pipeline, {"outline": "A story"}, "world", provider=Metered())
    record = CostTracker(pipeline.manager, pipeline.project_id).records()[0]
    assert record.cost == Decimal("0.002")
    assert record.pricing_known
    assert record.outcome == ("failed" if failed else "succeeded")
    assert pipeline.manager.load(pipeline.project_id).actual_cost == Decimal("0.002")
