import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import locations
from story_channel.file_cache import read_json, read_model
from story_channel.files import atomic_write
from story_channel.creative import Characters
from story_channel.media_utils import file_hash
from test_studio import world


def test_file_cache_invalidates_and_never_shares_mutable_data(world):
    world.generate_characters()
    file = world.path / "characters.json"
    first = read_model(file, Characters)
    name = first.characters[0].name
    first.characters[0].name = "Unsaved edit"
    assert read_model(file, Characters).characters[0].name == name
    value = read_json(file)
    value["characters"][0]["name"] = "Saved edit"
    atomic_write(file, json.dumps(value))
    assert read_model(file, Characters).characters[0].name == "Saved edit"
    assert read_json(file)["characters"][0]["name"] == "Saved edit"


def test_media_hash_cache_does_not_hide_replacements_or_deletion(tmp_path):
    file = tmp_path / "media.png"
    file.write_bytes(b"first")
    original = file_hash(file)
    assert file_hash(file) == original
    file.write_bytes(b"other")  # same length: time/inode version still changes
    assert file_hash(file) != original
    file.unlink()
    import pytest
    with pytest.raises(FileNotFoundError):
        file_hash(file)


def test_unchanged_json_is_read_from_disk_once(tmp_path, monkeypatch):
    from story_channel import file_cache
    file = tmp_path / "saved.json"
    atomic_write(file, '{"name":"Original"}')
    calls = []
    original = file_cache.read_text
    def read(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(file_cache, "read_text", read)
    for _ in range(5):
        value = read_json(file)
        value["name"] = "Local draft"
    assert len(calls) == 1
    atomic_write(file, '{"name":"New revision"}')
    assert read_json(file)["name"] == "New revision"
    assert len(calls) == 2


def test_cached_manifests_and_usage_are_isolated_and_refresh_after_saves(world,monkeypatch):
    from story_channel import file_cache
    from story_channel.costs import CostTracker
    from story_channel.models import UsageRecord
    from decimal import Decimal
    manager,pid = world.manager,world.project_id
    tracker = CostTracker(manager,pid)
    tracker.record(UsageRecord(provider="demo",model="demo",step="test",cost=Decimal(".12")))
    calls = []
    original = file_cache.read_text
    def read(path):
        calls.append(path.name)
        return original(path)
    monkeypatch.setattr(file_cache,"read_text",read)
    file_cache._model.cache_clear()
    file_cache._validated.cache_clear()
    for _ in range(4):
        manifest = manager.load(pid)
        original_title = manifest.title
        manifest.title = "Unsaved title"
        records = tracker.records()
        records[-1].cost = Decimal("999")
    assert calls.count("manifest.json") == 1
    assert calls.count("usage.json") == 1
    assert manager.load(pid).title == original_title
    assert tracker.records()[-1].cost == Decimal(".12")
    manifest = manager.load(pid)
    manifest.title = "Saved title"
    manager.save(manifest)
    tracker.record(UsageRecord(provider="demo",model="demo",step="test",cost=Decimal(".34")))
    assert manager.load(pid).title == "Saved title"
    assert [row.cost for row in tracker.records()[-2:]] == [Decimal(".12"),Decimal(".34")]


def app_for(world, monkeypatch):
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    return app


def test_character_collapse_and_tab_switch_load_only_visible_content_and_keep_drafts(world, monkeypatch):
    world.generate_characters()
    world.generate_characters("Guest")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    app = app_for(world, monkeypatch)
    assert not app.exception
    assert app.button(key="retry-asset-character_references:" + cid)
    assert not any(b.key == "studio-audio-sample-" + cid for b in app.button)
    card = f"character-library-card-{world.project_id}-{cid}"
    app.session_state[card] = False
    app.run()
    assert not any(b.key == "retry-asset-character_references:" + cid for b in app.button)
    app.session_state[card] = True
    app.session_state[f"page-tab-character-{world.project_id}-{cid}"] = "Voice"
    app.run()
    assert app.button(key="studio-audio-sample-" + cid)
    assert not any(b.key == "retry-asset-character_references:" + cid for b in app.button)
    draft = f"studio-draft-sample-{world.project_id}-{cid}"
    app.text_area(key=draft).set_value("Keep this unsaved speech draft.").run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    assert not any(b.key == "studio-audio-sample-" + cid for b in app.button)
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Character References"
    app.run()
    assert app.text_area(key=draft).value == "Keep this unsaved speech draft."
    assert not app.exception


def test_background_collapse_skips_generation_and_keeps_draft_settings(world, monkeypatch):
    world.generate_characters()
    app = app_for(world, monkeypatch)
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    lid = locations(engine.creative)[0].location_id
    button_key = "generate-location-pair-"+lid+"-masters"
    assert not any(b.key == button_key for b in app.button)
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    assert app.button(key=button_key)
    key = f"location-reference-set-{world.project_id}-{lid}-floor"
    app.text_area(key=key).set_value("Unsaved floor-plan draft.").run()
    app.session_state["background-card-" + world.project_id + "-" + lid] = False
    app.run()
    assert not any(b.key == button_key for b in app.button)
    app.session_state["background-card-" + world.project_id + "-" + lid] = True
    app.run()
    assert app.text_area(key=key).value == "Unsaved floor-plan draft."
    assert not app.exception


def test_subject_invalidation_isolated_from_other_characters(world, monkeypatch):
    from story_channel.asset_view_ui import subject_signature
    import story_channel.asset_view_ui as views
    from types import SimpleNamespace
    world.generate_characters()
    world.generate_characters("Guest")
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    first, second = [c.character_id for c in world.pipeline.artifact("characters").characters]
    engine.inspect()
    monkeypatch.setattr(views, "st", SimpleNamespace(session_state={}))
    monkeypatch.setattr("story_channel.ui_tasks.workspace_tasks", lambda: [])
    monkeypatch.setattr("story_channel.ui_tasks.generation_busy", lambda: False)
    before_first = subject_signature(engine, "character_references", first)
    before_second = subject_signature(engine, "character_references", second)
    engine.set_style_prompt("character_references", first, "Only this character uses pencil lines")
    assert subject_signature(engine, "character_references", first) != before_first
    assert subject_signature(engine, "character_references", second) == before_second
