import json
from copy import deepcopy
from decimal import Decimal

import pytest

from story_channel.assets import AssetPipeline
from story_channel.asset_sources import modify_source_with_openai
from story_channel.costs import CostTracker
from story_channel.llm import ProviderError
from story_channel.media_providers import DemoImageProvider, DemoTTSProvider
from story_channel.pipeline import Pipeline
from test_parallel_assets import cast
from test_assets_ui import prepare


class Editor:
    last_usage = {"input_tokens": 10, "output_tokens": 5}

    def __init__(self, edit):
        self.edit = edit
        self.requests = []

    def generate(self, prompt, response_model, config):
        data = json.loads(prompt.rsplit("\n", 1)[-1])
        self.requests.append(deepcopy(data))
        self.edit(data["current"])
        return response_model.model_validate(data["current"])


def test_prompt_modifies_existing_stage_with_history_invalidation_and_cost(tmp_path, monkeypatch):
    manager, project = cast(tmp_path)
    flow = Pipeline(manager, project.project_id)
    current = flow.artifact("world")
    original_path = flow.path / "world.json"
    expected = original_path.read_text(encoding="utf-8-sig")
    editor = Editor(lambda current: current.update(era="Future island"))
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: editor)
    flow.modify_with_openai("world", "Move the era into the future", expected=expected)
    assert editor.requests[0]["current"]["era"] == current.era
    assert editor.requests[0]["modification_request"] == "Move the era into the future"
    assert flow.artifact("world").era == "Future island"
    assert flow.artifact("world").locations == current.locations
    assert manager.load(project.project_id).steps["characters"].status == "stale"
    assert manager.load(project.project_id).steps["world"].revision == 2
    record = CostTracker(manager, project.project_id).records()[-1]
    assert record.step == "modify:world" and record.provider == "openai"
    assert record.input_tokens == 10
    assert list((flow.path / "history" / "world").glob("*.json"))
    with pytest.raises(ValueError, match="content changed"):
        flow.modify_with_openai("world", "Change it again", expected=expected)


def test_failed_modification_records_usage_and_preserves_content(tmp_path, monkeypatch):
    manager, project = cast(tmp_path)
    manager.settings.input_cost_per_million = Decimal("1")
    manager.settings.output_cost_per_million = Decimal("2")
    flow = Pipeline(manager, project.project_id)
    before = (flow.path / "world.json").read_bytes()

    class Failing:
        last_usage = {"input_tokens": 10, "output_tokens": 5}

        def generate(self, *_):
            raise ProviderError("Synthetic failure")

    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: Failing())
    with pytest.raises(ProviderError):
        flow.modify_with_openai("world", "Change era")
    assert (flow.path / "world.json").read_bytes() == before
    record = CostTracker(manager, project.project_id).records()[-1]
    assert record.outcome == "failed" and record.cost == Decimal("0.00002")


def test_id_changes_rejected_and_character_source_only_updates_target(tmp_path, monkeypatch):
    manager, project = cast(tmp_path)
    flow = Pipeline(manager, project.project_id)
    original = flow.artifact("characters")
    expected = original.characters[0].model_dump_json(by_alias=True)
    editor = Editor(lambda current: current.update(characterId="different"))
    monkeypatch.setattr("story_channel.modification.OpenAILLMProvider", lambda **_: editor)
    with pytest.raises(ValueError, match="IDs"):
        modify_source_with_openai(flow, "character_references", "cast_0", "Change appearance", expected=expected)
    assert flow.artifact("characters") == original
    editor.edit = lambda current: current.update(appearance="Long silver hair")
    modify_source_with_openai(flow, "character_references", "cast_0", "Change hair to silver", expected=expected)
    result = flow.artifact("characters")
    assert result.characters[0].appearance == "Long silver hair"
    assert result.characters[1:] == original.characters[1:]


def test_image_modification_uses_current_image_openai_cost_and_cache(tmp_path):
    manager, project = cast(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    engine.set_generic_prompt("Watercolor")
    engine.settings.image_cost_per_image = Decimal("0.07")
    before = engine.generate("character_references", subject="cast_0").records["character_references:cast_0"].model_copy(deep=True)
    calls = []

    class ImageEditor(DemoImageProvider):
        def generate(self, prompt, references, config):
            calls.append((prompt, references, config))
            self.last_usage = {"image_count": 1}
            result = super().generate(prompt, references, config)
            result.provider = "openai"
            return result

    engine.modification_image_provider = ImageEditor()
    state = engine.modify_with_openai("character_references", "cast_0", "Make lighting warmer", expected_asset_id=before.asset.asset_id)
    record = state.records["character_references:cast_0"]
    assert engine.local_path(before.asset.file_path) == calls[0][1][-1]
    assert "Make lighting warmer" in calls[0][0]
    assert calls[0][2].provider == "openai"
    assert record.revision == 2 and record.status == "succeeded"
    assert record.asset.asset_id != before.asset.asset_id
    assert engine.local_path(before.asset.file_path).is_file()
    usage = CostTracker(manager, project.project_id).records()[-1]
    assert usage.provider == "openai" and usage.cost == Decimal("0.07")
    engine.generate("character_references", subject="cast_0")
    assert len(calls) == 1  # saved modification remains current, not immediately stale
    # Explicit regeneration returns to the configured image provider, rather
    # than silently reissuing the earlier OpenAI edit.
    fresh = engine.generate("character_references", subject="cast_0", force=True)
    assert len(calls) == 1
    assert fresh.records["character_references:cast_0"].asset.provider == "demo"
    assert "character_references:cast_0" not in fresh.asset_modifications
    assert list((engine.path / "history/asset_modifications/character_references/cast_0").glob("*.json"))


def test_voice_modification_preserves_words_and_passes_delivery_prompt(tmp_path):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    engine.run()
    engine.approve("characters")
    engine.run()
    engine.approve("backgrounds")
    engine.generate("scene_images")
    state = engine.generate("voices")
    original = next(r for r in state.records.values() if r.stage == "voices")
    calls = []

    class VoiceEditor(DemoTTSProvider):
        def generate(self, text, voice, config):
            calls.append((text, config))
            result = super().generate(text, voice, config)
            result.provider = "openai"
            return result

    engine.modification_tts_provider = VoiceEditor()
    result = engine.modify_with_openai("voices", original.subject, "Speak softly and slowly")
    record = result.records[f"voices:{original.subject}"]
    assert calls[0][0] == original.asset.generation_params["prompt"]
    assert calls[0][1].options["modification"]["prompt"] == "Speak softly and slowly"
    assert record.revision == original.revision + 1
    assert record.status == "succeeded"


def test_failed_image_modification_keeps_previous_file_and_records_openai_cost(tmp_path):
    manager, project = cast(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    engine.set_generic_prompt("Watercolor")
    engine.settings.image_cost_per_image = Decimal("0.07")
    original = engine.generate("character_references", subject="cast_0").records["character_references:cast_0"].model_copy(deep=True)

    class FailingEditor(DemoImageProvider):
        def generate(self, *_):
            self.last_usage = {"image_count": 1}
            raise ProviderError("Synthetic edit failure")

    engine.modification_image_provider = FailingEditor()
    with pytest.raises(ProviderError):
        engine.modify_with_openai("character_references", "cast_0", "Make lighting warmer")
    record = engine.load().records["character_references:cast_0"]
    assert record.asset.asset_id == original.asset.asset_id
    assert engine.local_path(original.asset.file_path).is_file()
    assert record.status == "failed"
    usage = CostTracker(manager, project.project_id).records()[-1]
    assert usage.provider == "openai" and usage.outcome == "failed" and usage.cost == Decimal("0.07")
    recovered = engine.generate("character_references", subject="cast_0")
    assert recovered.records["character_references:cast_0"].status == "succeeded"
    assert recovered.records["character_references:cast_0"].asset.provider == "demo"
