from asset_test_helpers import complete_character_parts
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.assets_status import world_assets_status_rows
from story_channel.character_removal import CharacterRemoval
from story_channel.studio import Episodes
from story_channel.files import atomic_write
from test_studio import world
from test_ui import finish_background


def cast(world):
    world.generate_characters()
    world.generate_characters("Guest")
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    ids = [c.character_id for c in world.pipeline.artifact("characters").characters]
    return engine, ids


def test_complete_visual_voice_and_all_costumes_are_required_and_changes_reopen_approval(world):
    from story_channel.identity import IdentityManager
    from story_channel.library_media import LibraryMedia
    from story_channel.assets_workflow import next_assets_action
    engine, (first, second) = cast(world)
    engine.generate("character_references")
    assert engine.character_requirements(first, engine.load()) == {"Visual": True, "Voice": False, "Costumes": False}
    with pytest.raises(ValueError, match="Voice, Costumes"):
        engine.approve_character(first)
    with pytest.raises(ValueError, match="Complete character assets"):
        engine.approve("characters")
    assert next_assets_action(engine, engine.inspect()).stage == "voice_settings"
    identity = IdentityManager(world.manager, world.project_id)
    saved = identity.load()
    expected = saved.model_dump_json(by_alias=True)
    saved.voices[first].voice_id = "nova"
    identity.save(saved, expected=expected)
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    media.talking_sample(first, "A test sample.", video=False)
    assert not engine.character_requirements(first, engine.load())["Voice"]
    media.approve_voice(first)
    assert engine.character_requirements(first, engine.load())["Voice"]
    with pytest.raises(ValueError, match="Costumes"):
        engine.approve_character(first)
    complete_character_parts(engine, [first])
    state = engine.approve_character(first)
    assert engine.character_approved(first, state)
    identity.save_costume(first, "raincoat", "A red raincoat")
    state = engine.inspect()
    assert not engine.character_approved(first, state)
    assert state.records["character_references:" + first].status == "succeeded"
    complete_character_parts(engine, [first])
    engine.approve_character(first)
    media.talking_sample(first, "A changed sample.", video=False)
    assert not engine.character_approved(first, engine.inspect())
    media.approve_voice(first)
    engine.approve_character(first)
    costume = next(c for c in identity.load().costumes.values() if c.character_id == first)
    media_file = media.load()["visuals"]["costume:" + costume.costume_id][-1]["asset"]["filePath"]
    engine.local_path(media_file).write_bytes(b"modified")
    assert not engine.character_requirements(first, engine.inspect())["Costumes"]


def test_character_approval_buttons_disabled_until_every_section_is_complete(world, monkeypatch):
    engine, (first, second) = cast(world)
    engine.generate("character_references")
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert any(item.value == "**Total characters: 2**" for item in app.markdown)
    assert app.button(key="approve-character-" + first).disabled
    assert app.button(key="studio-approve-characters").disabled
    complete_character_parts(engine, [first])
    app.run()
    assert not app.button(key="approve-character-" + first).disabled
    assert app.button(key="approve-character-" + second).disabled
    assert app.button(key="studio-approve-characters").disabled
    complete_character_parts(engine, [second])
    app.run()
    assert not app.button(key="studio-approve-characters").disabled
    app.button(key="studio-approve-characters").click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key="studio-approve-characters").disabled
    assert all(engine.character_approved(cid, engine.load()) for cid in (first, second))


def test_voice_approval_disabled_with_missing_reasons_and_character_tooltip(world, monkeypatch):
    from story_channel.identity import IdentityManager
    from story_channel.library_media import LibraryMedia
    engine, (first, _) = cast(world)
    engine.generate("character_references")
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    library.voices[first].voice_id = "nova"
    identity.save(library, expected=expected)
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    media.talking_sample(first, "An excited sample.", "excited", video=False)
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    app.session_state[f"page-tab-character-{world.project_id}-{first}"] = "Voice"
    app.run()
    button = app.button(key="studio-voice-approve-" + first)
    assert button.disabled and "neutral voice sample" in button.proto.help
    assert any("Missing:" in caption.value and "neutral voice sample" in caption.value for caption in app.caption)
    hint = app.button(key="approve-character-" + first).proto.help
    assert "neutral voice sample" in hint and "Costume " + first + "_default" in hint
    media.talking_sample(first, "A neutral sample.", video=False)
    app.run()
    assert not app.button(key="studio-voice-approve-" + first).disabled
    excited = media.selected_samples(first)[0]
    engine.local_path(excited["audio"]).write_bytes(b"modified audio")
    app.run()
    assert app.button(key="studio-voice-approve-" + first).disabled
    assert "excited" in app.button(key="studio-voice-approve-" + first).proto.help
    assert any("excited" in caption.value and "missing or modified" in caption.value for caption in app.caption)
    with pytest.raises(ValueError, match="excited"):
        media.approve_voice(first)


def test_approve_individually_before_all_images_exist_and_unlock_after_every_approval(world):
    engine, (first, second) = cast(world)
    with pytest.raises(ValueError, match="current character reference"):
        engine.approve_character(first)
    state = engine.generate("character_references", subject=first)
    asset_id = state.records["character_references:" + first].asset.asset_id
    with pytest.raises(ValueError, match="changed"):
        engine.approve_character(first, expected_asset_id="another-asset")
    complete_character_parts(engine, [first])
    state = engine.approve_character(first, expected_asset_id=asset_id)
    assert engine.character_approved(first, state)
    assert not engine.character_approved(second, state)
    assert "characters" not in world.manager.load(world.project_id).asset_approvals
    rows = {(row["Stage"], row["Subject"]): row for row in world_assets_status_rows(engine, state)}
    assert rows["character_references", first]["Approval"] == "Approved"
    assert rows["character_references", second]["Approval"] == "Pending"
    engine.generate("character_references", subject=second)
    complete_character_parts(engine, [second])
    state = engine.approve_character(second)
    assert all(engine.character_approved(cid, state) for cid in (first, second))
    assert "characters" in world.manager.load(world.project_id).asset_approvals
    assert state.next_stage == "backgrounds"


def test_approve_all_and_regeneration_or_revision_application_only_reopen_changed_character(world):
    engine, (first, second) = cast(world)
    old = engine.generate("character_references").records["character_references:" + first].asset.asset_id
    complete_character_parts(engine)
    state = engine.approve("characters")
    assert all(engine.character_approved(cid, state) for cid in (first, second))
    state = engine.generate("character_references", subject=first, force=True)
    assert not engine.character_approved(first, state) and engine.character_approved(second, state)
    assert "characters" not in world.manager.load(world.project_id).asset_approvals
    complete_character_parts(engine, [first])
    engine.approve_character(first)
    state = engine.apply_revision("character_references", first, old)
    assert not engine.character_approved(first, state) and engine.character_approved(second, state)
    complete_character_parts(engine, [first])
    engine.approve_character(first)
    # Changes to nonvisual details also require this character's approval again.
    current = world.pipeline.artifact("characters").characters[0]
    world.pipeline.edit_character(first, current.model_copy(update={"background": "Raised in the northern harbor"}).model_dump_json(by_alias=True),
                                  expected=current.model_dump_json(by_alias=True))
    state = engine.inspect()
    assert not engine.character_approved(first, state) and engine.character_approved(second, state)


def test_old_group_approval_migrates_to_individual_approvals(world):
    engine, ids = cast(world)
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    original = world.manager.load(world.project_id).asset_approvals["characters"]
    data = json.loads(engine.state_path.read_text(encoding="utf-8"))
    data.pop("characterApprovals")
    atomic_write(engine.state_path, json.dumps(data))
    state = engine.inspect()
    assert all(engine.character_approved(cid, state) for cid in ids)
    assert world.manager.load(world.project_id).asset_approvals["characters"] == original


def test_individually_approved_character_in_use_cannot_be_removed(world):
    engine, (first, second) = cast(world)
    engine.generate("character_references", subject=first)
    complete_character_parts(engine, [first])
    engine.approve_character(first)
    episode = Episodes(world.manager, world.project_id).create("Episode", "A storm arrives.", 60)
    atomic_write(world.manager.project_path(episode.project_id) / "script.json", json.dumps({"characterIds": [first]}))
    assert "characters" not in world.manager.load(world.project_id).asset_approvals
    status = CharacterRemoval(world.manager, world.project_id).statuses()
    assert not status[first].allowed
    assert status[second].allowed


def test_character_cards_show_pending_then_approved_and_all_button(world, monkeypatch):
    engine, (first, second) = cast(world)
    engine.generate("character_references", subject=first)
    complete_character_parts(engine, [first])
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert app.button(key="studio-approve-characters").label == "Approve all characters"
    assert not app.button(key="approve-character-" + first).disabled
    assert app.button(key="approve-character-" + second).disabled
    assert any(item.value == ":orange[Pending]" for item in app.markdown)
    app.button(key="approve-character-" + first).click().run()
    finish_background(app)
    assert not app.exception
    app.session_state[f"character-library-card-{world.project_id}-{first}"] = True
    app.run()
    assert app.button(key="approve-character-" + first).label == "Approved"
    assert app.button(key="approve-character-" + first).disabled
    assert any(":green[Approved]" in item.label for item in app.expander)
    assert any(item.value == "1/2 characters approved" for item in app.caption)
