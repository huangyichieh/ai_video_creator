import pytest
from pathlib import Path
from streamlit.testing.v1 import AppTest

from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.studio import WorldStudio, TextAdvice, TextReview
from story_channel.pipeline import ApprovalRequired
from story_channel.files import input_hash, read_text
from story_channel.worldview import WORLD_DRAFT_POINTS
from test_studio import world
from test_ui import finish_background


@pytest.fixture
def draft(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo", asset_provider="demo"))
    project = WorldStudio.create(manager, "A hopeful coastal world where lighthouses guide lost memories home.")
    return WorldStudio(manager, project.project_id)


def test_initial_world_requires_review_and_explicit_decision_and_reopens_after_edit(draft):
    expected = input_hash(draft.load().base_text)
    with pytest.raises(ApprovalRequired, match="AI review"):
        draft.approve_base(expected=expected)
    draft.review()
    with pytest.raises(ApprovalRequired, match="apply or revert"):
        draft.approve_base(expected=expected)
    draft.resolve_base("revert", "Ignored edited proposal", expected=expected)
    assert input_hash(draft.load().base_text) == expected
    draft.edit_base("A hopeful coastal world. Memories cannot be duplicated.")
    with pytest.raises(ApprovalRequired, match="current World draft"):
        draft.approve_base(expected=input_hash(draft.load().base_text))
    draft.review()
    draft.resolve_base("apply", draft.load().base_text + " Magic always has a cost.", expected=input_hash(draft.load().base_text))
    result = draft.approve_base(expected=input_hash(draft.load().base_text))
    assert result.approval and result.base_text.endswith("Magic always has a cost.")


def test_selected_manual_world_edit_is_checked_and_conflicts_prevent_approval(draft, monkeypatch):
    draft.review()
    selected = draft.load().base_text + " Memories both can and cannot be duplicated."
    draft.resolve_base("apply", selected, expected=input_hash(draft.load().base_text))
    calls = []
    def check(pipeline, text, context, **kwargs):
        calls.append((text, context, kwargs["stage"]))
        return TextReview(source_hash=input_hash(text), context_hash=input_hash(context),
            advice=TextAdvice(proposed_text=text, conflicts=["Conflicting memory duplication rules"]))
    monkeypatch.setattr("story_channel.studio.generate_advice", check)
    with pytest.raises(ApprovalRequired, match="duplication"):
        draft.approve_base(expected=input_hash(selected))
    assert calls == [(selected, {}, "world_base_consistency")]
    assert not draft.load().approval
    stored = draft.load().review
    assert stored.verification.conflicts


def test_edited_addition_is_checked_against_base_and_all_approved_additions(world, monkeypatch):
    first = world.add_setting("A lighthouse may restore only one memory at a time.")
    world.review(setting_id=first.setting_id)
    world.approve_setting(first.setting_id, text=first.text, decision="revert")
    pending = world.add_setting("The harbor has another memory beacon.")
    world.review(setting_id=pending.setting_id)
    selected = "Every lighthouse may restore unlimited memories simultaneously."
    seen = []
    def check(pipeline, text, context, **kwargs):
        seen.append((text, context))
        return TextReview(source_hash=input_hash(text), context_hash=input_hash(context),
            advice=TextAdvice(proposed_text=text, conflicts=["Violates the one-memory limit"]))
    monkeypatch.setattr("story_channel.studio.generate_advice", check)
    with pytest.raises(ApprovalRequired, match="one-memory"):
        world.approve_setting(pending.setting_id, text=selected, decision="apply")
    assert seen[0][0] == selected
    assert seen[0][1] == {"worldSettings": world.load().base_text, "additionalSettings": [first.text]}
    unchanged = world.load().additions[1]
    assert not unchanged.approval and unchanged.text == pending.text
    assert unchanged.review.verification.conflicts


def test_new_project_offers_fields_for_all_worldview_points(tmp_path, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(tmp_path / "channel"))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    assert {label for label, _ in WORLD_DRAFT_POINTS} <= {area.label for area in app.text_area}
    app.text_area(key="studio-draft-new-world-premise").set_value("A short description is sufficient.")
    next(button for button in app.button if button.label == "Create project").click().run()
    assert next(area for area in app.text_area if area.label == "Premise").value == "A short description is sufficient."
    assert not app.exception


def test_additional_setting_ui_resets_decision_when_proposal_is_edited(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="world-add-settings-" + world.project_id).click().run()
    next(t for t in app.text_area if t.label == "New additional setting").set_value("Beacon light may store a memory overnight.")
    next(b for b in app.button if b.label == "Add draft").click().run()
    assert not app.exception
    item = world.load().additions[0]
    app.button(key="addition-review-" + item.setting_id).click().run()
    finish_background(app)
    assert app.button(key="addition-approve-" + item.setting_id).disabled
    app.button(key="addition-apply-" + item.setting_id).click().run()
    assert not app.button(key="addition-approve-" + item.setting_id).disabled
    report = world.load().additions[0].review
    key = f"studio-draft-addition-proposal-{item.setting_id}-{report.source_hash[:8]}-{report.review_id}"
    selected = "Beacon light may store one consenting person's memory overnight."
    app.text_area(key=key).set_value(selected).run()
    assert app.button(key="addition-approve-" + item.setting_id).disabled
    app.button(key="addition-apply-" + item.setting_id).click().run()
    app.button(key="addition-approve-" + item.setting_id).click().run()
    finish_background(app)
    assert not app.exception
    assert world.load().additions[0].approval
    assert world.load().additions[0].text == selected
