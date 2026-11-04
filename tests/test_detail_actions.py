import pytest
from streamlit.testing.v1 import AppTest

from story_channel.location_details import add_draft,remove
from story_channel.files import input_hash
from test_location_details import prepare
from test_location_master_pack import master_pack
from test_studio import world


def app_for(engine,lid):
    def display(engine,lid):
        from story_channel.location_details_ui import render
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=25).run()
    for identity in engine.load().location_reference_sets[lid].get("detail_references",{}):
        app.session_state[f"background-card-detail-{engine.project_id}-{lid}-{identity}"] = True
    return app.run()


def test_generate_button_generates_only_its_own_detail_draft(master_pack,monkeypatch):
    engine,lid,provider,_ = prepare(master_pack)
    add_draft(engine,lid,"desk_area","Desk area","Desk close-up")
    add_draft(engine,lid,"window_area","Window area","Window close-up")
    masters = {key:record.asset.asset_id for key,record in engine.load().records.items() if key.startswith("backgrounds:") and record.asset}
    calls_before = len(provider.calls)
    subjects = []
    def launch(engine,callback,label,**kwargs):
        subjects.append(kwargs["subject"])
        return callback()
    monkeypatch.setattr("story_channel.location_details_ui.launch_background",launch)
    monkeypatch.setattr("story_channel.location_details_ui.camera_fields",lambda *args,**kwargs:{
        "camera_setup":{"position":{"x":0,"y":0},"yaw":90},"fov":70})
    app = app_for(engine,lid)
    assert not app.exception
    prefix = f"location-details-{engine.project_id}-{lid}"
    card = next(card for card in app.expander if "window_area" in card.label)
    assert card.button[0].label == "Remove detail"
    app.button(key=prefix+"-regenerate-window_area").click().run()
    assert not app.exception
    state = engine.load()
    assert len(provider.calls) == calls_before + 1
    assert subjects == [lid+"__detail_window_area"]
    detail = state.location_reference_sets[lid]["detail_references"]["window_area"]
    assert detail["description"] == "Window close-up" and detail.get("file")
    assert engine.local_path(detail["file"]).is_file()
    card = next(card for card in app.expander if "window_area" in card.label)
    assert card.proto.expanded
    assert card.get("image")
    assert state.location_reference_sets[lid]["detail_references"]["desk_area"]["status"] == "draft"
    assert all(state.records[key].asset.asset_id == identity for key,identity in masters.items())


def test_remove_opens_id_confirmation_and_deletes_only_selected_detail(master_pack):
    engine,lid,_,_ = master_pack
    add_draft(engine,lid,"desk_area","Desk area")
    add_draft(engine,lid,"window_area","Window area")
    app = app_for(engine,lid)
    prefix = f"location-details-{engine.project_id}-{lid}"
    app.button(key=prefix+"-remove-desk_area").click().run()
    assert not app.exception
    assert app.button(key="confirm-delete-location-detail").disabled
    field = f"remove-location-detail-id-{engine.project_id}-{lid}-desk_area"
    app.text_input(key=field).input("desk_area ").run()
    assert app.button(key="confirm-delete-location-detail").disabled
    app.button(key="cancel-delete-location-detail").click().run()
    assert not app.exception and "desk_area" in engine.load().location_reference_sets[lid]["detail_references"]
    app.button(key=prefix+"-remove-desk_area").click().run()
    assert app.text_input(key=field).value == ""
    app.text_input(key=field).input("desk_area").run()
    assert not app.button(key="confirm-delete-location-detail").disabled
    app.button(key="confirm-delete-location-detail").click().run()
    assert not app.exception
    assert set(engine.load().location_reference_sets[lid]["detail_references"]) == {"window_area"}


def test_remove_requires_exact_id_and_rejects_changed_detail(master_pack):
    engine,lid,_,_ = master_pack
    state = add_draft(engine,lid,"desk_area","Desk area")
    expected = input_hash(state.location_reference_sets[lid]["detail_references"]["desk_area"])
    with pytest.raises(ValueError,match="exact detail ID"):
        remove(engine,lid,"desk_area",confirmation="window_area")
    add_draft(engine,lid,"desk_area","Updated desk area")
    with pytest.raises(ValueError,match="detail changed"):
        remove(engine,lid,"desk_area",confirmation="desk_area",expected=expected)
    assert "desk_area" in engine.load().location_reference_sets[lid]["detail_references"]


@pytest.mark.parametrize("phase",["running","queued"])
def test_detail_image_is_replaced_by_loading_until_zoom_finishes(master_pack,monkeypatch,phase):
    from concurrent.futures import Future
    from story_channel.background_library import BackgroundLibrary
    from story_channel.location_details import _generation_status
    from test_location_details import png
    engine,lid,_,_ = prepare(master_pack)
    BackgroundLibrary(engine).add_detail_reference(lid,"Desk area",png(),detail_id="desk_area",source_yaw=0)
    messages = []
    from story_channel.background_assets_ui import loading_result
    def loading(label):
        messages.append(label)
        loading_result(label)
    monkeypatch.setattr("story_channel.background_assets_ui.loading_result",loading)
    app = app_for(engine,lid)
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert card.get("image")
    if phase == "running":
        _generation_status(engine,lid,"desk_area","running")
    else:
        app.session_state["workspace-task-queue"] = [{"future":Future(),"phase":"queued",
            "stable_background_project":engine.project_id,"asset_subject":lid+"__detail_desk_area"}]
    app.run()
    assert not app.exception
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert not card.get("image")
    assert messages[-1] == ("Generating..." if phase == "running" else "Queuing...")
    app.session_state["workspace-task-queue"] = []
    _generation_status(engine,lid,"desk_area","succeeded")
    app.run()
    assert not app.exception
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert card.get("image")


def test_pressing_regenerate_immediately_replaces_old_image(master_pack,monkeypatch):
    from concurrent.futures import Future
    import streamlit as st
    from story_channel.background_library import BackgroundLibrary
    from test_location_details import png
    engine,lid,_,_ = prepare(master_pack)
    BackgroundLibrary(engine).add_detail_reference(lid,"Desk area",png(),detail_id="desk_area",description="Desk texture",source_yaw=0)
    monkeypatch.setattr("story_channel.location_details_ui.camera_fields",lambda *args,**kwargs:{
        "camera_setup":{"position":{"x":0,"y":0},"yaw":0},"fov":70})
    pending = Future()
    def launch(engine,callback,label,**kwargs):
        # The worker has not started and has not written a running status yet.
        task = {"future":pending,"phase":"running","queue_enabled":True,
            "stable_background_project":engine.project_id,"asset_subject":kwargs["subject"]}
        st.session_state["workspace-task"] = task
        return task
    monkeypatch.setattr("story_channel.location_details_ui.launch_background",launch)
    app = app_for(engine,lid)
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert card.get("image")
    key = f"location-details-{engine.project_id}-{lid}-regenerate-desk_area"
    app.button(key=key).click().run()
    assert not app.exception
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert not card.get("image")
    assert app.button(key=key).label == "Generating…"
    assert app.button(key=key).disabled
    pending.set_result(None)
    app.run()
    assert not app.exception
    card = next(card for card in app.expander if "desk_area" in card.label)
    assert card.get("image")
