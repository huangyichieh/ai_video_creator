import json

from story_channel.dialogue_review import review_dialogue
from story_channel.pipeline import Pipeline
from test_ui import finish_background, widget
from test_workspace_state import workspace


def storyboard_workspace(tmp_path, monkeypatch, *, reviewed=True):
    manager, project, app = workspace(tmp_path, monkeypatch)
    flow = Pipeline(manager, project.project_id, demo=True)
    flow.run()
    flow.approve("story")
    flow.run()
    if reviewed:
        review_dialogue(flow)
    return manager, project, app, flow


def test_stale_dialogue_review_can_be_refreshed_in_approval_dialog(tmp_path, monkeypatch):
    manager, project, app, flow = storyboard_workspace(tmp_path, monkeypatch)
    script = flow.artifact("script").model_dump(mode="json", by_alias=True)
    script["scenes"][0]["narration"] += " The keeper resolves to protect these memories."
    flow.edit("script", json.dumps(script))
    flow.generate("storyboard")
    original = (flow.path / "script.json").read_bytes()
    app.run()
    app.button(key="browse-storyboard-approval").click().run()
    assert app.button(key="ai-review-save-storyboard").disabled
    assert any("Dialogue review is outdated" in item.value for item in app.warning)
    app.button(key="review-storyboard-dialogue").click().run()
    finish_background(app)
    assert not app.exception
    assert not app.button(key="ai-review-save-storyboard").disabled
    assert any("Dialogue review is current" in item.value for item in app.success)
    assert (flow.path / "script.json").read_bytes() == original
    app.button(key="ai-review-save-storyboard").click().run()
    finish_background(app)
    assert "storyboard" in manager.load(project.project_id).approvals


def test_current_dialogue_errors_explain_and_block_approval(tmp_path, monkeypatch):
    manager, project, app, flow = storyboard_workspace(tmp_path, monkeypatch)
    file = flow.path / "dialogue_review.json"
    data = json.loads(file.read_text(encoding="utf-8"))
    data["report"]["issues"] = [{"sceneId": "scene_1", "lineIndex": 0, "category": "emotion",
        "severity": "error", "description": "Emotion conflicts with the scene.",
        "suggestion": "Adjust the line's intent."}]
    file.write_text(json.dumps(data), encoding="utf-8")
    app.run()
    app.button(key="browse-storyboard-approval").click().run()
    assert not app.exception
    assert app.button(key="ai-review-save-storyboard").disabled
    assert not app.button(key="ai-review-approve-unchanged-storyboard").disabled
    assert any("Dialogue review found errors" in item.value for item in app.error)
    assert any("Adjust the line's intent" in item.value for item in app.markdown)
    assert "storyboard" not in manager.load(project.project_id).approvals


def test_review_tab_starts_review_and_shows_result_with_optional_json(tmp_path, monkeypatch):
    manager, project, app, flow = storyboard_workspace(tmp_path, monkeypatch, reviewed=False)
    app.run()
    app.button(key="browse-storyboard-approval").click().run()
    review_tab = next(tab for tab in app.tabs if tab.label == "AI Review (Storyboard)")
    assert review_tab.button(key="review-storyboard-dialogue")
    assert any("No dialogue review" in item.value for item in review_tab.caption)
    assert not (flow.path / "dialogue_review.json").exists()
    review_tab.button(key="review-storyboard-dialogue").click().run()
    finish_background(app)
    review_tab = next(tab for tab in app.tabs if tab.label == "AI Review (Storyboard)")
    assert any("# AI Review (Storyboard)" in item.value for item in review_tab.markdown)
    assert any("## Summary" in item.value for item in review_tab.markdown)
    assert "dialogue_review.md" in {item.value for item in review_tab.caption}
    assert "dialogue_review.json" not in {item.value for item in review_tab.caption}
    app.button(key="toggle-approval-json-storyboard").click().run()
    review_tab = next(tab for tab in app.tabs if tab.label == "AI Review (Storyboard)")
    assert "dialogue_review.json" in {item.value for item in review_tab.caption}
    assert not app.exception
