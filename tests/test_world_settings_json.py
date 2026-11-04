import json
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.files import read_text, input_hash
from story_channel.locking import project_lock, ProjectBusyError
from story_channel.studio import WorldStudio
from story_channel.pipeline import ApprovalRequired
from story_channel.worldview import WorldSettings, WORLD_DRAFT_POINTS
from story_channel.cli import main
from story_channel.logging_config import configure_logging
from test_studio import world
from test_world_review_flow import draft
from test_ui import finish_background


def test_default_language_is_saved_and_inherited_by_new_episodes(draft):
    from story_channel.studio import Episodes
    draft.review()
    draft.edit_settings(draft.load().settings, default_language="ja")
    assert draft.load().review is None
    assert draft.manager.load(draft.project_id).language == "ja"
    assert json.loads(read_text(draft.path / "world_setting.json"))["defaultLanguage"] == "ja"
    draft.review()
    draft.resolve_base("revert", "", expected=input_hash(draft.load().base_text))
    draft.approve_base(expected=input_hash(draft.load().base_text))
    episode = Episodes(draft.manager, draft.project_id).create("Episode", "A keeper finds a memory.", 60)
    assert episode.language == "ja"
    with pytest.raises(ValueError, match="immutable"):
        draft.edit_settings(draft.load().settings, default_language="en")


def test_world_ui_saves_default_language(draft, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(draft.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    selector = next(field for field in app.selectbox if field.label == "Default language")
    app.selectbox(key=selector.key).select("en").run()
    app.button(key="studio-world-save").click().run()
    assert not app.exception
    assert draft.load().default_language == "en"
    assert draft.manager.load(draft.project_id).language == "en"


def test_world_rename_preserves_approved_canon_and_pending_reviews(world):
    addition = world.add_setting("The harbor has a second beacon.")
    world.review(setting_id=addition.setting_id)
    before = world.load()
    canon = world.canon()
    steps = world.manager.load(world.project_id).steps
    world.rename("New lighthouse world")
    after = world.require_approved()
    assert after.settings.name == "New lighthouse world"
    assert world.manager.load(world.project_id).title == "New lighthouse world"
    assert after.approval == before.approval
    assert after.review == before.review
    assert after.additions == before.additions
    assert world.canon() == canon
    assert world.manager.load(world.project_id).steps == steps
    world.save(after)
    assert world.load().settings.name == "New lighthouse world"
    with pytest.raises(ValueError, match="single line"):
        world.rename(" ")


def test_approved_world_name_can_be_saved_in_ui(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    name = next(field for field in app.text_input if field.label == "World name")
    assert not name.disabled
    app.text_input(key=name.key).set_value("Renamed World").run()
    app.button(key="studio-world-save-name").click().run()
    assert not app.exception
    assert world.require_approved().settings.name == "Renamed World"
    assert world.manager.load(world.project_id).title == "Renamed World"
    assert not any(field.label == "Premise" for field in app.text_area)
    assert any(item.value == "**Premise**" for item in app.markdown)


def test_settings_reviews_and_additions_share_one_json_and_migrate_without_changing_approval(world):
    approved = world.add_setting("Lighthouses can store memories overnight.")
    world.review(setting_id=approved.setting_id)
    world.approve_setting(approved.setting_id, text=approved.text, decision="revert")
    pending = world.add_setting("The harbor has a second beacon.")
    world.review(setting_id=pending.setting_id)
    previous = world.load()
    canon = world.canon()
    manifest = (world.path / "manifest.json").read_bytes()
    legacy = previous.model_dump(mode="json", by_alias=True, exclude={"settings", "review"})
    (world.path / "world_settings.json").write_text(json.dumps(legacy), encoding="utf-8")
    (world.path / "world_draft_review.json").write_text(previous.review.model_dump_json(by_alias=True), encoding="utf-8")
    (world.path / "world_settings.md").write_text("Old generated export", encoding="utf-8")
    (world.path / "world_setting.json").unlink()
    with project_lock(world.path):
        with pytest.raises(ProjectBusyError):
            world.migrate()
    assert (world.path / "world_settings.json").exists()
    migrated = world.migrate()
    assert migrated.approval == previous.approval
    assert migrated.additions == previous.additions
    assert migrated.review == previous.review
    assert world.canon() == canon
    assert world.require_approved()
    assert (world.path / "manifest.json").read_bytes() == manifest
    data = json.loads(read_text(world.path / "world_setting.json"))
    assert data["settings"]["premise"]
    assert data["review"]["decision"] == "revert"
    assert len(data["additions"]) == 2
    assert not any((world.path / name).exists() for name in ("world_settings.json", "world_settings.md", "world_draft_review.json"))


def test_failed_migration_keeps_original_files(world, monkeypatch):
    original = read_text(world.path / "world_setting.json")
    (world.path / "world_settings.json").write_text(original, encoding="utf-8")
    (world.path / "world_settings.md").write_text("Retain until migration succeeds", encoding="utf-8")
    (world.path / "world_setting.json").unlink()
    def fail(*args):
        raise OSError("Injected write failure")
    monkeypatch.setattr("story_channel.studio.atomic_write", fail)
    with pytest.raises(OSError, match="Injected"):
        world.migrate()
    assert read_text(world.path / "world_settings.json") == original
    assert (world.path / "world_settings.md").exists()


def test_approved_json_fields_cannot_silently_diverge_from_approved_content(world):
    file = world.path / "world_setting.json"
    data = json.loads(read_text(file))
    data["settings"]["tone"] = "A completely different tone"
    file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ApprovalRequired, match="fields changed"):
        world.load()


def test_structured_settings_roundtrip_and_cli_json_example(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    source = Path(__file__).parents[1] / "input" / "world_setting.json"
    settings = WorldSettings.model_validate_json(read_text(source))
    assert WorldSettings.from_text(settings.text()) == settings
    try:
        assert main(["--data-root", str(tmp_path / "channel"), "create", str(source)]) == 0
        project = json.loads(capsys.readouterr().out)
        file = tmp_path / "channel" / "03_stories" / project["projectId"] / "world_setting.json"
        assert json.loads(read_text(file))["settings"] == settings.model_dump(mode="json", by_alias=True)
        assert not file.with_name("world_settings.md").exists()
        assert project["title"] == settings.name
    finally:
        configure_logging()


def test_world_ui_shows_readonly_base_and_edit_review_approve_controls_for_drafts(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    pending = world.add_setting("A second beacon stands at the harbor.")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    for label, _ in WORLD_DRAFT_POINTS:
        assert not any(area.label == label for area in app.text_area)
        assert any(item.value == "**" + label + "**" for item in app.markdown)
    assert app.button(key="addition-edit-" + pending.setting_id)
    assert app.button(key="addition-review-" + pending.setting_id)
    assert app.button(key="addition-approve-" + pending.setting_id).disabled
    app.button(key="addition-edit-" + pending.setting_id).click().run()
    key = next(area.key for area in app.text_area if area.label == "Additional setting")
    app.text_area(key=key).set_value("A second beacon stands on the harbor's northern pier.").run()
    assert app.button(key="addition-review-" + pending.setting_id).disabled
    app.button(key="addition-save-" + pending.setting_id).click().run()
    assert not app.exception
    assert world.load().additions[0].text.endswith("northern pier.")
    assert not app.button(key="addition-review-" + pending.setting_id).disabled
    assert (world.path / "world_setting.json").exists()


def test_world_field_edit_and_manually_edited_ai_proposal_are_saved_in_single_json(draft, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(draft.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    era = next(area for area in app.text_area if area.label == "Era & setting")
    app.text_area(key=era.key).set_value("A coastal brass-and-lens society.").run()
    app.button(key="studio-world-save").click().run()
    assert draft.load().settings.era_setting == "A coastal brass-and-lens society."
    app.button(key="studio-world-review").click().run()
    finish_background(app)
    report = draft.load().review
    assert not app.text_area
    for label, _ in WORLD_DRAFT_POINTS:
        assert any(item.value == "**" + label + "**" for item in app.markdown)
    app.toggle(key=f"studio-draft-world-proposal-{draft.project_id}-{report.review_id}-editing").set_value(True).run()
    tone_key = f"studio-draft-world-proposal-{draft.project_id}-{report.review_id}-tone"
    app.text_area(key=tone_key).set_value("Mysterious and hopeful.").run()
    app.toggle(key=f"studio-draft-world-proposal-{draft.project_id}-{report.review_id}-editing").set_value(False).run()
    assert not app.text_area
    assert any(item.value == "Mysterious and hopeful." for item in app.text)
    app.button(key="studio-world-apply").click().run()
    app.button(key="studio-world-approve").click().run()
    finish_background(app)
    assert not app.exception
    state = draft.require_approved()
    assert state.settings.tone == "Mysterious and hopeful."
    assert not any(area.label == "Tone" for area in app.text_area)
    data = json.loads(read_text(draft.path / "world_setting.json"))
    assert data["settings"]["tone"] == state.settings.tone
    assert data["review"]["decision"] == "apply"
    assert not any((draft.path / name).exists() for name in ("world_settings.json", "world_settings.md", "world_draft_review.json"))
