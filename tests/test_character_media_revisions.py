from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from asset_test_helpers import complete_character_parts
from story_channel.assets import AssetPipeline
from story_channel.assets_status import world_assets_status_rows
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.library_revisions import active_visual, sample_profile
from test_studio import world
from test_scene_production import episode
from test_ui import finish_background


@pytest.fixture
def cast(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    cid = world.pipeline.artifact("characters").characters[0].character_id
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    return engine, media, cid


def test_apply_costume_selects_older_file_without_rewriting_history_or_billing(cast):
    engine, media, cid = cast
    identity = IdentityManager(engine.manager, engine.project_id)
    costume = next(c for c in identity.load().costumes.values() if c.character_id == cid)
    key = "costume:" + costume.costume_id
    first = deepcopy(media.load()["visuals"][key][0])
    identity.save_costume(cid, costume.costume_id, "A blue coat", expected=costume.model_dump_json(by_alias=True))
    media.generate_costume(cid, costume.costume_id)
    engine.approve_character(cid)
    history = deepcopy(media.load()["visuals"][key])
    costs = len(CostTracker(engine.manager, engine.project_id).records())
    media.apply_costume_revision(cid, costume.costume_id, 1)
    saved = media.load()
    assert saved["visuals"][key] == history and active_visual(saved, key) == first
    assert len(CostTracker(engine.manager, engine.project_id).records()) == costs
    assert not engine.character_approved(cid, engine.inspect())
    assert engine.character_requirements(cid, engine.inspect())["Costumes"]
    row = next(r for r in world_assets_status_rows(engine, engine.inspect()) if r["Stage"] == "costumes")
    assert row["Revision"] == 1 and row["Status"] == "Model sheet generated"
    engine.approve_character(cid)
    media.generate_costume(cid, costume.costume_id)
    assert active_visual(media.load(), key)["revision"] == 3


def test_apply_voice_restores_profile_and_per_emotion_sample_requires_reapproval(cast):
    engine, media, cid = cast
    identity = IdentityManager(engine.manager, engine.project_id)
    original_profile = identity.load().voices[cid].model_copy(deep=True)
    original = deepcopy(media.load()["samples"][cid][0])
    media.talking_sample(cid, "A happy sample", "happy", video=False)
    happy = media.load()["samples"][cid][-1]
    media.talking_sample(cid, "A new baseline", video=False)
    media.approve_voice(cid)
    engine.approve_character(cid)
    costs = len(CostTracker(engine.manager, engine.project_id).records())
    media.apply_voice_revision(cid, original["revision"])
    chosen = {s["emotion"]: s for s in media.selected_samples(cid)}
    assert chosen["neutral"]["revision"] == original["revision"]
    assert chosen["happy"]["revision"] == happy["revision"]
    assert not media.voice_approved(cid) and not engine.character_approved(cid, engine.inspect())
    media.approve_voice(cid)
    engine.approve_character(cid)
    # Applying a revision from a previous provider Voice ID restores that profile.
    saved = identity.load()
    expected = saved.model_dump_json(by_alias=True)
    saved.voices[cid].voice_id = "alloy"
    identity.save(saved, expected=expected)
    media.talking_sample(cid, "Another voice", video=False)
    media.apply_voice_revision(cid, original["revision"])
    assert identity.load().voices[cid] == original_profile
    assert media.selected_samples(cid)[0]["revision"] == original["revision"]
    # Applying itself incurred no new generation records.
    assert len(CostTracker(engine.manager, engine.project_id).records()) == costs + 1
    assert media.load()["samples"][cid][0] == original


def test_missing_modified_or_wrong_provider_revisions_do_not_change_selection(cast):
    engine, media, cid = cast
    saved = media.load()
    first = saved["samples"][cid][0]
    key = next(iter(saved["visuals"]))
    image = active_visual(saved, key)
    engine.local_path(first["audio"]).write_bytes(b"changed")
    before = media.file.read_bytes()
    with pytest.raises(ValueError, match="missing or modified"):
        media.apply_voice_revision(cid, first["revision"])
    with pytest.raises(ValueError, match="missing or modified"):
        media.apply_costume_revision(cid, key.removeprefix("costume:"), 999)
    engine.local_path(image["asset"]["filePath"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="missing or modified"):
        media.apply_costume_revision(cid, key.removeprefix("costume:"), image["revision"])
    assert media.file.read_bytes() == before


def test_legacy_voice_profile_recovers_from_identity_history(cast):
    engine, media, cid = cast
    sample = deepcopy(media.load()["samples"][cid][0])
    sample.pop("profile")
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    original = library.voices[cid].model_copy(deep=True)
    library.voices[cid].voice_id = "alloy"
    identity.save(library, expected=expected)
    assert sample_profile(media, cid, sample) == original


def test_incompatible_provider_or_unrecoverable_profile_can_preview_but_not_apply(cast):
    engine, media, cid = cast
    state = media.load()
    state["samples"][cid][0]["audioAsset"]["provider"] = "other"
    media.save(state)
    before = media.file.read_bytes()
    with pytest.raises(ValueError, match="different provider/model"):
        media.apply_voice_revision(cid, 1)
    assert media.file.read_bytes() == before
    state["samples"][cid][0]["audioAsset"]["provider"] = "demo"
    state["samples"][cid][0].pop("profile")
    state["samples"][cid][0]["profileHash"] = "unrecoverable"
    media.save(state)
    before = media.file.read_bytes()
    with pytest.raises(ValueError, match="no recoverable voice profile"):
        media.apply_voice_revision(cid, 1)
    assert media.file.read_bytes() == before


def test_episode_snapshot_and_animation_use_applied_revisions(episode):
    from story_channel.animation import AnimationDirector
    from story_channel.studio import Episodes
    world, child, _ = episode
    engine = AssetPipeline(world.manager, world.project_id)
    media = LibraryMedia(world.manager, world.project_id)
    cid = next(iter(IdentityManager(world.manager, world.project_id).load().voices))
    costume_id = next(c.costume_id for c in IdentityManager(world.manager, world.project_id).load().costumes.values()
                      if c.character_id == cid)
    first_voice = deepcopy(media.selected_samples(cid)[0])
    media.talking_sample(cid, "Another baseline", video=False)
    media.generate_costume(cid, costume_id)
    media.apply_voice_revision(cid, first_voice["revision"])
    media.apply_costume_revision(cid, costume_id, 1)
    media.approve_voice(cid)
    engine.approve("characters")
    Episodes(world.manager, world.project_id).refresh_assets(child.project_id)
    copied = LibraryMedia(world.manager, child.project_id)
    assert active_visual(copied.load(), "costume:" + costume_id)["revision"] == 1
    assert copied.selected_samples(cid)[0]["revision"] == first_voice["revision"]
    AssetPipeline(world.manager, child.project_id).generate("scene_images")
    direction = AnimationDirector(world.manager, child.project_id).prepare()
    assert direction.previews[cid].asset.file_path == first_voice["audio"]
    assert direction.previews[cid].approval


def test_voice_and_costume_revision_ui_preview_then_apply(cast, monkeypatch):
    engine, media, cid = cast
    identity = IdentityManager(engine.manager, engine.project_id)
    library = identity.load()
    costume_id = next(iter(library.costumes))
    original_voice = library.voices[cid].voice_id
    expected = library.model_dump_json(by_alias=True)
    library.voices[cid].voice_id = "alloy"
    identity.save(library, expected=expected)
    media.talking_sample(cid, "New neutral", video=False)
    media.generate_costume(cid, costume_id)
    media.approve_voice(cid)
    engine.approve_character(cid)
    monkeypatch.setenv("STORY_DATA_ROOT", str(engine.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    vp = f"character-revisions-{engine.project_id}-voice-{cid}"
    cp = f"character-revisions-{engine.project_id}-costume-{costume_id}"
    app.session_state[f"character-library-card-{engine.project_id}-{cid}"] = True
    app.session_state[f"page-tab-character-{engine.project_id}-{cid}"] = "Voice"
    app.session_state[vp + "-panel"] = True
    app.run()
    app.selectbox(key=vp + "-choice").select(1).run()
    assert not app.exception
    assert media.selected_samples(cid)[0]["revision"] == 2
    assert active_visual(media.load(), "costume:" + costume_id)["revision"] == 2
    assert engine.character_approved(cid, engine.inspect())
    app.button(key=vp + "-apply").click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key=vp + "-apply").disabled
    assert not media.voice_approved(cid)
    assert app.text_input(key=f"animation-preview-voice-{engine.project_id}-{cid}").value == original_voice
    app.session_state[f"page-tab-character-{engine.project_id}-{cid}"] = "Costumes"
    app.session_state[f"character-costume-card-{engine.project_id}-{costume_id}"] = True
    app.session_state[cp + "-panel"] = True
    app.run()
    app.selectbox(key=cp + "-choice").select(1).run()
    assert active_visual(media.load(), "costume:" + costume_id)["revision"] == 2
    app.button(key=cp + "-apply").click().run()
    finish_background(app)
    assert not app.exception
    assert app.button(key=cp + "-apply").disabled
    assert active_visual(media.load(), "costume:" + costume_id)["revision"] == 1
    assert not engine.character_approved(cid, engine.inspect())
    app.button(key="nav-world").click().run()
    app.button(key="nav-assets").click().run()
    assert app.session_state[vp + "-choice"] == app.selectbox(key=cp + "-choice").value == 1
