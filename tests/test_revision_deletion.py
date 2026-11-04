from copy import deepcopy

import pytest

from asset_test_helpers import complete_character_parts
from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary, locations
from story_channel.character_visual_batch import generate_all_visuals_text
from story_channel.files import input_hash
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.library_revisions import active_visual
from story_channel.visual_generation import VisualGeneration
from test_character_visual_costumes import image_bytes
from test_studio import world


def setup(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    return engine, cid


def test_bulk_text_forces_all_characters_ignores_design_images_and_preserves_prompts(world):
    world.generate_characters()
    world.generate_characters("Guest")
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    ids = [c.character_id for c in world.pipeline.artifact("characters").characters]
    for cid in ids:
        engine.set_character_image(cid, image_bytes())
        VisualGeneration(engine).update("character_references", cid, mode="image", prompt="Text model sheet for " + cid)
    engine.generate("character_references")
    calls = []
    original = engine.image_provider.generate
    def generate(prompt, refs, config):
        calls.append((prompt, refs))
        return original(prompt, refs, config)
    engine.image_provider.generate = generate
    state = generate_all_visuals_text(engine)
    assert len(calls) == len(ids) and all(not refs for _, refs in calls)
    assert all(any("Text model sheet for " + cid in prompt for prompt, _ in calls) for cid in ids)
    assert all(state.records["character_references:" + cid].revision == 2 for cid in ids)
    assert all(state.generation_inputs["character_references:" + cid]["mode"] == "text" for cid in ids)
    assert all(cid in state.character_images for cid in ids)


def test_image_delete_renumbers_and_preserves_approved_active_content(world):
    engine, cid = setup(world)
    for _ in range(4):
        engine.generate("character_references", subject=cid, force=True)
    old = {r.revision: deepcopy(r) for r in engine.revisions("character_references", cid)}
    complete_character_parts(engine)
    engine.approve("characters")
    state = engine.delete_revision("character_references", cid, old[2].asset.asset_id)
    history = sorted(engine.revisions("character_references", cid), key=lambda r: r.revision)
    assert [r.revision for r in history] == [1, 2, 3]
    assert [r.asset.asset_id for r in history] == [old[n].asset.asset_id for n in (1, 3, 4)]
    assert state.records["character_references:" + cid].revision == 3
    assert engine.character_approved(cid, state)
    assert engine.local_path(old[2].asset.file_path).exists()
    engine.generate("character_references", subject=cid, force=True)
    assert engine.load().records["character_references:" + cid].revision == 4


def test_delete_active_and_last_image_requires_explicit_replacement(world):
    engine, cid = setup(world)
    engine.generate("character_references")
    first = engine.load().records["character_references:" + cid].asset.asset_id
    engine.generate("character_references", subject=cid, force=True)
    current = engine.load().records["character_references:" + cid].asset.asset_id
    state = engine.delete_revision("character_references", cid, current)
    assert state.records["character_references:" + cid].asset is None
    assert not engine.character_requirements(cid, state)["Visual"]
    assert [r.revision for r in engine.revisions("character_references", cid)] == [1]
    engine.apply_revision("character_references", cid, first)
    engine.delete_revision("character_references", cid, first)
    assert not engine.revisions("character_references", cid)
    engine.generate("character_references", subject=cid, force=True)
    assert engine.load().records["character_references:" + cid].revision == 1


def test_voice_and_costume_deletion_remaps_active_selections_and_rejects_stale_targets(world):
    engine, cid = setup(world)
    engine.generate("character_references")
    complete_character_parts(engine)
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    costume = next(c.costume_id for c in IdentityManager(world.manager, world.project_id).load().costumes.values() if c.character_id == cid)
    for i in range(3):
        media.talking_sample(cid, "Neutral sample " + str(i), video=False)
        media.generate_costume(cid, costume)
    media.approve_voice(cid)
    engine.approve("characters")
    old = deepcopy(media.load()["samples"][cid][1])
    media.delete_voice_revision(cid, 2, expected=input_hash(old))
    assert [s["revision"] for s in media.load()["samples"][cid]] == [1, 2, 3]
    assert media.selected_samples(cid)[0]["revision"] == 3
    assert media.voice_approved(cid) and engine.character_approved(cid, engine.load())
    with pytest.raises(ValueError, match="list changed"):
        media.delete_voice_revision(cid, 2, expected=input_hash(old))
    media.delete_costume_revision(cid, costume, 2)
    assert [v["revision"] for v in media.load()["visuals"]["costume:" + costume]] == [1, 2, 3]
    assert active_visual(media.load(), "costume:" + costume)["revision"] == 3
    assert engine.character_approved(cid, engine.load())
    media.delete_voice_revision(cid, 3)
    assert not media.selected_samples(cid) and not media.voice_approved(cid)
    assert not engine.character_approved(cid, engine.load())
    media.delete_costume_revision(cid, costume, 3)
    assert active_visual(media.load(), "costume:" + costume) is None


def test_inflight_voice_update_uses_stable_identity_after_renumber_and_cannot_restore_deleted_sample(world):
    engine, cid = setup(world)
    engine.generate("character_references")
    complete_character_parts(engine)
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    media.talking_sample(cid, "Second sample", video=False)
    media.talking_sample(cid, "Third sample", video=False)
    pending = deepcopy(media.load()["samples"][cid][-1])
    media.delete_voice_revision(cid, 1)
    pending["status"] = "failed"
    pending["error"] = "simulated late video failure"
    media._save_sample(cid, pending)
    assert pending["revision"] == 2
    assert media.load()["samples"][cid][-1]["error"] == "simulated late video failure"
    media.delete_voice_revision(cid, 2)
    with pytest.raises(ValueError, match="was deleted"):
        media._save_sample(cid, pending)
    assert [s["revision"] for s in media.load()["samples"][cid]] == [1]


def test_background_revision_delete_updates_active_number_and_approval(world):
    engine, cid = setup(world)
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    lid = locations(engine.creative)[0].location_id
    for _ in range(3):
        engine.generate("backgrounds", subject=lid, force=True)
    subject = lid + "__panorama"
    old = {r.revision: r for r in engine.revisions("backgrounds", subject)}
    BackgroundLibrary(engine).approve(lid)
    state = engine.delete_revision("backgrounds", subject, old[2].asset.asset_id)
    assert state.records["backgrounds:" + subject].revision == 2
    assert BackgroundLibrary(engine).approved(lid, state)
    state = engine.delete_revision("backgrounds", subject, old[3].asset.asset_id)
    assert state.records["backgrounds:" + subject].asset is None
    assert not BackgroundLibrary(engine).approved(lid, state)


def test_revision_delete_ui_refreshes_selection_and_bulk_label(world, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    engine, cid = setup(world)
    for _ in range(4):
        engine.generate("character_references", subject=cid, force=True)
    revisions = {r.revision: r for r in engine.revisions("character_references", cid)}
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert app.button(key="studio-character-all").label == "Generate all Visual(Text)"
    prefix = f"reference-revisions-{engine.project_id}-character_references-{cid}"
    app.session_state[prefix + "-panel"] = True
    app.run()
    app.selectbox(key=prefix + "-choice").select(revisions[2].asset.asset_id).run()
    app.button(key=prefix + "-delete").click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=15)
    app.run()
    assert not app.exception
    assert revisions[2].asset.asset_id not in app.selectbox(key=prefix + "-choice").value
    assert [r.revision for r in engine.revisions("character_references", cid)] == [3, 2, 1]
    assert app.selectbox(key=prefix + "-choice").value in {r.asset.asset_id for r in engine.revisions("character_references", cid)}


def test_voice_costume_delete_buttons_refresh_numbered_choices(world, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    engine, cid = setup(world)
    engine.generate("character_references")
    complete_character_parts(engine)
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    costume = next(c.costume_id for c in IdentityManager(world.manager, world.project_id).load().costumes.values() if c.character_id == cid)
    for i in range(2):
        media.talking_sample(cid, "Sample " + str(i), video=False)
        media.generate_costume(cid, costume)
    for key, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                       "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(key, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    tab = f"page-tab-character-{engine.project_id}-{cid}"
    for kind in ("voice", "costume"):
        prefix = f"character-revisions-{engine.project_id}-{kind}-{cid if kind == 'voice' else costume}"
        app.session_state[tab] = "Voice" if kind == "voice" else "Costumes"
        app.session_state[prefix + "-panel"] = True
        if kind == "costume":
            app.session_state[f"character-costume-card-{engine.project_id}-{costume}"] = True
        app.run()
        app.selectbox(key=prefix + "-choice").select(2).run()
        app.button(key=prefix + "-delete").click().run()
        if "workspace-task" in app.session_state:
            app.session_state["workspace-task"]["future"].result(timeout=15)
        app.run()
        assert not app.exception
        assert app.selectbox(key=prefix + "-choice").value in (1, 2)
    assert [s["revision"] for s in media.load()["samples"][cid]] == [1, 2]
    assert [v["revision"] for v in media.load()["visuals"]["costume:" + costume]] == [1, 2]
