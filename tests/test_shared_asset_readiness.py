from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.assets_status import assets_status_rows
from story_channel.assets_workflow import next_assets_action
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.pipeline import ApprovalRequired
from test_studio import world
from asset_test_helpers import complete_character_parts


def references(world, *, voice=True):
    world.generate_characters()
    identity = IdentityManager(world.manager, world.project_id)
    library = identity.load()
    if voice:
        expected = library.model_dump_json(by_alias=True)
        for profile in library.voices.values():
            profile.voice_id = "nova"
        identity.save(library, expected=expected)
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references")
    if voice:
        complete_character_parts(engine)
        engine.approve("characters")
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    return engine, identity


def test_reference_approvals_replace_separate_library_gate_but_voice_review_remains(world):
    engine, identity = references(world)
    assert identity.load().approval is None
    assert identity.require_approved()
    assert next_assets_action(engine, engine.inspect()).stage == "complete"
    media = LibraryMedia(world.manager, world.project_id)
    for cid in identity.load().voices:
        assert media.voice_approved(cid)
    assert next_assets_action(engine, engine.inspect()).stage == "complete"
    assert identity.load().approval is None
    rows = assets_status_rows(engine, engine.inspect())
    assert not any(row["Stage"] == "identity_approval" for row in rows)
    # Costume edits preserve the base visual but reopen the complete-character approval.
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    next(iter(library.costumes.values())).description = "A red raincoat"
    identity.save(library, expected=expected)
    with pytest.raises(ApprovalRequired):
        identity.require_approved()
    complete_character_parts(engine)
    engine.approve("characters")
    assert identity.require_approved()


def test_voice_ids_are_still_required_and_ui_has_no_identity_status_or_approval(world, monkeypatch):
    engine, identity = references(world, voice=False)
    with pytest.raises(ApprovalRequired):
        identity.require_approved()
    assert next_assets_action(engine, engine.inspect()).stage == "voice_settings"
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert not any("Identity library status" in item.value for item in app.markdown)
    assert not any(button.key == "identity-approve-" + world.project_id for button in app.button)
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Props"
    app.run()
    assert any(expander.label == "Shared asset settings (advanced)" for expander in app.expander)
    app.button(key="studio-assets-next").click().run()
    assert not app.exception
    for cid in identity.load().voices:
        assert app.session_state[f"page-tab-character-{world.project_id}-{cid}"] == "Voice"
