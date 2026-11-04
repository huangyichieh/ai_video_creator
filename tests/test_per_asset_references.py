from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from asset_test_helpers import complete_character_parts
from story_channel.assets import AssetPipeline
from story_channel.assets_status import assets_status_rows, world_assets_status_rows
from story_channel.background_library import BackgroundLibrary
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.library_revisions import active_visual
from test_character_visual_costumes import image_bytes
from test_scene_production import episode
from test_studio import world


@pytest.fixture
def older_assets(world, monkeypatch):
    """Create the fingerprints and approvals used before global style removal."""
    import story_channel.reference_input_migration as migration
    with monkeypatch.context() as patch:
        patch.setattr(migration, "retire_shared_style", lambda *args: None)
        patch.setattr("story_channel.background_panorama.panorama_mode", lambda *args: False)
        world.generate_characters()
        engine = AssetPipeline(world.manager, world.project_id, demo=True)
        engine.set_generic_prompt("OLD_SHARED_STYLE")
        engine.set_style_reference(image_bytes("red"))
        engine.generate("character_references")
        complete_character_parts(engine)
        engine.approve("characters")
        engine.generate("backgrounds")
        engine.approve("backgrounds")
        older = engine.load()
        older.background_modes.clear()
        engine._save(older)
        assert not engine.load().per_asset_references
    return engine


def test_old_style_retirement_preserves_current_files_history_and_approvals(older_assets):
    engine = older_assets
    before = engine.load()
    approvals = engine.manager.load(engine.project_id).asset_approvals
    identities = {key: (r.asset.asset_id, r.revision, r.file_hash) for key, r in before.records.items()}
    history = {path: path.read_bytes() for path in (engine.path / "history" / "assets").rglob("*.json")}
    costs = len(CostTracker(engine.manager, engine.project_id).records())
    media = LibraryMedia(engine.manager, engine.project_id)
    costume_history = deepcopy(media.load()["visuals"])
    # The obsolete upload is no longer needed by inspect or later requests.
    engine.local_path(before.style_reference).unlink()
    state = engine.inspect()
    assert state.per_asset_references and not engine.shared_style_enabled(state)
    assert all(record.status == "succeeded" for record in state.records.values())
    assert identities == {key: (r.asset.asset_id, r.revision, r.file_hash) for key, r in state.records.items()}
    assert history == {path: path.read_bytes() for path in history}
    assert media.load()["visuals"] == costume_history
    assert len(CostTracker(engine.manager, engine.project_id).records()) == costs
    manifest = engine.manager.load(engine.project_id)
    assert {"characters", "backgrounds"} <= manifest.asset_approvals.keys()
    assert all(manifest.asset_approvals[g].approved_at == a.approved_at for g, a in approvals.items())
    assert all(engine.character_approved(cid, state) for cid in state.character_approvals)
    assert all(BackgroundLibrary(engine).approved(lid, state) for lid in state.background_approvals)
    cid = next(iter(IdentityManager(engine.manager, engine.project_id).load().voices))
    assert engine.character_requirements(cid, state) == {"Visual": True, "Voice": True, "Costumes": True}
    digest = state.model_dump(mode="json", exclude={"updated_at"})
    assert engine.inspect().model_dump(mode="json", exclude={"updated_at"}) == digest


def test_retirement_does_not_approve_changed_character_inputs(older_assets):
    engine = older_assets
    cid = next(iter(IdentityManager(engine.manager, engine.project_id).load().voices))
    # Simulate an edit saved without an intervening inspection.
    state = engine.load()
    state.generation_inputs["character_references:" + cid]["prompt"] = "New character prompt"
    engine._save(state)
    state = engine.inspect()
    assert state.records["character_references:" + cid].status == "stale"
    assert not engine.character_approved(cid, state)
    assert "characters" not in engine.manager.load(engine.project_id).asset_approvals


def test_new_requests_use_only_individual_prompt_and_reference(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.set_generic_prompt("OBSOLETE_GLOBAL_PROMPT")
    engine.set_style_reference(image_bytes("red"))
    reference = engine.set_character_image(cid, image_bytes("blue")).character_images[cid]
    engine.set_style_prompt("character_references", cid, "Individual watercolor")
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, references, config):
        calls.append((prompt, references, config))
        return original(prompt, references, config)
    engine.image_provider.generate = capture
    state = engine.generate("character_references", subject=cid)
    prompt, refs, config = calls[-1]
    assert "OBSOLETE_GLOBAL_PROMPT" not in prompt and "Individual watercolor" not in prompt
    assert refs == [engine.local_path(reference["file"])]
    assert "generic_prompt" not in config.options and "style_reference_hash" not in config.options
    params = state.records["character_references:" + cid].asset.generation_params
    assert params["styleReferences"] == [] and params["genericPrompt"] == ""
    assert not any(row["Stage"] == "style_references" for row in world_assets_status_rows(engine, state))
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    costume = next(iter(IdentityManager(world.manager, world.project_id).load().costumes.values()))
    media.generate_costume(cid, costume.costume_id)
    assert "OBSOLETE_GLOBAL_PROMPT" not in calls[-1][0]
    assert calls[-1][1] == [engine.local_path(state.records["character_references:" + cid].asset.file_path)]


def test_scene_snapshots_generate_without_shared_style_using_approved_assets(episode):
    world, child, _ = episode
    engine = AssetPipeline(world.manager, child.project_id)
    state = engine.inspect()
    assert state.per_asset_references and not state.generic_prompt and not state.style_references
    assert engine.can_generate("scene_images", state)
    calls = {}
    original = engine.image_provider.generate
    def capture(prompt, references, config):
        calls[Path(config.options["output_path"]).parent.name] = references
        return original(prompt, references, config)
    engine.image_provider.generate = capture
    state = engine.generate("scene_images")
    board = engine.creative.artifact("storyboard").scenes
    assert len(calls) == len(board)
    for scene in board:
        expected = {engine.local_path(state.records["character_references:" + cid].asset.file_path) for cid in scene.character_ids}
        expected |= {engine.local_path(state.records["backgrounds:" + scene.location_id + suffix].asset.file_path)
                     for suffix in ("__panorama",)}
        assert set(calls[scene.scene_id]) == expected
    assert not any(row["Stage"] == "style_references" for row in assets_status_rows(engine, state))


def test_assets_ui_has_individual_references_without_global_style_controls(world, monkeypatch):
    world.generate_characters()
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert not any(field.label == "Shared style prompt" for field in app.text_area)
    assert not any(panel.label == "Style references (up to 3 images)" for panel in app.expander)
    assert not any(button.label in ("Save style references", "Remove style references") for button in app.button)
    assert any(field.label == "Runway API prompt" for field in app.text_area)
    assert any(button.label == "Switch to image prompt" for button in app.button)
