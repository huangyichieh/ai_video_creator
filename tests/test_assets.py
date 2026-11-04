import json
import io
from decimal import Decimal
from threading import Event

import pytest

from story_channel.asset_models import ASSET_STAGES, Alignment, Cue, Production
from story_channel.assets import AssetPipeline
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.llm import ProviderError
from story_channel.media_providers import DemoImageProvider
from story_channel.media_utils import file_hash, subtitle_text, timestamp, wav_duration
from story_channel.models import ProjectStatus
from story_channel.pipeline import ApprovalRequired, Pipeline
from story_channel.projects import ProjectManager


@pytest.fixture
def assets(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo", llm_max_retries=0))
    source = tmp_path / "outline.md"
    source.write_text("The keeper saves lost memories.", encoding="utf-8")
    project = manager.create("Asset story", source, target_duration_sec=30)
    creative = Pipeline(manager, project.project_id)
    creative.run()
    creative.approve("story")
    creative.run()
    board = creative.artifact("storyboard")
    for scene in board.scenes:
        scene.dialogue = [{"character_id": "keeper", "text": "Keep the beacon burning."}]
    # Revalidate nested dictionaries before serializing.
    board = type(board).model_validate(board.model_dump())
    creative.edit("storyboard", board.model_dump_json(by_alias=True))
    creative.approve("storyboard")
    creative.run()
    engine = AssetPipeline(manager, project.project_id, demo=True)
    engine.set_generic_prompt("Cinematic adventure, warm palette, expressive natural voices")
    return engine


def complete(assets):
    assert assets.run().next_stage == "character_review"
    assets.approve("characters")
    assert assets.run().next_stage == "background_review"
    assets.approve("backgrounds")
    assert assets.run().next_stage == "asset_review"
    assert assets.approve("assets", "Reviewed visuals and audio").next_stage == "complete"


def test_style_prompts_persist_invalidate_and_reach_image_and_voice_generation(assets):
    complete(assets)
    state = assets.inspect()
    char = next(r for r in state.records.values() if r.stage == "character_references")
    assets.set_style_prompt(char.stage, char.subject, "Ink illustration")
    state = assets.inspect()
    assert state.records[f"{char.stage}:{char.subject}"].status == "stale"
    state = assets.generate(char.stage)
    assert "Ink illustration" in state.records[f"{char.stage}:{char.subject}"].asset.generation_params["prompt"]
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    assets.generate("scene_images")
    assets.generate("voices")
    voice = next(r for r in assets.inspect().records.values() if r.stage == "voices")
    original_text = voice.asset.generation_params["prompt"]
    key = f"voices:{voice.subject}"
    assets.set_style_prompt("voices", voice.subject, "Whisper softly, slowly")
    state = assets.generate("voices", subject=voice.subject)
    assert state.records[key].revision == voice.revision + 1
    assert state.records[key].asset.generation_params["prompt"] == original_text
    assert state.records[key].asset.generation_params["config"]["style_prompt"] == "Whisper softly, slowly"
    reopened = AssetPipeline(assets.manager, assets.project_id)
    assert reopened.load().style_prompts[key] == "Whisper softly, slowly"
    count = len(CostTracker(assets.manager, assets.project_id).records())
    reopened.generate("voices", subject=voice.subject)
    assert len(CostTracker(assets.manager, assets.project_id).records()) == count


def test_run_stop_after_current_voice_does_not_start_subtitles(assets):
    assets.generate("character_references")
    assets.approve("characters")
    assets.generate("backgrounds")
    assets.approve("backgrounds")
    assets.generate("scene_images")
    stop = Event()
    original = assets.tts_provider.generate

    def finish_then_stop(*args, **kwargs):
        result = original(*args, **kwargs)
        stop.set()
        return result

    assets.tts_provider.generate = finish_then_stop
    assets.stop_requested = stop.is_set
    state = assets.run()
    assert state.stopped
    assert 1 <= len([r for r in state.records.values() if r.stage == "voices"]) <= 5
    assert not any(r.stage == "subtitles" for r in state.records.values())
    assert next(r for r in state.records.values() if r.stage == "voices").status == "succeeded"


def reference_image(color="blue"):
    from PIL import Image
    output = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(output, "PNG")
    return output.getvalue()


def test_three_style_references_are_passed_to_visuals_and_validate_atomically(assets):
    images = [reference_image(color) for color in ("red", "green", "blue")]
    state = assets.set_style_references(images)
    paths = assets._style_references(state)
    assert len(paths) == 3
    baseline = assets.load()
    with pytest.raises(ValueError, match="at most 3"):
        assets.set_style_references(images + [reference_image("white")])
    assert assets.load() == baseline
    with pytest.raises(ValueError, match="not a valid"):
        assets.set_style_references([images[0], b"invalid image"])
    assert assets.load() == baseline
    original = assets.image_provider.generate
    captured = []
    def capture(prompt, references, config):
        captured.append((prompt, list(references)))
        return original(prompt, references, config)
    assets.image_provider.generate = capture
    generated = assets.generate("character_references")
    assert captured[0][1][:3] == paths
    assert "first 3" in captured[0][0]
    record = next(r for r in generated.records.values() if r.stage == "character_references")
    assert record.asset.generation_params["styleReferences"] == state.style_references
    assets.set_style_references(images[:2])
    assert assets.inspect().records["character_references:" + record.subject].status == "stale"
    assets.local_path(assets.load().style_references[1]["file"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="missing or modified"):
        assets.inspect()


def test_single_image_legacy_production_migrates_without_changing_hash(assets):
    from story_channel.asset_models import Production
    current = assets.set_style_reference(reference_image())
    legacy = current.model_dump(mode="json", by_alias=True)
    del legacy["styleReferences"]
    restored = Production.model_validate(legacy)
    assert restored.style_references == current.style_references
    assert restored.style_reference_hash == current.style_reference_hash


def test_image_only_style_shared_across_all_visuals_and_replacement_invalidates(assets):
    assets.set_style_reference(reference_image())
    assets.set_generic_prompt("")
    calls = []
    original = assets.image_provider.generate

    def capture(prompt, references, config):
        calls.append((prompt, list(references)))
        return original(prompt, references, config)

    assets.image_provider.generate = capture
    complete(assets)
    state = assets.inspect()
    shared = assets.local_path(state.style_reference)
    assert len(calls) == 5  # character, background, three scene images
    assert all(refs[0] == shared for _, refs in calls)
    assert all("Transfer style only" in prompt for prompt, _ in calls)
    assert len(calls[-1][1]) == 3  # style, approved character, approved background
    assert state.consistency_report["styleReferenceHash"] == state.style_reference_hash
    visuals = [r for r in state.records.values() if r.stage in ASSET_STAGES[:3]]
    assert all(r.asset.generation_params["styleReferenceHash"] == state.style_reference_hash for r in visuals)
    assert all(not r.asset.generation_params["styleReference"] for r in state.records.values() if r.stage == "voices")
    old = state.style_reference
    assets.set_style_reference(reference_image("red"))
    state = assets.inspect()
    assert state.style_reference != old
    assert assets.local_path(old).exists()  # immutable history reference is retained
    assert all(r.status == "stale" for r in state.records.values() if r.stage in ASSET_STAGES[:3])
    assert all(r.status == "succeeded" for r in state.records.values() if r.stage == "voices")
    assets.set_style_reference(None)
    assert assets.can_generate("character_references", assets.load())


def test_style_image_validation_integrity_and_repair(assets):
    before = assets.load()
    with pytest.raises(ValueError, match="not a valid"):
        assets.set_style_reference(b"not an image")
    assert assets.load() == before
    image = reference_image()
    assets.set_style_reference(image)
    state = assets.load()
    path = assets.local_path(state.style_reference)
    path.write_bytes(b"modified")
    with pytest.raises(ValueError, match="missing or modified"):
        assets.inspect()
    assets.set_style_reference(image)
    assert file_hash(path) == assets.inspect().style_reference_hash


def test_asset_pipeline_complete_reference_reuse_voices_and_subtitles(assets):
    complete(assets)
    state = assets.inspect()
    assert len(state.scenes) == 3
    assert all(record.status == "succeeded" for record in state.records.values())
    assert assets.manager.load(assets.project_id).status == ProjectStatus.ASSETS_READY
    assert assets.creative.inspect().status == ProjectStatus.ASSETS_READY
    voice_ids = set()
    for scene in state.scenes.values():
        assert scene.duration == wav_duration(assets.local_path(scene.audio))
        timing = json.loads(assets.local_path(scene.timing).read_text(encoding="utf-8"))
        assert len(timing["tracks"]) == 2
        assert timing["tracks"][1]["start"] == timing["tracks"][0]["duration"]
        assert len(timing["segments"]) == 2
        voice_ids.add(timing["tracks"][1]["providerVoice"])
        assert "keeper:" in assets.local_path(scene.srt).read_text(encoding="utf-8")
        assert assets.local_path(scene.vtt).read_text(encoding="utf-8").startswith("WEBVTT")
        image = state.records[f"scene_images:{scene.scene_id}"].asset
        assert image.generation_params["references"] == ["character_references:keeper", "backgrounds:lighthouse"]
        assert image.source_scene_id == scene.scene_id
    assert len(voice_ids) == 1
    assert state.consistency_report["passed"]
    assert state.consistency_report["visualReviewRequired"]
    usage = CostTracker(assets.manager, assets.project_id).records()
    assert any(record.step == "assets:voices" for record in usage)
    assert any(record.step == "assets:subtitles" for record in usage)
    assert all(record.pricing_known and record.cost == 0 for record in usage if record.provider == "demo")


def test_asset_gates_cannot_be_bypassed(assets):
    with pytest.raises(ApprovalRequired):
        assets.generate("scene_images")
    with pytest.raises(ValueError):
        assets.approve("characters")
    assets.generate("character_references")
    with pytest.raises(ApprovalRequired):
        assets.generate("backgrounds")
    assets.approve("characters")
    assets.generate("backgrounds")
    with pytest.raises(ApprovalRequired):
        assets.generate("voices")
    with pytest.raises(ValueError):
        assets.approve("assets")


def test_characters_required_before_media(assets):
    assets.creative.edit("outline", "Changed premise")
    with pytest.raises(ApprovalRequired):
        assets.generate("character_references")


def test_character_assets_available_before_story_and_reused_afterwards(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper restores memories.", encoding="utf-8")
    project = manager.create("Early characters", outline, target_duration_sec=30)
    creative = Pipeline(manager, project.project_id)
    engine = AssetPipeline(manager, project.project_id, demo=True)
    engine.set_generic_prompt("Cinematic adventure")
    creative.generate("world")
    with pytest.raises(ApprovalRequired, match="Characters"):
        engine.inspect()
    creative.generate("characters")
    assert engine.inspect().next_stage == "character_references"
    assert engine.run().next_stage == "character_review"
    state = engine.approve("characters")
    assert state.next_stage == "waiting_for_creative"
    original = {key: record.asset.asset_id for key, record in state.records.items()}
    assert engine.run().next_stage == "waiting_for_creative"
    for stage in ASSET_STAGES[1:]:
        with pytest.raises(ApprovalRequired):
            engine.generate(stage)
    with pytest.raises(ApprovalRequired):
        engine.approve("backgrounds")
    creative.run()
    creative.approve("story")
    creative.run()
    creative.approve("storyboard")
    creative.run()
    state = engine.run()
    # Completing story stages does not revoke approvals for unchanged characters.
    assert state.next_stage == "background_review"
    assert {key: state.records[key].asset.asset_id for key in original} == original
    assert engine.character_approved("keeper", state)
    assert engine.run().next_stage == "background_review"


def test_cached_run_adds_no_calls_or_versions(assets):
    complete(assets)
    before = assets.inspect()
    records = CostTracker(assets.manager, assets.project_id).records()
    assert assets.run().next_stage == "complete"
    assert len(CostTracker(assets.manager, assets.project_id).records()) == len(records)
    assert {key: r.revision for key, r in assets.inspect().records.items()} == {key: r.revision for key, r in before.records.items()}


def test_batch_failure_preserves_other_items_and_retries_only_failure(assets):
    assets.run()
    assets.approve("characters")
    assets.run()
    assets.approve("backgrounds")

    class Flaky(DemoImageProvider):
        failed = False

        def generate(self, prompt, refs, config):
            if "scene_2" in config.options["output_path"] and not self.failed:
                self.failed = True
                raise ProviderError("Synthetic image failure")
            return super().generate(prompt, refs, config)

    assets.image_provider = Flaky()
    with pytest.raises(ProviderError, match="Successful items were preserved"):
        assets.generate("scene_images")
    state = assets.inspect()
    assert state.records["scene_images:scene_1"].status == "succeeded"
    assert state.records["scene_images:scene_2"].status == "failed"
    assert state.records["scene_images:scene_3"].status == "succeeded"
    assets.generate("scene_images")
    state = assets.inspect()
    assert state.records["scene_images:scene_1"].attempts == 1
    assert state.records["scene_images:scene_2"].attempts == 2
    assert state.records["scene_images:scene_3"].attempts == 1


@pytest.mark.parametrize("failed", [False, True])
def test_asset_cost_is_recorded_for_success_and_failed_media_attempts(assets, failed):
    assets.demo = False
    assets.settings.image_cost_per_image = Decimal("0.05")
    assets.settings.runway_image_cost_per_image = Decimal("0.05")

    class Metered(DemoImageProvider):
        last_usage = {"image_count": 1, "input_tokens": 10, "output_tokens": 20}

        def generate(self, prompt, refs, config):
            if failed:
                raise ProviderError("Response failed after generation")
            return super().generate(prompt, refs, config)

    assets.image_provider = Metered()
    if failed:
        with pytest.raises(ProviderError):
            assets.generate("character_references")
    else:
        assets.generate("character_references")
    record = CostTracker(assets.manager, assets.project_id).records()[-1]
    assert record.provider == assets.settings.image_provider
    assert record.step == "assets:character_references"
    assert record.cost == Decimal("0.05")
    assert record.pricing_known
    assert record.image_count == 1
    assert record.outcome == ("failed" if failed else "succeeded")
    assert assets.manager.load(assets.project_id).actual_cost == Decimal("0.05")


def test_failed_forced_regeneration_is_retried_instead_of_using_previous_result(assets):
    assets.generate("character_references")
    old = assets.inspect().records["character_references:keeper"].asset.file_path

    class FailsOnce(DemoImageProvider):
        calls = 0

        def generate(self, prompt, refs, config):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("Synthetic forced regeneration failure")
            return super().generate(prompt, refs, config)

    provider = FailsOnce()
    assets.image_provider = provider
    with pytest.raises(ProviderError):
        assets.generate("character_references", force=True)
    assets.generate("character_references")
    record = assets.inspect().records["character_references:keeper"]
    assert provider.calls == 2
    assert record.status == "succeeded"
    assert record.asset.file_path != old
    assert assets.local_path(old).exists()


def test_tampered_reference_revokes_approval_and_never_reuses_bad_file(assets):
    complete(assets)
    reference = assets.inspect().records["character_references:keeper"]
    assets.local_path(reference.asset.file_path).write_bytes(b"tampered")
    state = assets.inspect()
    assert state.next_stage == "character_references"
    assert state.records["character_references:keeper"].status == "stale"
    assert "characters" not in assets.manager.load(assets.project_id).asset_approvals
    with pytest.raises(ApprovalRequired):
        assets.generate("scene_images")


def test_scene_regeneration_refreshes_exports_without_repeating_voice_calls(assets):
    complete(assets)
    previous = assets.inspect()
    assets.generate("scene_images", subject="scene_1", force=True)
    state = assets.inspect()
    assert state.next_stage == "subtitles"
    assert state.records["scene_images:scene_1"].revision == 2
    assert state.records["scene_images:scene_2"].revision == 1
    assert "assets" not in assets.manager.load(assets.project_id).asset_approvals
    assets.generate("subtitles")
    state = assets.inspect()
    assert state.scenes["scene_1"].image == state.records["scene_images:scene_1"].asset.file_path
    assert all(state.records[key].attempts == record.attempts for key, record in previous.records.items() if record.stage == "voices")


def test_voice_mapping_change_invalidates_voice_and_alignment(assets):
    complete(assets)
    before = assets.inspect()
    current = before.voice_map["keeper-voice"]
    new = "echo" if current != "echo" else "alloy"
    state = assets.set_voice("keeper-voice", new)
    assert state.next_stage == "voices"
    assert "assets" not in assets.manager.load(assets.project_id).asset_approvals
    assert "characters" in assets.manager.load(assets.project_id).asset_approvals


def test_alignment_bounds_and_timestamp_formats():
    with pytest.raises(ValueError):
        Alignment(duration=1, segments=[Cue(start=0, end=2, text="Too long")])
    with pytest.raises(ValueError):
        Cue(start=2, end=1, text="Reversed")
    assert timestamp(3661.25) == "01:01:01,250"
    assert timestamp(3661.25, True) == "01:01:01.250"
    assert "00:00:00,100 --> 00:00:00,900" in subtitle_text([Cue(start=.1, end=.9, text="Hello")])


def test_asset_paths_confined_and_old_manifests_supported(assets):
    with pytest.raises(ValueError):
        assets.local_path("../escape.wav")
    manifest = assets.manager.load(assets.project_id)
    manifest = type(manifest).model_validate(manifest.model_dump(exclude={"asset_state", "asset_step", "asset_approvals"}))
    assert manifest.asset_state == "not_started"
    assert manifest.asset_approvals == {}
    assert Production.model_validate({}).generic_prompt == ""
    assert Production.model_validate({}).stopped is False


def test_regenerate_selected_preserves_unselected_assets(assets):
    complete(assets)
    state = assets.inspect()
    records = [record for record in state.records.values() if record.stage == "voices"]
    assert len(records) >= 2
    before = {record.subject: record.revision for record in records}
    selected = records[0].subject
    state = assets.generate("voices", subjects=[selected], force=True)
    for record in state.records.values():
        if record.stage == "voices":
            assert record.revision == before[record.subject] + (1 if record.subject == selected else 0)
    with pytest.raises(ValueError, match="Unknown selected"):
        assets.generate("voices", subjects=["missing"], force=True)
