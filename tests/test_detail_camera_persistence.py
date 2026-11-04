from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.location_details import add_draft,save_camera,saved_camera,remove
from test_location_master_pack import master_pack
from test_studio import world


def test_camera_is_durable_before_generation_and_scoped_to_detail(master_pack):
    engine,lid,provider,_ = master_pack
    add_draft(engine,lid,"desk_area","Desk area")
    add_draft(engine,lid,"door_area","Door area")
    setup = {"position":{"x":1,"y":-1},"yaw":135}
    camera = save_camera(engine,lid,"desk_area",setup,85)
    restored = AssetPipeline(engine.manager,engine.project_id,demo=True)
    assert saved_camera(restored,lid,"desk_area") == camera
    assert saved_camera(restored,lid,"door_area") is None
    assert not provider.calls
    assert restored.load().location_reference_sets[lid]["detail_references"]["desk_area"]["status"] == "draft"
    with pytest.raises(ValueError):
        save_camera(engine,lid,"desk_area",{"position":{"x":999,"y":0},"yaw":90},70)
    assert saved_camera(restored,lid,"desk_area") == camera
    remove(engine,lid,"desk_area",confirmation="desk_area")
    assert saved_camera(restored,lid,"desk_area") is None


def test_ui_restores_last_saved_camera_in_fresh_session_and_saves_fov(master_pack,monkeypatch):
    engine,lid,provider,_ = master_pack
    add_draft(engine,lid,"desk_area","Desk area")
    setup = {"position":{"x":1,"y":-1},"yaw":135}
    save_camera(engine,lid,"desk_area",setup,85)
    received = []
    callbacks = []
    def picker(**kwargs):
        received.append(kwargs["data"])
        callbacks.append((kwargs["key"],kwargs["on_camera_change"]))
        import streamlit as st
        def release():
            st.session_state[kwargs["key"]] = {"camera":{"position":{"x":2,"y":0},"yaw":220}}
            kwargs["on_camera_change"]()
        st.button("Simulate camera release",key="simulate-camera-release",on_click=release)
        return SimpleNamespace(camera=kwargs["data"]["camera"])
    monkeypatch.setattr("story_channel.detail_camera_ui.picker",picker)
    def display(engine,lid):
        from story_channel.location_details_ui import camera_fields
        camera_fields(engine.load().location_reference_sets[lid],"test-detail-camera",{"yaw":0,"fov":30},
            engine=engine,lid=lid,identity="desk_area")
    app = AppTest.from_function(display,args=(engine,lid)).run()
    assert not app.exception
    assert received[-1]["camera"]["position"] == setup["position"]
    assert received[-1]["camera"]["yaw"] == 135 and app.number_input[0].value == 85
    app.number_input[0].set_value(95.).run()
    assert not app.exception
    assert saved_camera(engine,lid,"desk_area")["fov"] == 95
    fresh = AppTest.from_function(display,args=(engine,lid)).run()
    assert not fresh.exception and fresh.number_input[0].value == 95
    assert received[-1]["camera"]["position"] == setup["position"]
    assert received[-1]["fov"] == 95
    fresh.button(key="simulate-camera-release").click().run()
    assert not fresh.exception
    assert saved_camera(engine,lid,"desk_area")["floorPlanSetup"]["position"] == {"x":2,"y":0}
    assert saved_camera(engine,lid,"desk_area")["yaw"] == 220
    assert received[-1]["camera"]["position"] == {"x":2,"y":0}
    assert received[-1]["fov"] == 95
    assert not provider.calls
