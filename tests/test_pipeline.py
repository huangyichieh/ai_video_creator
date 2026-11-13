import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_channel.cli import main
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.creative import Characters, Prompts, Storyboard
from story_channel.demo import DemoLLMProvider
from story_channel.files import input_hash
from story_channel.llm import ProviderError, TransientProviderError, token_cost
from story_channel.locking import project_lock
from story_channel.models import ProviderConfig
from story_channel.modules import MODULES, STEPS, validate_output
from story_channel.pipeline import ApprovalRequired, Pipeline
from story_channel.projects import ProjectManager


@pytest.fixture
def flow(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo"))
    source = tmp_path / "outline.md"
    source.write_text("守燈人要在暴風雨之前找回村民失落的記憶。", encoding="utf-8")
    project = manager.create("最後的燈塔", source, target_duration_sec=120)
    return Pipeline(manager, project.project_id)


def complete(flow):
    state = flow.run()
    if state.current_step == "story_review":
        flow.approve("story", "Read story and report")
        state = flow.run()
    if state.current_step == "storyboard_review":
        flow.approve("storyboard", "Reviewed visuals and duration")
    assert flow.run().current_step == "complete"


def test_outline_to_full_project_golden_and_cache(flow):
    complete(flow)
    manifest = flow.inspect()
    assert manifest.approved_steps == ["story", "storyboard"]
    assert all(manifest.steps[s].status == "succeeded" for s in STEPS)
    assert all(flow.artifact(s) for s in STEPS)
    assert sum(s.duration_sec for s in flow.artifact("storyboard").scenes) == 120
    assert list(flow.artifact("story").model_dump()) == ["title", "target_duration_sec", "hook", "setup", "conflict", "escalation", "twist", "climax", "ending"]
    expected_files = ("outline.json", "world.json", "characters.json", "characters/keeper.json",
                      "story.json", "review_report.json", "script.json", "scenes.json",
                      "prompts/prompts.json", "prompts/image_prompts.json", "prompts/video_prompts.json", "usage.json", "logs/pipeline.jsonl")
    assert all((flow.path / name).exists() for name in expected_files)
    records = CostTracker(flow.manager, flow.project_id).records()
    assert len(records) == 6
    assert all(r.cost == 0 and r.pricing_known for r in records)
    before = {s: manifest.steps[s].revision for s in STEPS}
    flow.run()
    assert not list(flow.path.rglob("*.md"))
    assert len(CostTracker(flow.manager, flow.project_id).records()) == 6
    assert {s: flow.inspect().steps[s].revision for s in STEPS} == before
    assert len(list((flow.path / "history").glob("*/*.json"))) == 7


def test_storyboard_markdown_export_is_on_demand_without_persistence(flow):
    from story_channel.markdown_exports import readable_text
    complete(flow)
    content = flow.export_storyboard_markdown()
    board = flow.artifact("storyboard")
    assert content.startswith("# Storyboard")
    for scene in board.scenes:
        assert scene.scene_id in content
        assert readable_text(scene.visual_description) in content
        assert scene.camera_movement in content
        assert readable_text(scene.narration) in content
    before = flow.manager.load(flow.project_id)
    assert flow.export_storyboard_markdown() == content
    assert not list(flow.path.rglob("*.md"))
    assert flow.manager.load(flow.project_id) == before


def test_progress_is_reported_before_provider_call_and_after_completion(flow):
    events = []
    flow.on_progress = lambda manifest: events.append(manifest)
    original = flow.provider.generate

    def generate(prompt, response_model, config):
        assert events[-1].steps["world"].status == "running"
        assert events[-1].current_step == "world"
        return original(prompt, response_model, config)

    flow.provider.generate = generate
    flow.generate("world")
    assert [manifest.steps["world"].status for manifest in events] == ["running", "succeeded"]
    assert events[-1].current_step == "characters"
    assert set(events[-1].steps) == {"world"}


def test_progress_reports_failed_stage(flow):
    events = []
    flow.on_progress = lambda manifest: events.append(manifest)

    class Failure(DemoLLMProvider):
        def generate(self, prompt, response_model, config):
            raise ProviderError("Test failure")

    flow.provider = Failure()
    with pytest.raises(ProviderError):
        flow.generate("world")
    assert [manifest.steps["world"].status for manifest in events] == ["running", "failed"]
    assert events[-1].steps["world"].error == "Test failure"


def test_openai_regeneration_changes_only_selected_stage_and_tracks_usage(flow, monkeypatch):
    complete(flow)
    before = flow.inspect()
    monkeypatch.setattr("story_channel.pipeline.OpenAILLMProvider", lambda **kwargs: DemoLLMProvider())
    flow.regenerate_with_openai("story")
    current = flow.inspect()
    assert current.steps["world"].revision == before.steps["world"].revision
    assert current.steps["characters"].revision == before.steps["characters"].revision
    assert current.steps["story"].revision == before.steps["story"].revision + 1
    assert current.steps["review"].status == "stale"
    assert "story" not in current.approvals and "storyboard" in current.approvals
    assert current.provider_config["llm"].provider == "demo"
    assert current.provider_config["llm:story"].provider == "openai"
    assert CostTracker(flow.manager, flow.project_id).records()[-1].provider == "openai"
    assert Pipeline(flow.manager, flow.project_id).inspect().steps["story"].status == "succeeded"


def test_openai_regeneration_failure_preserves_saved_output(flow, monkeypatch):
    flow.generate("world")
    original = (flow.path / "world.json").read_bytes()

    class Failure:
        last_usage = {}

        def generate(self, *args):
            raise ProviderError("API unavailable")

    monkeypatch.setattr("story_channel.pipeline.OpenAILLMProvider", lambda **kwargs: Failure())
    with pytest.raises(ProviderError, match="unavailable"):
        flow.regenerate_with_openai("world")
    assert (flow.path / "world.json").read_bytes() == original
    assert flow.inspect().steps["world"].status == "failed"


@pytest.mark.parametrize("entry", ["pipeline", "next_stage", "regenerate"])
@pytest.mark.parametrize("failed", [False, True])
def test_every_generation_action_records_known_cost_on_success_and_failure(flow, monkeypatch, entry, failed):
    flow.settings.input_cost_per_million = Decimal("1")
    flow.settings.output_cost_per_million = Decimal("2")

    class Metered(DemoLLMProvider):
        def generate(self, prompt, response_model, config):
            if failed:
                self.last_usage = {"input_tokens": 1000, "output_tokens": 500}
                raise ProviderError("Response failed after metered usage")
            output = super().generate(prompt, response_model, config)
            self.last_usage = {"input_tokens": 1000, "output_tokens": 500}
            return output

    metered = Metered()
    monkeypatch.setattr("story_channel.pipeline.OpenAILLMProvider", lambda **kwargs: metered)
    live = Pipeline(flow.manager, flow.project_id, provider=metered,
                    llm_config=ProviderConfig(provider="openai", model="test-model"))
    operation = live.run if entry == "pipeline" else (lambda: live.generate("world")) if entry == "next_stage" else (lambda: live.regenerate_with_openai("world"))
    if failed:
        with pytest.raises(ProviderError):
            operation()
    else:
        operation()
    records = CostTracker(flow.manager, flow.project_id).records()
    assert records
    assert all(record.cost == Decimal("0.002") and record.pricing_known for record in records)
    assert all(record.outcome == ("failed" if failed else "succeeded") for record in records)
    assert flow.manager.load(flow.project_id).actual_cost == Decimal("0.002") * len(records)


def test_bundle_validates_every_edit_before_writing(flow):
    flow.run()
    before = {name: (flow.path / MODULES[name].filename).read_text(encoding="utf-8")
              for name in ("world", "story")}
    story = json.loads(before["story"])
    story["ending"] = "Changed ending"
    with pytest.raises(ValidationError):
        flow.edit_bundle({"story": json.dumps(story), "world": "{}"}, expected=before)
    assert all((flow.path / MODULES[name].filename).read_text(encoding="utf-8") == text
               for name, text in before.items())


def test_bundle_saves_content_and_rejects_concurrent_change(flow):
    complete(flow)
    before = (flow.path / "story.json").read_text(encoding="utf-8")
    story = json.loads(before)
    story["ending"] = "Edited from the browser"
    flow.edit_bundle({"story": json.dumps(story)}, expected={"story": before})
    assert flow.artifact("story").ending == "Edited from the browser"
    assert json.loads((flow.path / "story.json").read_text(encoding="utf-8"))["ending"] == "Edited from the browser"
    assert flow.inspect().steps["review"].status == "stale"
    assert "story" not in flow.inspect().approvals and "storyboard" in flow.inspect().approvals
    with pytest.raises(ValueError, match="another session"):
        flow.edit_bundle({"story": before}, expected={"story": before})


def test_bundle_saves_project_outline_and_stage_together(flow):
    flow.generate("world")
    before = (flow.path / "world.json").read_text(encoding="utf-8")
    flow.edit_bundle({"project": json.dumps({"title": "New title", "language": "en", "targetDurationSec": 120}),
                      "outline": "A revised outline.", "world": before}, expected={"world": before})
    assert flow.inspect().title == "New title"
    assert flow.inspect().language == "en"
    assert flow.manager.read_outline(flow.project_id) == "A revised outline."
    assert flow.inspect().steps["world"].status == "succeeded"


def test_approval_gates_cannot_be_bypassed(flow):
    with pytest.raises(ValueError):
        flow.approve("story")
    assert flow.run().current_step == "story_review"
    with pytest.raises(ApprovalRequired):
        flow.generate("script")
    with pytest.raises(ValueError):
        flow.approve("storyboard")
    flow.approve("story")
    assert flow.run().current_step == "storyboard_review"
    with pytest.raises(ApprovalRequired):
        flow.generate("prompts")


@pytest.mark.parametrize("step", ["world", "characters", "story", "review", "script", "storyboard"])
def test_edit_invalidation_and_immutable_history(flow, step):
    complete(flow)
    previous = flow.inspect()
    old = flow.artifact(step).model_dump_json(by_alias=True)
    flow.edit(step, old)
    current = flow.inspect()
    assert current.steps[step].revision == previous.steps[step].revision + 1
    assert current.steps[step].status == "succeeded"
    affected = {"world": {"characters", "story", "review", "script", "storyboard", "prompts"},
                "characters": {"review"}, "story": {"review"}, "review": set(),
                "script": {"storyboard", "prompts"}, "storyboard": {"prompts"}}[step]
    for other in set(STEPS) - {step}:
        assert current.steps[other].status == ("stale" if other in affected else "succeeded")
    if STEPS.index(step) <= STEPS.index("review"):
        assert "story" not in current.approvals
    else:
        assert "story" in current.approvals
    assert ("storyboard" in current.approvals) == (step in ("characters", "story", "review"))
    first_snapshot = flow.path / "history" / step / "000001.json"
    assert MODULES[step].model.model_validate_json(first_snapshot.read_text(encoding="utf-8")).model_dump_json(by_alias=True) == old


def test_story_edit_requires_new_review_then_resume(flow):
    complete(flow)
    story = flow.artifact("story")
    story.ending = "她選擇守護全村共同的回憶。"
    flow.edit("story", story.model_dump_json(by_alias=True))
    with pytest.raises(ValueError):
        flow.approve("story")
    assert flow.run().current_step == "story_review"
    assert flow.artifact("story").ending == story.ending
    flow.approve("story")
    flow.run()
    flow.approve("storyboard")
    assert flow.run().current_step == "complete"


def test_outline_edit_and_external_changes_revoke_approvals(flow):
    complete(flow)
    flow.edit("outline", "A changed premise")
    assert flow.inspect().approved_steps == []
    assert all(s.status == "stale" for s in flow.inspect().steps.values())
    complete(flow)
    data = json.loads((flow.path / "scenes.json").read_text(encoding="utf-8"))
    data["scenes"][0]["visualDescription"] = "Changed outside the application"
    (flow.path / "scenes.json").write_text(json.dumps(data), encoding="utf-8")
    assert flow.inspect().steps["storyboard"].status == "stale"
    assert "storyboard" not in flow.inspect().approvals
    with pytest.raises(ValueError):
        flow.approve("storyboard")


def test_invalid_edit_does_not_modify_artifacts(flow):
    complete(flow)
    before = (flow.path / "scenes.json").read_bytes()
    state = flow.inspect()
    invalid = flow.artifact("storyboard").model_dump(mode="json", by_alias=True)
    invalid["scenes"][0]["locationId"] = "missing"
    with pytest.raises(ValueError, match="Unknown location"):
        flow.edit("storyboard", json.dumps(invalid))
    assert (flow.path / "scenes.json").read_bytes() == before
    assert flow.inspect().approvals == state.approvals
    with pytest.raises(ValidationError):
        flow.edit("story", "{}")


def test_failure_resume_preserves_successful_modules(flow):
    class Failing(DemoLLMProvider):
        def generate(self, prompt, response_model, config):
            if response_model is Characters:
                raise ProviderError("Synthetic failure")
            return super().generate(prompt, response_model, config)

    flow.provider = Failing()
    with pytest.raises(ProviderError):
        flow.run()
    manifest = flow.inspect()
    assert manifest.steps["world"].status == "succeeded"
    assert manifest.steps["characters"].status == "failed"
    assert manifest.current_step == "characters"
    flow.provider = DemoLLMProvider()
    assert flow.run().current_step == "story_review"
    assert flow.inspect().steps["world"].attempts == 1
    assert flow.inspect().steps["characters"].attempts == 2


def test_transient_retries_and_each_attempt_is_recorded(flow, monkeypatch):
    monkeypatch.setattr("story_channel.pipeline.time.sleep", lambda _: None)

    class Flaky(DemoLLMProvider):
        count = 0

        def generate(self, prompt, response_model, config):
            self.count += 1
            if self.count == 1:
                self.last_usage = {}
                raise TransientProviderError("Temporary problem")
            return super().generate(prompt, response_model, config)

    flow.provider = Flaky()
    flow.generate("world")
    records = CostTracker(flow.manager, flow.project_id).records()
    assert [r.outcome for r in records] == ["failed", "succeeded"]
    assert [r.retry_count for r in records] == [0, 1]


def test_regenerate_single_module_and_provider_change(flow):
    complete(flow)
    world_revision = flow.inspect().steps["world"].revision
    flow.generate("story", force=True)
    assert flow.inspect().steps["world"].revision == world_revision
    assert flow.inspect().steps["review"].status == "stale"
    complete(flow)
    flow.settings.prompt_provider = "another-vendor"
    flow.settings.prompt_model = "another-image-model"
    state = flow.inspect()
    assert state.steps["prompts"].status == "stale"
    assert state.approved_steps == ["story", "storyboard"]
    flow.run()
    assert flow.artifact("prompts").prompts[0].provider == "another-vendor"
    assert "imagePrompt" not in flow.artifact("storyboard").scenes[0].model_dump(by_alias=True)


def test_saved_generation_config_survives_new_session(flow):
    flow.run()
    replacement = Pipeline(flow.manager, flow.project_id)
    assert replacement.config == flow.config
    assert replacement.demo
    assert replacement.inspect().current_step == "story_review"


def test_project_lock_blocks_another_worker(flow):
    with project_lock(flow.path):
        with pytest.raises(RuntimeError, match="busy"):
            flow.run()
    assert flow.run().current_step == "story_review"


def test_shared_library_roundtrip(flow):
    flow.run()
    for step in ("world", "characters"):
        flow.save_to_library(step, "reusable")
        with pytest.raises(FileExistsError):
            flow.save_to_library(step, "reusable")
    new_project = flow.manager.create("Episode two", flow.path / "outline.json")
    other = Pipeline(flow.manager, new_project.project_id)
    other.use_library("world", "reusable")
    other.use_library("characters", "reusable")
    assert other.artifact("world") == flow.artifact("world")
    assert other.artifact("characters") == flow.artifact("characters")
    assert other.inspect().steps["characters"].attempts == 0
    with pytest.raises(ValueError):
        other.use_library("world", "../escape")


def test_reference_timing_and_prompt_coverage_validation(flow):
    complete(flow)
    storyboard = flow.artifact("storyboard")
    context = flow._context("storyboard")
    storyboard.scenes[0].duration_sec = 1000
    with pytest.raises(ValueError, match="durations total"):
        validate_output("storyboard", storyboard, context)
    prompts = flow.artifact("prompts")
    prompts.prompts.pop()
    with pytest.raises(ValueError, match="cover every"):
        validate_output("prompts", prompts, flow._context("prompts"))


def test_cost_rates_and_unknown_pricing():
    usage = {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 200}
    assert token_cost(usage, Decimal("1"), Decimal("4"), Decimal("0.5")) == (Decimal("0.0029"), True)
    assert token_cost(usage, None, None) == (Decimal("0"), False)
    assert token_cost(usage, Decimal("1"), Decimal("4")) == (Decimal("0"), False)


def test_cli_phase1_lifecycle_and_schemas(flow, capsys):
    common = ["--data-root", str(flow.manager.root)]
    pid = flow.project_id
    assert main(common + ["run", pid, "--demo"]) == 0
    assert json.loads(capsys.readouterr().out)["currentStep"] == "story_review"
    assert main(common + ["approve", pid, "story"]) == 0
    capsys.readouterr()
    assert main(common + ["resume", pid]) == 0
    assert json.loads(capsys.readouterr().out)["currentStep"] == "storyboard_review"
    assert main(common + ["approve", pid, "storyboard"]) == 0
    capsys.readouterr()
    assert main(common + ["resume", pid]) == 0
    assert json.loads(capsys.readouterr().out)["currentStep"] == "complete"
    directory = flow.path / "schemas"
    assert main(common + ["schemas", str(directory)]) == 0
    assert len(list(directory.glob("*.schema.json"))) == 7
    capsys.readouterr()
    assert main(common + ["list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["projectId"] == pid
    from story_channel.logging_config import configure_logging
    configure_logging()
