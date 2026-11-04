from asset_test_helpers import complete_character_parts
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.character_removal import CharacterRemoval
from story_channel.assets import AssetPipeline
from story_channel.studio import Episodes
from story_channel.identity import IdentityManager
from story_channel.files import atomic_write
from story_channel.locking import project_lock, ProjectBusyError
from test_studio import world


def setup_cast(world, *, approved=False):
    world.generate_characters()
    world.generate_characters("Guest")
    cast = world.pipeline.artifact("characters").characters
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references")
    if approved:
        complete_character_parts(engine)
        engine.approve("characters")
    return cast


def test_approved_character_used_by_episode_is_blocked_and_check_is_repeated(world):
    keeper, guest = setup_cast(world, approved=True)
    service = CharacterRemoval(world.manager, world.project_id)
    assert service.statuses()[keeper.character_id].allowed
    episode = Episodes(world.manager, world.project_id).create("Episode", "A storm arrives.", 60)
    atomic_write(world.manager.project_path(episode.project_id) / "story.json", json.dumps({"hook": keeper.name + " finds a lamp."}))
    before = (world.path / "characters.json").read_bytes()
    status = service.statuses()[keeper.character_id]
    assert not status.allowed and "Episode · Story" in status.uses
    with pytest.raises(ValueError, match="approved and appears"):
        service.remove(keeper.character_id, expected=keeper.model_dump_json(by_alias=True))
    assert (world.path / "characters.json").read_bytes() == before
    assert service.statuses()[guest.character_id].allowed
    service.remove(guest.character_id, expected=guest.model_dump_json(by_alias=True))
    assert [c.character_id for c in world.pipeline.artifact("characters").characters] == [keeper.character_id]


def test_unapproved_used_character_can_be_removed_without_changing_episode_snapshot(world):
    keeper, guest = setup_cast(world)
    episode = Episodes(world.manager, world.project_id).create("Episode", "A storm arrives.", 60)
    path = world.manager.project_path(episode.project_id)
    atomic_write(path / "script.json", json.dumps({"scenes": [{"characterIds": [keeper.character_id]}]}))
    snapshot = {file.name: file.read_bytes() for file in path.iterdir() if file.is_file() and file.name != ".project.lock"}
    service = CharacterRemoval(world.manager, world.project_id)
    assert service.statuses()[keeper.character_id].uses
    assert service.statuses()[keeper.character_id].allowed
    image = AssetPipeline(world.manager, world.project_id).load().records["character_references:" + keeper.character_id].asset.file_path
    service.remove(keeper.character_id, expected=keeper.model_dump_json(by_alias=True))
    assert all((path / name).read_bytes() == data for name, data in snapshot.items())
    assert keeper.character_id not in IdentityManager(world.manager, world.project_id).load().voices
    assert "character_references:" + keeper.character_id not in AssetPipeline(world.manager, world.project_id).load().records
    assert AssetPipeline(world.manager, world.project_id).local_path(image).exists()
    assert list((world.path / "history" / "removed_characters" / keeper.character_id).glob("*/snapshot.json"))
    assert world.require_approved()


def test_usage_checks_ids_names_and_animation_but_not_voice_casting_alone(world):
    keeper, guest = setup_cast(world, approved=True)
    episode = Episodes(world.manager, world.project_id).create("Episode", "A storm arrives.", 60)
    path = world.manager.project_path(episode.project_id)
    atomic_write(path / "animation.json", json.dumps({"plan": {"voices": {keeper.character_id: {"characterId": keeper.character_id}}, "shots": [], "performances": []}}))
    service = CharacterRemoval(world.manager, world.project_id)
    assert service.statuses()[keeper.character_id].allowed
    atomic_write(path / "animation.json", json.dumps({"plan": {"performances": [{"characterId": keeper.character_id}]}}))
    assert not service.statuses()[keeper.character_id].allowed
    assert not service.mentioned(keeper.character_id + "_other", keeper)
    assert service.mentioned(keeper.character_id + "的故事", keeper)


def test_last_character_removal_reopens_initial_generation_and_busy_episode_blocks_removal(world):
    world.generate_characters()
    character = world.pipeline.artifact("characters").characters[0]
    episode = Episodes(world.manager, world.project_id).create("Episode", "A storm arrives.", 60)
    service = CharacterRemoval(world.manager, world.project_id)
    with project_lock(world.manager.project_path(episode.project_id)):
        with pytest.raises(ProjectBusyError):
            service.remove(character.character_id, expected=character.model_dump_json(by_alias=True))
    service.remove(character.character_id, expected=character.model_dump_json(by_alias=True))
    assert not (world.path / "characters.json").exists()
    assert world.manager.load(world.project_id).steps["characters"].status == "pending"
    assert not IdentityManager(world.manager, world.project_id).load().voices
    assert world.require_approved()
    world.generate_characters()
    assert world.pipeline.artifact("characters").characters


def test_remove_character_ui_cancel_then_confirm(world, monkeypatch):
    world.generate_characters()
    character = world.pipeline.artifact("characters").characters[0]
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.button(key="remove-character-" + character.character_id).click().run()
    assert not app.exception
    app.button(key="cancel-remove-character").click().run()
    assert (world.path / "characters.json").exists()
    app.button(key="remove-character-" + character.character_id).click().run()
    app.button(key="confirm-remove-character").click().run()
    assert not app.exception
    assert not (world.path / "characters.json").exists()
    assert app.button(key="studio-add-character").label == "Generate initial characters"
