import pytest

from story_channel.creative import ReviewReport, ReviewIssue
from story_channel.demo import DemoLLMProvider
from story_channel.files import input_hash
from story_channel.models import ApprovalRecord
from story_channel.modules import MODULES, STEPS
from story_channel.pipeline import Pipeline
from story_channel.review_changes import (ReviewDifference, ReviewSuggestions, approve_story_unchanged,
                                          prepare_review, review_inputs, save_review)
from test_pipeline import flow, complete


@pytest.mark.parametrize("stage,affected,storyboard_approved", [
    ("world", {"characters", "story", "review", "script", "storyboard", "prompts"}, False),
    ("characters", {"review"}, True),
    ("story", {"review"}, True),
    ("review", set(), True),
    ("script", {"storyboard", "prompts"}, False),
    ("storyboard", {"prompts"}, False),
])
def test_edit_invalidates_only_requested_stages_and_survives_reload(flow, stage, affected, storyboard_approved):
    complete(flow)
    before = flow.inspect()
    snapshots = {name: (flow.path / module.filename).read_bytes() for name, module in MODULES.items()}
    output = flow.artifact(stage)
    if stage == "world":
        output.era += " A new era."
    elif stage == "characters":
        output.characters[0].appearance += " A small scar."
    elif stage == "story":
        output.ending += " A new final thought."
    elif stage == "review":
        output.summary += " Additional review observations."
    elif stage == "script":
        output.scenes[0].purpose += " A clearer purpose."
    else:
        output.scenes[0].visual_description += " Soft light through the window."
    flow.edit(stage, output.model_dump_json(by_alias=True))
    # Reopening must not cascade fingerprint differences into preserved stages.
    current = Pipeline(flow.manager, flow.project_id).inspect()
    current = Pipeline(flow.manager, flow.project_id).inspect()
    assert current.steps[stage].status == "succeeded"
    assert current.steps[stage].revision == before.steps[stage].revision + 1
    for name in set(STEPS) - {stage}:
        assert current.steps[name].status == ("stale" if name in affected else "succeeded")
        assert current.steps[name].revision == before.steps[name].revision
        assert (flow.path / MODULES[name].filename).read_bytes() == snapshots[name]
        if name not in affected:
            assert current.steps[name].input_hash == before.steps[name].input_hash
    assert ("storyboard" in current.approvals) == storyboard_approved
    assert ("story" in current.approvals) == (stage in ("script", "storyboard"))


def test_rerunning_ai_review_and_remaining_unchanged_preserves_production_and_approvals(flow):
    complete(flow)
    manifest = flow.inspect()
    manifest.asset_state, manifest.render_state = "approved", "approved"
    manifest.asset_approvals["assets"] = ApprovalRecord(content_hash="saved-assets")
    manifest.final_approval = ApprovalRecord(content_hash="saved-final")
    flow.manager.save(manifest)
    before = flow.inspect()
    original = {name: (flow.path / MODULES[name].filename).read_bytes() for name in ("story", "script", "storyboard", "prompts")}
    class NewReview(DemoLLMProvider):
        def generate(self, prompt, response_model, config):
            if response_model is ReviewReport:
                return ReviewReport(summary="A new review of the same Story.", issues=[], recommendation="approve")
            raise AssertionError("Only the review should be generated")
    flow.provider = NewReview()
    flow.generate("review", force=True)
    review = prepare_review(flow, "story")
    context, report = review_inputs(flow, "story")
    approve_story_unchanged(flow, source_hash=input_hash(context), report_hash=input_hash(report), report_revision=review.report_revision)
    current = Pipeline(flow.manager, flow.project_id).inspect()
    assert current.current_step == "complete"
    for name, content in original.items():
        assert (flow.path / MODULES[name].filename).read_bytes() == content
        assert current.steps[name].status == "succeeded"
        assert current.steps[name].revision == before.steps[name].revision
        assert current.steps[name].attempts == before.steps[name].attempts
    assert current.approvals["storyboard"] == before.approvals["storyboard"]
    assert current.asset_approvals == before.asset_approvals
    assert current.asset_state == before.asset_state and current.render_state == before.render_state
    assert current.final_approval == before.final_approval


@pytest.mark.parametrize("stage", ["characters", "story"])
def test_character_or_story_edit_followed_by_unchanged_review_does_not_repeat_production(flow, stage):
    complete(flow)
    before = flow.inspect()
    output = flow.artifact(stage)
    if stage == "characters":
        output.characters[0].personality += " More patient."
    else:
        output.ending += " A closing reflection."
    flow.edit(stage, output.model_dump_json(by_alias=True))
    flow.generate("review")
    review = prepare_review(flow, "story")
    context, report = review_inputs(flow, "story")
    approve_story_unchanged(flow, source_hash=input_hash(context), report_hash=input_hash(report), report_revision=review.report_revision)
    current = Pipeline(flow.manager, flow.project_id).inspect()
    assert current.current_step == "complete"
    for name in ("script", "storyboard", "prompts"):
        assert current.steps[name].status == "succeeded"
        assert current.steps[name].revision == before.steps[name].revision
        assert current.steps[name].attempts == before.steps[name].attempts
    assert current.approvals["storyboard"] == before.approvals["storyboard"]


@pytest.mark.parametrize("change_story", [True, False])
def test_only_actual_story_edits_accepted_from_review_restart_script_onwards(flow, change_story):
    complete(flow)
    before = flow.inspect()
    report = flow.artifact("review")
    report.issues = [ReviewIssue(category="logic", severity="warning", description="Clarify the ending", suggestion="Add a conclusion")]
    flow.edit("review", report.model_dump_json(by_alias=True))
    original = flow.artifact("story").ending
    change = ReviewDifference(path=["ending"], original=original, suggested=original + " A clearer conclusion.", reason="Resolve the finding", issue_indices=[0])
    review = prepare_review(flow, "story", suggestions=ReviewSuggestions(changes=[change]))
    save_review(flow, "story", [{"choice": "apply", "text": change.suggested if change_story else original}],
                expected=input_hash(review.model_dump(mode="json")))
    current = Pipeline(flow.manager, flow.project_id).inspect()
    for name in ("script", "storyboard", "prompts"):
        assert current.steps[name].status == ("stale" if change_story else "succeeded")
        assert current.steps[name].revision == before.steps[name].revision
    assert ("storyboard" in current.approvals) != change_story
    assert "story" in current.approvals
    assert current.current_step == ("script" if change_story else "complete")
