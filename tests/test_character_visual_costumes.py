from asset_test_helpers import complete_character_parts
import io
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.visual_generation import VisualGeneration
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.costs import CostTracker
from story_channel.files import input_hash
from test_studio import world
from test_ui import finish_background
from test_scene_production import episode
from story_channel.studio import Episodes


def image_bytes(color="blue"):
    output = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(output, "PNG")
    return output.getvalue()


def test_optional_character_reference_and_style_reach_batch_generation_and_only_invalidate_target(world):
    world.generate_characters()
    world.generate_characters("Guest")
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    first, second = [c.character_id for c in world.pipeline.artifact("characters").characters]
    assert engine.can_generate("character_references", engine.load())
    engine.generate("character_references")  # Both style and input image are optional.
    complete_character_parts(engine)
    state = engine.approve("characters")
    second_asset = state.records["character_references:" + second].asset.asset_id
    engine.set_character_image(first, image_bytes())
    state = VisualGeneration(engine).update("character_references", first, mode="image")
    reference = state.character_images[first]
    assert engine.character_image(state, first).read_bytes() == image_bytes()
    assert not engine.character_approved(first, state) and engine.character_approved(second, state)
    engine.set_style_prompt("character_references", first, "Watercolor and warm light")
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, references, config):
        calls.append((prompt, list(references), config))
        return original(prompt, references, config)
    engine.image_provider.generate = capture
    state = engine.generate("character_references")
    assert len(calls) == 1
    assert "Watercolor and warm light" not in calls[0][0]
    assert "model sheet" in calls[0][0] and "back" in calls[0][0]
    assert calls[0][1][-1] == engine.local_path(reference["file"])
    assert state.records["character_references:" + first].asset.generation_params["characterImage"] == reference
    assert state.records["character_references:" + second].asset.asset_id == second_asset
    assert AssetPipeline(world.manager, world.project_id).load().character_images[first] == reference
    before = engine.state_path.read_bytes()
    with pytest.raises(ValueError, match="valid image"):
        engine.set_character_image(first, b"invalid")
    assert engine.state_path.read_bytes() == before
    engine.local_path(reference["file"]).write_bytes(b"modified")
    assert engine.inspect().records["character_references:" + first].status == "stale"
    cost_count = len(CostTracker(world.manager, world.project_id).records())
    with pytest.raises(ValueError, match="missing or modified"):
        engine.generate("character_references", subject=first)
    assert len(calls) == 1 and len(CostTracker(world.manager, world.project_id).records()) == cost_count
    state = engine.set_character_image(first, None)
    assert first not in state.character_images


def test_costumes_add_edit_and_generate_model_sheets_without_regenerating_base_character(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.generate("character_references")
    complete_character_parts(engine)
    state = engine.approve("characters")
    base_id = state.records["character_references:" + cid].asset.asset_id
    identity = IdentityManager(world.manager, world.project_id)
    costume = identity.save_costume(cid, "raincoat", "Red raincoat with black boots")
    assert not engine.character_approved(cid, engine.inspect())
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, references, config):
        calls.append((prompt, list(references)))
        return original(prompt, references, config)
    engine.image_provider.generate = capture
    media.generate_costume(cid, costume.costume_id, expected=costume.model_dump_json(by_alias=True))
    version = media.load()["visuals"]["costume:raincoat"][-1]
    assert version["sourceHash"] == media.costume_signature(cid, "raincoat")
    assert "COSTUME MODEL SHEET" in calls[0][0] and "back" in calls[0][0]
    assert "Red raincoat" in calls[0][0] and calls[0][1]
    updated = identity.save_costume(cid, "raincoat", "Yellow raincoat with black boots", expected=costume.model_dump_json(by_alias=True))
    assert version["sourceHash"] != media.costume_signature(cid, "raincoat")
    with pytest.raises(ValueError, match="changed"):
        media.generate_costume(cid, "raincoat", expected=costume.model_dump_json(by_alias=True))
    media.generate_costume(cid, "raincoat", expected=updated.model_dump_json(by_alias=True))
    assert len(media.load()["visuals"]["costume:raincoat"]) == 2
    assert "Yellow raincoat" in calls[-1][0]
    assert engine.load().records["character_references:" + cid].asset.asset_id == base_id
    assert not engine.character_approved(cid, engine.inspect())
    engine.approve_character(cid)
    assert engine.character_approved(cid, engine.inspect())
    assert CostTracker(world.manager, world.project_id).records()[-1].step == "library:image:costume:raincoat"
    with pytest.raises(ValueError, match="already exists"):
        identity.save_costume(cid, "raincoat", "Another outfit")
    with pytest.raises(ValueError, match="changed"):
        identity.save_costume(cid, "raincoat", "Stale edit", expected=costume.model_dump_json(by_alias=True))


def test_costume_ui_add_edit_revert_and_generate_visual(world, monkeypatch):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.generate("character_references")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    assert app.button(key="retry-asset-character_references:" + cid).label == "Generate"
    prefix = f"asset-character-{world.project_id}-{cid}"
    app.session_state[prefix + "-details-panel"] = True
    app.run()
    app.toggle(key=prefix + "-edit-mode").set_value(True).run()
    app.button(key=f"visual-generation-{world.project_id}-character_references-{cid}-image").click().run()
    assert any(field.proto.label == "Design reference image" for field in app.get("file_uploader"))
    app.button(key=f"visual-generation-{world.project_id}-character_references-{cid}-text").click().run()
    app.session_state[f"page-tab-character-{world.project_id}-{cid}"] = "Costumes"
    app.run()
    prefix = f"studio-draft-new-costume-{world.project_id}-{cid}-"
    app.text_input(key=prefix + "id").set_value("raincoat")
    app.text_area(key=prefix + "description").set_value("Red raincoat")
    app.button(key="add-costume-" + cid).click().run()
    finish_background(app)
    assert not app.exception
    key = f"studio-draft-costume-{world.project_id}-raincoat-{input_hash('Red raincoat')[:10]}"
    app.session_state[f"character-costume-card-{world.project_id}-raincoat"] = True
    app.run()
    app.text_area(key=key).set_value("Yellow raincoat").run()
    assert app.button(key="studio-costume-raincoat").disabled
    app.button(key="revert-costume-raincoat").click().run()
    assert app.text_area(key=key).value == "Red raincoat"
    app.text_area(key=key).set_value("Yellow raincoat").run()
    app.button(key="save-costume-raincoat").click().run()
    finish_background(app)
    assert not app.exception
    assert IdentityManager(world.manager, world.project_id).load().costumes["raincoat"].description == "Yellow raincoat"
    app.button(key="studio-costume-raincoat").click().run()
    finish_background(app)
    assert not app.exception
    assert LibraryMedia(world.manager, world.project_id).load()["visuals"]["costume:raincoat"][-1]["revision"] == 1


def test_new_costumes_and_input_images_refresh_into_episode_without_breaking_shared_approvals(episode):
    world, episode, pipeline = episode
    manager = world.manager
    root = AssetPipeline(manager, world.project_id)
    child = AssetPipeline(manager, episode.project_id)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    initial_asset = child.load().records["character_references:" + cid].asset.asset_id
    IdentityManager(manager, world.project_id).save_costume(cid, "raincoat", "Red raincoat")
    state = root.set_character_image(cid, image_bytes())
    reference = state.character_images[cid]
    root.generate("character_references", subject=cid)
    complete_character_parts(root)
    root.approve("characters")
    root.approve("backgrounds")
    assert child.load().records["character_references:" + cid].asset.asset_id == initial_asset
    assert "raincoat" not in IdentityManager(manager, episode.project_id).load().costumes
    Episodes(manager, world.project_id).refresh_assets(episode.project_id)
    state = child.inspect()
    assert state.records["character_references:" + cid].status == "succeeded"
    assert child.character_approved(cid, state)
    assert "raincoat" in IdentityManager(manager, episode.project_id).load().costumes
    assert child.character_image(state, cid).read_bytes() == image_bytes()
    assert state.character_images[cid] == reference
    assert manager.load(episode.project_id).shared_assets_hash
