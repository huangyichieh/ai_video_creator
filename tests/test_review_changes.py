import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.config import Settings
from story_channel.files import atomic_write, input_hash
from story_channel.pipeline import ApprovalRequired, Pipeline
from story_channel.projects import ProjectManager
from story_channel.review_changes import (ReviewDifference, ReviewSuggestions, current_session, prepare_review,
                                          resolution_current, save_review, approve_story_unchanged, review_inputs)


def story_review(tmp_path, *, production=False):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper saves the island's memories.", encoding="utf-8")
    project = manager.create("Review changes", outline)
    if production:
        from story_channel.demo import DemoLLMProvider
        from story_channel.models import ProviderConfig
        flow = Pipeline(manager, project.project_id, demo=False, provider=DemoLLMProvider(),
                        llm_config=ProviderConfig(provider="openai", model="test-model"))
    else:
        flow = Pipeline(manager, project.project_id, demo=True)
    flow.run()
    report = flow.artifact("review").model_dump(mode="json", by_alias=True)
    report["issues"] = [dict(severity="warning", category="pacing", description="Clarify setup", suggestion="Explain the keeper's goal"),
                        dict(severity="error", category="logic", description="Clarify climax", suggestion="Explain the resolution")]
    flow.edit("review", json.dumps(report))
    original = flow.artifact("story")
    changes = [ReviewDifference(path=["setup"], original=original.setup, suggested=original.setup + " The keeper has a clear goal.", reason="Clarify motivation", issue_indices=[0]),
               ReviewDifference(path=["climax"], original=original.climax, suggested=original.climax + " The mystery is resolved.", reason="Resolve logic", issue_indices=[1])]
    session = prepare_review(flow, "story", suggestions=ReviewSuggestions(changes=changes))
    return flow, session


def test_all_differences_must_be_resolved_before_save_or_pipeline_approval(tmp_path):
    flow, session = story_review(tmp_path)
    expected = input_hash(session.model_dump(mode="json"))
    original = (flow.path / "story.json").read_bytes()
    with pytest.raises(ApprovalRequired):
        flow.approve("story")
    with pytest.raises(ApprovalRequired):
        save_review(flow, "story", [{"choice": "apply", "text": "Only one decision"}], expected=expected)
    assert (flow.path / "story.json").read_bytes() == original
    with pytest.raises(ApprovalRequired):
        flow.generate("script")
    edited = session.changes[0].suggested + " A manual refinement."
    save_review(flow, "story", [{"choice": "apply", "text": edited}, {"choice": "revert"}], expected=expected)
    assert flow.artifact("story").setup == edited
    assert flow.artifact("story").climax == session.changes[1].original
    assert resolution_current(flow, "story")
    assert (flow.path / "ai_review_story_changes.md").is_file()
    assert "story" in flow.inspect().approvals
    assert flow.inspect().steps["review"].status == "succeeded"
    flow.generate("script")
    assert flow.inspect().steps["script"].status == "succeeded"


def test_stale_review_and_invalid_edits_do_not_overwrite_saved_content(tmp_path):
    flow, session = story_review(tmp_path)
    expected = input_hash(session.model_dump(mode="json"))
    original = (flow.path / "story.json").read_bytes()
    with pytest.raises(ValueError):
        save_review(flow, "story", [{"choice": "apply", "text": ""}, {"choice": "revert"}], expected=expected)
    assert (flow.path / "story.json").read_bytes() == original
    changed = flow.artifact("story").model_copy(update={"hook": "Another editor changed this hook."})
    flow.edit("story", changed.model_dump_json(by_alias=True))
    with pytest.raises(ValueError, match="changed|outdated"):
        save_review(flow, "story", [{"choice": "revert"}, {"choice": "revert"}], expected=expected)
    assert flow.artifact("story").hook == changed.hook
    assert not resolution_current(flow, "story")


def test_suggestions_must_cover_findings_and_cannot_change_identifiers(tmp_path):
    flow, session = story_review(tmp_path)
    with pytest.raises(ValueError, match="every review finding"):
        prepare_review(flow, "story", force=True, suggestions=ReviewSuggestions(changes=session.changes[:1]))
    invalid = session.changes[0].model_copy(update={"path": ["targetDurationSec"], "original": "600"})
    with pytest.raises(ValueError, match="exact current text"):
        prepare_review(flow, "story", force=True, suggestions=ReviewSuggestions(changes=[invalid, session.changes[1]]))


@pytest.mark.parametrize("choice", ["apply", "revert"])
def test_storyboard_review_saves_dialogue_and_keeps_storyboard_complete_and_approved(tmp_path, choice):
    flow, session = story_review(tmp_path)
    save_review(flow, "story", [{"choice": "revert"}, {"choice": "revert"}], expected=input_hash(session.model_dump(mode="json")))
    flow.run()
    script = flow.artifact("script").model_dump(mode="json", by_alias=True)
    script["scenes"][0]["dialogue"] = [{"characterId": "keeper", "text": "I will protect these memories."}]
    flow.edit("script", json.dumps(script))
    flow.generate("storyboard")
    from story_channel.dialogue_review import DialogueReport, DialogueIssue, SavedDialogueReview, dialogue_context
    report = DialogueReport(summary="Refine dialogue", recommendation="revise", issues=[DialogueIssue(scene_id="scene_1", line_index=0,
        category="intent", severity="error", description="Unclear goal", suggestion="Clarify the goal")])
    saved = SavedDialogueReview(source_hash=input_hash(dialogue_context(flow)), report=report, provider="demo", model="demo-v1")
    atomic_write(flow.path / "dialogue_review.json", saved.model_dump_json(by_alias=True))
    change = ReviewDifference(path=["scenes", 0, "dialogue", 0, "text"], original=script["scenes"][0]["dialogue"][0]["text"],
                              suggested="I won't let our memories disappear.", reason="Clarify intent", issue_indices=[0])
    review = prepare_review(flow, "storyboard", suggestions=ReviewSuggestions(changes=[change]))
    original_board = (flow.path / "scenes.json").read_bytes()
    board_revision = flow.inspect().steps["storyboard"].revision
    with pytest.raises(ApprovalRequired):
        flow.approve("storyboard")
    save_review(flow, "storyboard", [{"choice": choice, "text": change.suggested}], expected=input_hash(review.model_dump(mode="json")))
    assert resolution_current(flow, "storyboard")
    if choice == "apply":
        assert flow.artifact("script").scenes[0].dialogue[0].text == change.suggested
    assert flow.inspect().steps["storyboard"].status == "succeeded"
    assert flow.inspect().steps["storyboard"].revision == board_revision
    assert (flow.path / "scenes.json").read_bytes() == original_board
    assert "storyboard" in flow.inspect().approvals
    assert json.loads((flow.path / "dialogue_review.json").read_text())["sourceHash"] == saved.source_hash


def test_ui_resolve_each_difference_edit_resets_choice_and_saves_approval(tmp_path, monkeypatch):
    flow, session = story_review(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(flow.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert not any(b.key == "browse-story-approval" for b in app.button)
    app.button(key="browse-review").click().run()
    assert not app.exception
    prefix = f"ai-review-{flow.project_id}-story-{input_hash(session.model_dump(mode='json'))}"
    assert app.button(key="ai-review-save-story").disabled
    app.button(key=prefix + "-apply-0").click().run()
    app.button(key=prefix + "-revert-1").click().run()
    assert not app.button(key="ai-review-save-story").disabled
    app.text_area(key=prefix + "-draft-0").set_value("A manually edited setup.").run()
    assert app.button(key="ai-review-save-story").disabled
    app.button(key=prefix + "-apply-0").click().run()
    app.button(key="ai-review-save-story").click().run()
    from test_ui import finish_background
    finish_background(app)
    assert not app.exception
    assert flow.artifact("story").setup == "A manually edited setup."
    assert "story" in flow.inspect().approvals
    assert not any(b.key == "ai-review-save-story" for b in app.button)


def test_production_suggestion_preparation_uses_exact_source_and_defers_all_edits(tmp_path, monkeypatch):
    flow, session = story_review(tmp_path, production=True)
    original = (flow.path / "story.json").read_bytes()
    calls = []
    def suggest(pipeline, current, instructions, stage, *, context):
        calls.append((stage, context))
        return ReviewSuggestions(changes=session.changes)
    monkeypatch.setattr("story_channel.modification.modify_content", suggest)
    prepared = prepare_review(flow, "story", force=True)
    assert len(calls) == 1 and calls[0][0] == "ai_review_story_suggestions"
    assert calls[0][1]["ORIGINAL"] == session.original
    assert len(calls[0][1]["findings"]) == 2
    assert prepared.changes == session.changes
    assert (flow.path / "story.json").read_bytes() == original
    assert not flow.inspect().approvals


def test_review_request_failure_is_shown_inside_review_dialog(tmp_path, monkeypatch):
    flow, session = story_review(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(flow.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    original = (flow.path / "story.json").read_bytes()
    def unavailable(*args, **kwargs):
        raise RuntimeError("Review provider unavailable")
    monkeypatch.setattr(Pipeline, "generate", unavailable)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="browse-review").click().run()
    app.button(key="run-ai-review-story").click().run()
    if "workspace-task" in app.session_state:
        with pytest.raises(RuntimeError, match="unavailable"):
            app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert any("Review provider unavailable" in error.value for error in app.error)
    assert (flow.path / "story.json").read_bytes() == original
    assert not flow.inspect().approvals


@pytest.mark.parametrize("prepared", [True, False])
def test_approve_unchanged_keeps_original_and_declines_all_findings_without_ai(tmp_path, monkeypatch, prepared):
    flow, session = story_review(tmp_path)
    if not prepared:
        (flow.path / "ai_review_story_changes.json").unlink()
    original = (flow.path / "story.json").read_bytes()
    revision = flow.inspect().steps["story"].revision
    context, report = review_inputs(flow, "story")
    def forbidden(*args, **kwargs):
        raise AssertionError("Keeping the Story unchanged must not call AI")
    monkeypatch.setattr("story_channel.modification.modify_content", forbidden)
    with pytest.raises(ValueError, match="changed"):
        approve_story_unchanged(flow, source_hash="outdated-source", report_hash=input_hash(report),
                                report_revision=flow.inspect().steps["review"].revision)
    assert not flow.inspect().approvals
    approve_story_unchanged(flow, source_hash=input_hash(context), report_hash=input_hash(report),
                            report_revision=flow.inspect().steps["review"].revision)
    assert (flow.path / "story.json").read_bytes() == original
    assert flow.inspect().steps["story"].revision == revision
    assert "story" in flow.inspect().approvals
    saved = current_session(flow, "story")
    assert saved.saved_hash and all(d["choice"] == "revert" for d in saved.decisions)
    assert set(i for change in saved.changes for i in change.issue_indices) == {0, 1}
    flow.generate("script")
    assert flow.inspect().steps["script"].status == "succeeded"


def test_unchanged_button_discards_applied_draft_and_skips_remaining_decisions(tmp_path, monkeypatch):
    flow, session = story_review(tmp_path)
    original = (flow.path / "story.json").read_bytes()
    monkeypatch.setenv("STORY_DATA_ROOT", str(flow.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="browse-review").click().run()
    prefix = f"ai-review-{flow.project_id}-story-{input_hash(session.model_dump(mode='json'))}"
    app.text_area(key=prefix + "-draft-0").set_value("This draft must not be saved.").run()
    app.button(key=prefix + "-apply-0").click().run()
    assert app.button(key="ai-review-save-story").disabled
    app.button(key="ai-review-approve-unchanged-story").click().run()
    from test_ui import finish_background
    finish_background(app)
    assert not app.exception
    assert (flow.path / "story.json").read_bytes() == original
    assert "story" in flow.inspect().approvals
