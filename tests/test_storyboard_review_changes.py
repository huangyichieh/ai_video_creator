from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.dialogue_review import DialogueIssue, DialogueReport, SavedDialogueReview, dialogue_context
from story_channel.files import atomic_write, input_hash
from story_channel.pipeline import ApprovalRequired
from story_channel.review_changes import (ReviewDifference, ReviewSuggestions, approve_storyboard_unchanged,
    current_session, prepare_review, save_review, storyboard_approval_snapshot)
from test_pipeline import flow, complete
from test_ui import finish_background


@pytest.fixture
def board_review(flow):
    complete(flow)
    script = flow.artifact("script")
    from story_channel.creative import Dialogue
    script.scenes[0].dialogue = [Dialogue(character_id="keeper", text="I will protect our memories."),
                                 Dialogue(character_id="keeper", text="Come with me.")]
    flow.edit("script", script.model_dump_json(by_alias=True))
    flow.generate("storyboard")
    flow.approve("storyboard")
    flow.generate("prompts")
    issues = [DialogueIssue(scene_id=script.scenes[0].scene_id, line_index=index, category="intent", severity="warning",
                           description="Clarify intent", suggestion="Make the goal clearer") for index in range(2)]
    report = SavedDialogueReview(source_hash=input_hash(dialogue_context(flow)),
        report=DialogueReport(summary="Clarify dialogue", recommendation="revise", issues=issues), provider="demo", model="demo-v1")
    atomic_write(flow.path / "dialogue_review.json", report.model_dump_json(by_alias=True))
    changes = [ReviewDifference(path=["scenes", 0, "dialogue", index, "text"], original=line.text,
        suggested=line.text + " Before the storm arrives.", reason="Clarify intent", issue_indices=[index])
        for index, line in enumerate(script.scenes[0].dialogue)]
    session = prepare_review(flow, "storyboard", suggestions=ReviewSuggestions(changes=changes))
    return flow, session


def open_review(flow, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(flow.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=20).run()
    assert app.button(key="generate-next-stage").label == "Next stage: AI Review (Storyboard)"
    app.button(key="generate-next-stage").click().run()
    assert not app.exception
    from legacy_pages import stage_rows
    assert any(row["Stage"] == "AI Review (Storyboard)" for row in stage_rows(flow.inspect()))
    return app


@pytest.mark.parametrize("prepared", [True, False])
def test_storyboard_unchanged_approval_declines_all_findings_and_preserves_outputs(board_review, monkeypatch, prepared):
    flow, session = board_review
    if not prepared:
        (flow.path / "ai_review_storyboard_changes.json").unlink()
    before = {name: (flow.path / name).read_bytes() for name in ("script.json", "scenes.json", "prompts/prompts.json")}
    def forbidden(*args, **kwargs):
        raise AssertionError("Unchanged approval must not call a provider")
    monkeypatch.setattr(flow.provider, "generate", forbidden)
    monkeypatch.setattr("story_channel.modification.modify_content", forbidden)
    approve_storyboard_unchanged(flow, expected=storyboard_approval_snapshot(flow))
    state = flow.inspect()
    assert state.current_step == "complete"
    assert "storyboard" in state.approvals
    assert state.steps["prompts"].status == "succeeded"
    assert all((flow.path / name).read_bytes() == content for name, content in before.items())
    saved = current_session(flow, "storyboard")
    assert saved.saved_hash and all(decision["choice"] == "revert" for decision in saved.decisions)
    assert set(i for change in saved.changes for i in change.issue_indices) == {0, 1}


def test_storyboard_approval_rejects_stale_snapshot_without_writing(board_review):
    flow, session = board_review
    expected = storyboard_approval_snapshot(flow)
    board = flow.artifact("storyboard")
    board.scenes[0].visual_description += " New lighting."
    flow.edit("storyboard", board.model_dump_json(by_alias=True))
    with pytest.raises(ValueError, match="changed"):
        approve_storyboard_unchanged(flow, expected=expected)
    with pytest.raises(ValueError, match="changed"):
        save_review(flow, "storyboard", [{"choice": "revert"}] * 2,
                    expected=input_hash(session.model_dump(mode="json")), approval_expected=expected)
    assert "storyboard" not in flow.inspect().approvals
    assert not current_session(flow, "storyboard").saved_hash


def test_storyboard_save_requires_every_decision_and_approves_reverted_content(board_review):
    flow, session = board_review
    expected = input_hash(session.model_dump(mode="json"))
    with pytest.raises(ApprovalRequired):
        save_review(flow, "storyboard", [{"choice": "revert"}], expected=expected)
    assert "storyboard" not in flow.inspect().approvals
    save_review(flow, "storyboard", [{"choice": "revert"}] * 2, expected=expected)
    assert flow.inspect().current_step == "complete"
    assert "storyboard" in flow.inspect().approvals


def test_storyboard_ui_edit_resets_decision_and_approves_without_regenerating_board(board_review, monkeypatch):
    flow, session = board_review
    app = open_review(flow, monkeypatch)
    prefix = f"ai-review-{flow.project_id}-storyboard-{input_hash(session.model_dump(mode='json'))}"
    original = flow.artifact("script").scenes[0].dialogue[1].text
    original_board = (flow.path / "scenes.json").read_bytes()
    original_state = flow.inspect().steps["storyboard"].model_copy(deep=True)
    assert app.button(key="ai-review-save-storyboard").disabled
    app.button(key=prefix + "-apply-0").click().run()
    app.button(key=prefix + "-revert-1").click().run()
    assert not app.button(key="ai-review-save-storyboard").disabled
    app.text_area(key=prefix + "-draft-0").set_value("I refuse to lose these memories.").run()
    assert app.button(key="ai-review-save-storyboard").disabled
    app.button(key=prefix + "-apply-0").click().run()
    app.button(key="ai-review-save-storyboard").click().run()
    finish_background(app)
    assert not app.exception
    assert flow.artifact("script").scenes[0].dialogue[0].text == "I refuse to lose these memories."
    assert flow.artifact("script").scenes[0].dialogue[1].text == original
    from story_channel.pipeline import Pipeline
    current = Pipeline(flow.manager, flow.project_id).inspect()
    current = Pipeline(flow.manager, flow.project_id).inspect()
    assert "storyboard" in current.approvals
    assert current.steps["storyboard"].status == "succeeded"
    assert current.steps["storyboard"].revision == original_state.revision
    assert current.steps["storyboard"].attempts == original_state.attempts
    assert current.steps["storyboard"].input_hash == original_state.input_hash
    assert (flow.path / "scenes.json").read_bytes() == original_board
    assert current.steps["prompts"].status == "stale"
    assert current.current_step == "prompts"
    assert not any(button.key == "ai-review-save-storyboard" for button in app.button)


def test_storyboard_ui_unchanged_discards_draft_and_skips_pending_decisions(board_review, monkeypatch):
    flow, session = board_review
    original = (flow.path / "script.json").read_bytes()
    app = open_review(flow, monkeypatch)
    prefix = f"ai-review-{flow.project_id}-storyboard-{input_hash(session.model_dump(mode='json'))}"
    app.text_area(key=prefix + "-draft-0").set_value("Discard this draft.").run()
    app.button(key=prefix + "-apply-0").click().run()
    assert app.button(key="ai-review-save-storyboard").disabled
    app.button(key="ai-review-approve-unchanged-storyboard").click().run()
    finish_background(app)
    assert not app.exception
    assert (flow.path / "script.json").read_bytes() == original
    assert "storyboard" in flow.inspect().approvals
    assert not any(button.key == "ai-review-approve-unchanged-storyboard" for button in app.button)


@pytest.mark.parametrize("mismatch", [None, "board_file", "board_inputs", "later_script", "pending_receipt", "upstream"])
def test_legacy_saved_review_recovers_only_the_unchanged_reviewed_storyboard(board_review, mismatch):
    flow, session = board_review
    save_review(flow, "storyboard", [{"choice": "apply", "text": change.suggested} for change in session.changes],
                expected=input_hash(session.model_dump(mode="json")))
    manifest = flow.manager.load(flow.project_id)
    original = manifest.steps["storyboard"].model_copy(deep=True)
    manifest.steps["storyboard"].status = "stale"
    manifest.steps["storyboard"].retained_input_hash = ""
    manifest.approvals.pop("storyboard")
    manifest.approved_steps = ["story"]
    manifest.current_step = "storyboard"
    if mismatch == "board_inputs":
        manifest.steps["storyboard"].input_hash = "unrelated-generation-context"
    elif mismatch == "upstream":
        manifest.steps["review"].status = "stale"
    flow.manager.save(manifest)
    if mismatch == "board_file":
        board = flow.artifact("storyboard")
        board.scenes[0].visual_description += " A separate external change."
        flow._write_outputs("storyboard", board)
    elif mismatch == "later_script":
        script = flow.artifact("script")
        script.scenes[0].narration += " A later manual change."
        flow.edit("script", script.model_dump_json(by_alias=True))
    elif mismatch == "pending_receipt":
        from story_channel.review_changes import write_session
        saved = current_session(flow, "storyboard")
        saved.decisions = []
        write_session(flow, saved)
    from story_channel.pipeline import Pipeline
    current = Pipeline(flow.manager, flow.project_id).inspect()
    current = Pipeline(flow.manager, flow.project_id).inspect()
    if mismatch is not None:
        assert current.steps["storyboard"].status == "stale"
        assert "storyboard" not in current.approvals
        return
    assert current.steps["storyboard"].status == "succeeded"
    assert current.steps["storyboard"].revision == original.revision
    assert current.steps["storyboard"].attempts == original.attempts
    assert current.steps["storyboard"].input_hash == original.input_hash
    assert current.current_step == "prompts"
    assert "storyboard" in current.approvals
    import json
    recovered = [json.loads(line) for line in (flow.path / "logs/pipeline.jsonl").read_text().splitlines()
                 if json.loads(line).get("recovered")]
    assert len(recovered) == 1
