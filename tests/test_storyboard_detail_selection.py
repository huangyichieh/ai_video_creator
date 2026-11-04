from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.background_library import BackgroundLibrary
from story_channel.location_details import add_draft
from story_channel.storyboard_editor import inventory
from test_location_details import png
from test_scene_production import episode
from test_studio import world


def add_details(episode):
    world,project,pipeline = episode
    engine = AssetPipeline(world.manager,world.project_id)
    lid = pipeline.artifact("storyboard").scenes[0].location_id
    add_draft(engine,lid,"draft_area","Draft area","Unfinished area")
    BackgroundLibrary(engine).add_detail_reference(lid,"Window area",png(),detail_id="window_area",description="Window close-up")
    return world,project,pipeline,lid


def test_storyboard_lists_details_and_saves_parent_and_detail_target(episode):
    world,project,pipeline,lid = add_details(episode)
    catalog = inventory(pipeline)
    key = lid+"__detail_window_area"
    assert key in catalog["detail_backgrounds"]
    assert key in catalog["available"]["backgrounds"]
    assert lid+"__detail_draft_area" not in catalog["available"]["backgrounds"]
    def display(pipeline):
        from story_channel.storyboard_editor_ui import render
        render(pipeline)
    app = AppTest.from_function(display,args=(pipeline,),default_timeout=25).run()
    assert not app.exception
    background = next(w for w in app.selectbox if w.label == "Background")
    assert any("window_area" in label and "Existing" in label for label in background.options)
    assert any("draft_area" in label and "pending" in label for label in background.options)
    background.select(key).run()
    assert not app.exception
    next(b for b in app.button if b.label == "Save storyboard changes").click().run()
    assert not app.exception
    board = pipeline.artifact("storyboard")
    assert board.scenes[0].location_id == lid
    assert board.scenes[0].target_area == "window_area"
    assert all("__detail_" not in item.location_id for item in board.backgrounds)
    assert next(w for w in app.selectbox if w.label == "Background").value == key


def test_animation_storyboard_only_offers_generated_detail_images(episode):
    world,project,pipeline,lid = add_details(episode)
    def display(manager,project_id):
        import streamlit as st
        from story_channel.animation_inputs_ui import input_stage
        st.session_state[f"animation-storyboard-{project_id}"] = True
        input_stage(manager,project_id,images=True)
    app = AppTest.from_function(display,args=(world.manager,project.project_id),default_timeout=25).run()
    assert not app.exception
    fields = [w for w in app.selectbox if w.label == "Background"]
    assert fields
    assert any("window_area" in label for label in fields[0].options)
    assert not any("draft_area" in label for label in fields[0].options)
