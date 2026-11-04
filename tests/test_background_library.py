from asset_test_helpers import complete_character_parts
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.assets_status import world_assets_status_rows
from story_channel.background_library import BackgroundLibrary, locations
from story_channel.visual_generation import VisualGeneration
from story_channel.files import atomic_write
from story_channel.identity import IdentityManager
from story_channel.studio import Episodes
from test_studio import world
from test_ui import finish_background


def test_generate_background_without_characters_or_shared_style(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    lid = locations(world.pipeline)[0].location_id
    assert engine.can_generate("backgrounds", engine.load())
    subjects = [lid + "__panorama"]
    state = engine.generate("backgrounds", subjects=subjects)
    assert all(state.records["backgrounds:" + s].status == "succeeded" for s in subjects)
    assert not (world.path / "characters.json").exists()
    world.generate_characters()
    state = engine.inspect()
    assert all(state.records["backgrounds:" + s].status == "succeeded" for s in subjects)


def test_background_reference_reaches_four_views_and_invalidates_only_its_background(world):
    from test_character_visual_costumes import image_bytes
    from story_channel.costs import CostTracker
    engine, library = ready(world)
    first = locations(world.pipeline)[0].location_id
    second = library.add("Garden", "A walled garden.").location_id
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    library.set_image(first, image_bytes())
    state = VisualGeneration(engine).update("backgrounds", first, mode="image")
    assert not library.approved(first, state) and library.approved(second, state)
    reference = state.background_images[first]
    library.set_prompt(first, "Warm sunset")
    calls = []
    original = engine.image_provider.generate
    def capture(prompt, references, config):
        calls.append((prompt, list(references)))
        return original(prompt, references, config)
    engine.image_provider.generate = capture
    state = engine.generate("backgrounds")
    assert len(calls) == 1  # One panorama request; no derived images.
    assert all(engine.local_path(reference["file"]) in refs and "Warm sunset" not in prompt for prompt, refs in calls)
    assert all(record.asset.generation_params["backgroundImage"] == reference for record in state.records.values()
               if record.stage == "backgrounds" and (record.subject == first or record.subject.startswith(first + "__")))
    before = engine.state_path.read_bytes()
    with pytest.raises(ValueError, match="valid image"):
        library.set_image(first, b"invalid")
    assert engine.state_path.read_bytes() == before
    engine.local_path(reference["file"]).write_bytes(b"modified")
    costs = len(CostTracker(engine.manager, engine.project_id).records())
    with pytest.raises(ValueError, match="missing or modified"):
        engine.generate("backgrounds")
    assert len(calls) == 1
    assert len(CostTracker(engine.manager, engine.project_id).records()) == costs
    assert first not in library.set_image(first, None).background_images


def test_background_generate_enabled_in_ui_before_characters(world, monkeypatch):
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    lid = locations(world.pipeline)[0].location_id
    assert not app.button(key="generate-background-" + lid).disabled
    assert any(field.label == "Runway API prompt" for field in app.text_area)
    app.button(key="generate-background-" + lid).click().run()
    finish_background(app)
    assert not app.exception
    assert not app.error
    assert len(AssetPipeline(world.manager, world.project_id).load().records) == 1


def ready(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    engine.set_generic_prompt("Watercolor")
    engine.generate("character_references")
    complete_character_parts(engine)
    engine.approve("characters")
    return engine, BackgroundLibrary(engine)


@pytest.mark.parametrize("added",[False,True])
def test_edit_background_description_preserves_canon_snapshots_and_other_asset_details(world,added):
    engine,library = ready(world)
    location = library.add("Office","Original office description",reference_type="ONE_TIME") if added else locations(world.pipeline)[0]
    identity = IdentityManager(world.manager,world.project_id)
    before = identity.load()
    expected = before.model_dump_json(by_alias=True)
    next(iter(before.costumes.values())).description = "Keep this custom costume"
    identity.save(before,expected=expected)
    before = identity.load()
    episode = Episodes(world.manager,world.project_id).create("Frozen episode","Original locations",60)
    child = AssetPipeline(world.manager,episode.project_id,demo=True)
    original_canon = (world.path/"world.json").read_bytes()
    library.edit_description(location.location_id,"Updated office description",expected=location.description)
    assert next(loc for loc in locations(world.pipeline) if loc.location_id == location.location_id).description == "Updated office description"
    assert next(loc for loc in locations(child.creative) if loc.location_id == location.location_id).description == location.description
    assert (world.path/"world.json").read_bytes() == original_canon
    after = identity.load()
    assert after.locations[location.location_id].description == "Updated office description"
    assert after.voices == before.voices and after.costumes == before.costumes
    saved = (world.path/"background_library.json").read_bytes()
    with pytest.raises(ValueError,match="another session"):
        library.edit_description(location.location_id,"Stale overwrite",expected=location.description)
    with pytest.raises(ValueError,match="empty"):
        library.edit_description(location.location_id,"  ",expected="Updated office description")
    assert (world.path/"background_library.json").read_bytes() == saved


def test_background_description_editor_save_revert_and_navigation(world,monkeypatch):
    engine = AssetPipeline(world.manager,world.project_id,demo=True)
    location = locations(world.pipeline)[0]
    for key,value in {"STORY_DATA_ROOT":str(world.manager.root),"STORY_LLM_PROVIDER":"demo",
                      "STORY_ASSET_PROVIDER":"demo","OPENAI_ADMIN_KEY":""}.items():
        monkeypatch.setenv(key,value)
    app = AppTest.from_file(str(Path(__file__).parents[1]/"app.py"),default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    prefix = f"background-description-{world.project_id}-{location.location_id}-"
    field = next(field for field in app.text_area if field.key.startswith(prefix))
    field.input("My edited background description").run()
    app.button(key="nav-world").click().run()
    app.button(key="nav-assets").click().run()
    assert next(field for field in app.text_area if field.key.startswith(prefix)).value == "My edited background description"
    app.button(key="revert-background-description-"+location.location_id).click().run()
    assert next(field for field in app.text_area if field.key.startswith(prefix)).value == location.description
    next(field for field in app.text_area if field.key.startswith(prefix)).input("Saved background description").run()
    app.button(key="save-background-description-"+location.location_id).click().run()
    if "workspace-task" in app.session_state:
        app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception
    assert next(loc for loc in locations(world.pipeline) if loc.location_id == location.location_id).description == "Saved background description"
    assert next(field for field in app.text_area if field.key.startswith(prefix)).value == "Saved background description"


def test_background_drafts_preserve_canon_and_can_exist_before_characters(world):
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    library = BackgroundLibrary(engine)
    original = (world.path / "world.json").read_bytes()
    with pytest.raises(ValueError):
        library.add("", "An empty name")
    added = library.add("Office", "A bright office overlooking the harbour.")
    assert added in locations(world.pipeline)
    assert (world.path / "world.json").read_bytes() == original
    world.require_approved()
    library.remove(added.location_id)
    assert added not in locations(world.pipeline)
    world.require_approved()


def test_one_generation_four_views_individual_and_bulk_approval_invalidation(world):
    engine, library = ready(world)
    first = locations(world.pipeline)[0].location_id
    added = library.add("Office", "A bright seaside office.")
    second = added.location_id
    subjects = [first + "__panorama"]
    with pytest.raises(ValueError, match="current panorama"):
        library.approve(first)
    state = engine.generate("backgrounds", subjects=subjects, force=True)
    assert all(state.records["backgrounds:" + s].status == "succeeded" for s in subjects)
    assert not state.records.get("backgrounds:" + second)
    state = library.approve(first)
    stamp = state.background_approvals[first]
    assert library.approved(first, state)
    assert "backgrounds" not in world.manager.load(world.project_id).asset_approvals
    engine.generate("backgrounds")
    state = engine.approve("backgrounds")
    assert state.background_approvals[first] == stamp
    assert library.approved(second, state)
    assert "backgrounds" in world.manager.load(world.project_id).asset_approvals
    VisualGeneration(engine).update("backgrounds", second, mode="text", prompt="Warm sunset panorama")
    state = engine.inspect()
    assert library.approved(first, state)
    assert not library.approved(second, state)
    rows = world_assets_status_rows(engine, state)
    assert next(r for r in rows if r["Stage"] == "backgrounds" and r["Subject"] == second + "__panorama")["Approval"] == "Pending"
    engine.generate("backgrounds")
    state = library.approve(second)
    record = state.records["backgrounds:" + second + "__panorama"]
    state = library.apply_panorama_revision(second, record.asset.asset_id)
    assert not library.approved(second, state)
    assert library.approved(first, state)


def test_removal_protects_used_approved_background_and_retains_media(world):
    engine, library = ready(world)
    added = library.add("Office", "A bright seaside office.")
    engine.generate("backgrounds")
    state = engine.approve("backgrounds")
    file = engine.local_path(state.records["backgrounds:" + added.location_id + "__panorama"].asset.file_path)
    atomic_write(world.path / "story.json", '{"locationId": "' + added.location_id + '"}')
    with pytest.raises(ValueError, match="used"):
        library.remove(added.location_id)
    (world.path / "story.json").unlink()
    library.remove(added.location_id)
    assert file.exists()
    assert added not in locations(world.pipeline)
    identity = IdentityManager(world.manager, world.project_id)
    identity.validate(identity.load())
    world.require_approved()
    assert list((world.path / "history" / "removed_backgrounds").glob("*.json"))


def test_episode_snapshots_keep_background_drafts_and_generation_context(world):
    engine, library = ready(world)
    added = library.add("Office", "A bright seaside office.")
    from test_character_visual_costumes import image_bytes
    library.set_image(added.location_id, image_bytes())
    engine.generate("backgrounds")
    engine.approve("backgrounds")
    episodes = Episodes(world.manager, world.project_id)
    episode = episodes.create("A keeper tale", "A keeper protects the beacon.", 60)
    from story_channel.pipeline import Pipeline
    child = Pipeline(world.manager, episode.project_id)
    assert added.location_id in {loc.location_id for loc in locations(child)}
    assert added.location_id in {loc["locationId"] for loc in child._context("story")["world"]["locations"]}
    identity = IdentityManager(world.manager, world.project_id)
    settings = identity.load()
    expected = settings.model_dump_json(by_alias=True)
    for profile in settings.voices.values():
        profile.voice_id = "nova"
    identity.save(settings, expected=expected)
    from story_channel.library_media import LibraryMedia
    media = LibraryMedia(world.manager, world.project_id)
    for cid in settings.voices:
        if media.voice_approved(cid):
            continue
        media.talking_sample(cid, "Keep the beacon burning.", video=False)
        media.approve_voice(cid)
    episodes.import_assets(episode.project_id)
    child_engine = AssetPipeline(world.manager, episode.project_id)
    assert BackgroundLibrary(child_engine).image(child_engine.load(), added.location_id).read_bytes() == image_bytes()
    library.remove(added.location_id)
    assert added.location_id not in {loc.location_id for loc in locations(world.pipeline)}
    assert added.location_id in {loc.location_id for loc in locations(child)}
    IdentityManager(world.manager, episode.project_id).validate(IdentityManager(world.manager, episode.project_id).load())


def test_background_ui_single_generation_and_approval(world, monkeypatch):
    engine, library = ready(world)
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-assets-{world.project_id}"] = "Background"
    app.run()
    assert not app.exception
    lid = locations(world.pipeline)[0].location_id
    assert app.button(key="studio-approve-backgrounds").label == "Approve all backgrounds"
    assert len([b for b in app.button if b.key and b.key.startswith("generate-background-")]) == len(locations(world.pipeline))
    assert not any(e.label == "Prompt" for e in app.expander)
    assert app.button(key="approve-background-" + lid).disabled
    app.button(key="generate-background-" + lid).click().run()
    finish_background(app)
    assert not app.exception
    app.button(key="approve-background-" + lid).click().run()
    finish_background(app)
    assert not app.exception
    app.session_state["background-card-" + lid] = True
    app.run()
    assert app.button(key="approve-background-" + lid).label == "Approved"
    assert app.button(key="approve-background-" + lid).disabled
    app.text_input(key="background-new-name-" + world.project_id).set_value("Garden")
    next(field for field in app.text_area if field.label == "Description").set_value("A small walled garden.")
    next(button for button in app.button if button.label == "Add draft").click().run()
    finish_background(app)
    assert not app.exception
    added = next(loc for loc in locations(world.pipeline) if loc.name == "Garden")
    assert app.button(key="approve-background-" + lid).disabled
    assert app.button(key="approve-background-" + added.location_id).disabled
    app.button(key="remove-background-" + added.location_id).click().run()
    assert not app.exception
    app.button(key="confirm-remove-background").click().run()
    assert not app.exception
    assert not app.error, [error.value for error in app.error]
    assert added not in locations(world.pipeline)
