from concurrent.futures import Future
from pathlib import Path
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary, locations
from story_channel.library_media import LibraryMedia
from story_channel.identity import IdentityManager
from test_character_visual_costumes import image_bytes
from test_studio import world


def test_uploads_are_design_references_and_generated_sheets_remain_identity_anchors(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.set_character_image(cid, image_bytes())
    calls = []
    original = engine.image_provider.generate
    def generate(prompt, references, config):
        calls.append((prompt, list(references)))
        return original(prompt, references, config)
    engine.image_provider.generate = generate
    state = engine.generate("character_references", subject=cid)
    prompt, refs = calls[-1]
    assert "Preserve the reference character's" in prompt
    assert "STYLE GUIDE ONLY" not in prompt
    assert refs == [engine.character_image(state, cid)]
    assert state.records["character_references:" + cid].asset.generation_params["uploadedReferenceRole"] == "design_reference"

    library = BackgroundLibrary(engine)
    lid = locations(world.pipeline)[0].location_id
    library.set_image(lid, image_bytes("red"))
    library.configure_references(lid,floor_plan="Fixed room center",object_layout={"desk":"north wall"})
    from story_channel.floor_plan_guides import publish_guides
    guides = publish_guides(engine,lid,engine.load().location_reference_sets[lid])
    engine.generate("backgrounds",subject=lid)
    prompt, refs = calls[-1]
    assert "authoritative camera-region guide" in prompt
    assert "STYLE GUIDE ONLY" not in prompt
    assert refs[:2] == [engine.local_path(guides["views"]["west_panorama"]["cameraGuide"]),
                    engine.local_path(guides["views"]["west_panorama"]["visibleFloorPlan"])]
    assert all(path.parent.name == "overlap_anchors" and path.is_file() for path in refs[2:])

    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    costume = next(c for c in IdentityManager(world.manager, world.project_id).load().costumes.values() if c.character_id == cid)
    media.generate_costume(cid, costume.costume_id)
    assert "Use the supplied character reference sheet for face, hair, anatomy and proportions" in calls[-1][0]
    assert calls[-1][1] == [engine.local_path(state.records["character_references:" + cid].asset.file_path)]


def test_card_list_and_add_form_survive_catalog_registry_update_interval(world, monkeypatch):
    world.generate_characters()
    entered, release = Event(), Event()
    original = BackgroundLibrary._rebuild
    def slow_rebuild(self):
        entered.set()
        assert release.wait(20)
        return original(self)
    monkeypatch.setattr(BackgroundLibrary, "_rebuild", slow_rebuild)
    app = AppTest.from_string('''
import streamlit as st
from story_channel.assets import AssetPipeline
from story_channel.background_assets_ui import background_cards
from story_channel.ui_tasks import task_status
manager, pid = st.session_state["manager"], st.session_state["pid"]
st.session_state["workspace_page"] = "Assets"
st.session_state["generation-context-project"] = pid
engine = AssetPipeline(manager, pid, demo=True)
task_status(engine.path)
background_cards(engine)
''', default_timeout=20)
    app.session_state["manager"] = world.manager
    app.session_state["pid"] = world.project_id
    app.run()
    pid = world.project_id
    existing = locations(world.pipeline)[0]
    app.text_input(key="background-new-name-" + pid).set_value("Office")
    app.text_area(key="background-new-description-" + pid).set_value("Shelves along the west wall.")
    task = None
    try:
        next(button for button in app.button if button.label == "Add draft").click().run()
        task = app.session_state["workspace-task"]
        assert entered.wait(10)
        app.run()
        assert not app.exception
        assert app.button(key="generate-location-pair-" + existing.location_id+"-masters")
        assert any(title.value == "Add background" for title in app.subheader)
        assert app.text_input(key="background-new-name-" + pid)
        assert task.get("stable_background_project") == pid
        release.set()
        task["future"].result(timeout=15)
        app.run()
        assert not app.exception
        added = next(loc for loc in locations(world.pipeline) if loc.name == "Office")
        assert app.button(key="generate-location-pair-" + added.location_id+"-masters")
    finally:
        release.set()
        if task:
            task["future"].result(timeout=15)


def test_registry_mapping_race_uses_last_valid_tasks_instead_of_clearing_cards(world, monkeypatch):
    world.generate_characters()
    racing = [False]
    original = AssetPipeline.tasks
    def tasks(self, state):
        if racing[0]:
            raise KeyError("background_being_registered")
        return original(self, state)
    monkeypatch.setattr(AssetPipeline, "tasks", tasks)
    app = AppTest.from_string('''
import streamlit as st
from story_channel.assets import AssetPipeline
from story_channel.background_assets_ui import background_cards
background_cards(AssetPipeline(st.session_state["manager"], st.session_state["pid"], demo=True))
''', default_timeout=20)
    app.session_state["manager"] = world.manager
    app.session_state["pid"] = world.project_id
    app.run()
    future = Future()
    app.session_state["workspace-task"] = {"future": future, "stable_background_project": world.project_id,
                                           "stop_event": Event()}
    racing[0] = True
    app.run()
    assert not app.exception
    assert any(title.value == "Add background" for title in app.subheader)
    assert app.button(key="generate-location-pair-" + locations(world.pipeline)[0].location_id+"-masters")
    racing[0] = False
    future.set_result(None)


def test_modifying_a_character_keeps_uploaded_style_and_generated_identity_roles_distinct(world):
    from story_channel.media_providers import DemoImageProvider
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    cid = world.pipeline.artifact("characters").characters[0].character_id
    engine.set_character_image(cid, image_bytes())
    state = engine.generate("character_references", subject=cid)
    record = state.records["character_references:" + cid]
    calls = []
    provider = DemoImageProvider()
    original = provider.generate
    def generate(prompt, references, config):
        calls.append((prompt, list(references)))
        return original(prompt, references, config)
    provider.generate = generate
    engine.modification_image_provider = provider
    engine.modify_with_openai("character_references", cid, "Make the lighting warmer", expected_asset_id=record.asset.asset_id)
    prompt, refs = calls[0]
    assert "Modify the attached current image" in prompt
    assert "Make the lighting warmer" in prompt
    assert refs == [engine.local_path(record.asset.file_path)]


@pytest.mark.parametrize("done", [False, True])
def test_background_status_polling_does_not_rerun_the_app(world, done):
    app = AppTest.from_string('''
import streamlit as st
from pathlib import Path
from types import SimpleNamespace
import streamlit.runtime.scriptrunner as runner
from story_channel.ui_tasks import task_status
pid = st.session_state["pid"]
st.session_state["workspace_page"] = "Assets"
calls = []
original_context, original_rerun = runner.get_script_run_ctx, st.rerun
try:
    runner.get_script_run_ctx = lambda: SimpleNamespace(fragment_ids_this_run={"poll"})
    st.rerun = lambda **kwargs: calls.append(kwargs)
    task_status.__wrapped__(Path(st.session_state["path"]))
finally:
    runner.get_script_run_ctx, st.rerun = original_context, original_rerun
st.session_state["rerun_calls"] = calls
''')
    future = Future()
    if done:
        future.set_result(None)
    app.session_state["pid"] = world.project_id
    app.session_state["path"] = str(world.path)
    app.session_state["workspace-task"] = {"future": future, "stable_background_project": world.project_id}
    app.run()
    assert not app.exception and app.session_state["rerun_calls"] == []
