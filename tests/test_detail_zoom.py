from types import SimpleNamespace
import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from story_channel.files import input_hash
from story_channel.location_details import generate,add_draft,generation_status,task_key
from story_channel.detail_zoom import rectangle
from test_location_details import prepare,png
from test_location_master_pack import master_pack
from test_studio import world


def detail(master_pack):
    engine,lid,provider,_ = prepare(master_pack)
    from story_channel.background_library import BackgroundLibrary
    state = BackgroundLibrary(engine).add_detail_reference(lid,"Desk area",png(),detail_id="desk_area",description="Desk texture",source_yaw=45.5)
    ref = state.location_reference_sets[lid]["detail_references"]["desk_area"]
    return engine,lid,provider,ref


def payload(ref):
    return {"expected":input_hash(ref),"rectangle":{"left":.1,"top":.2,"right":.7,"bottom":.8}}


def test_zoom_uses_crop_and_saves_revision_and_request_metadata(master_pack):
    engine,lid,provider,ref = detail(master_pack)
    original = engine.load().records[ref["key"]]
    state = generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    assert len(provider.calls) == 5  # Four masters, one zoom; no master regeneration.
    prompt,refs = provider.calls[-1]
    assert "ONLY this crop" in prompt and "do not return to a wider view" in prompt
    assert len(refs) == 1
    with Image.open(refs[0]) as crop:
        assert crop.size == (48,36)
    record = state.records[ref["key"]]
    assert record.revision == original.revision+1
    assert record.asset.asset_id != original.asset.asset_id
    assert engine.local_path(original.asset.file_path).exists()
    zoom = record.asset.generation_params["zoom"]
    assert zoom["sourceAssetId"] == original.asset.asset_id
    assert state.location_reference_sets[lid]["detail_references"]["desk_area"]["yaw"] == "45.5"
    assert zoom["rectangle"] == payload(ref)["rectangle"]
    assert zoom["pixelBox"] == [8,12,56,48]
    assert generation_status(engine,lid,"desk_area")["status"] == "succeeded"
    assert any(r.asset.asset_id == original.asset.asset_id for r in engine.revisions("backgrounds",original.subject))


def test_zoom_rejects_changed_source_without_paid_call(master_pack):
    engine,lid,provider,ref = detail(master_pack)
    add_draft(engine,lid,"desk_area","Changed area")
    with pytest.raises(ValueError,match="detail changed"):
        generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    assert len(provider.calls) == 4
    assert generation_status(engine,lid,"desk_area")["status"] == "failed"


def test_zoom_validates_saved_yaw_before_provider_request(master_pack):
    engine,lid,provider,ref = detail(master_pack)
    state = engine.load()
    state.location_reference_sets[lid]["detail_references"]["desk_area"]["yaw"] = "not a number"
    engine._save(state)
    ref = engine.load().location_reference_sets[lid]["detail_references"]["desk_area"]
    with pytest.raises(ValueError,match="finite number"):
        generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    assert len(provider.calls) == 4


def test_zoom_failure_keeps_original_and_success_clears_error_report(master_pack,monkeypatch):
    engine,lid,provider,ref = detail(master_pack)
    from story_channel.error_reports import reports
    original = engine.load().records[ref["key"]].asset.asset_id
    original_generate = provider.generate
    def fail(*args):
        raise RuntimeError("Image generation unavailable")
    monkeypatch.setattr(provider,"generate",fail)
    with pytest.raises(RuntimeError,match="unavailable"):
        generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    assert engine.load().records[ref["key"]].asset.asset_id == original
    assert reports(engine.path,task_key(lid,"desk_area"))
    monkeypatch.setattr(provider,"generate",original_generate)
    generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    assert not reports(engine.path,task_key(lid,"desk_area"))


def test_zoom_does_not_overwrite_concurrent_detail_edit(master_pack,monkeypatch):
    engine,lid,provider,ref = detail(master_pack)
    original = engine.load().records[ref["key"]].asset.asset_id
    original_generate = provider.generate
    def changed(*args):
        add_draft(engine,lid,"desk_area","Updated during request","Keep this edit")
        return original_generate(*args)
    monkeypatch.setattr(provider,"generate",changed)
    with pytest.raises(ValueError,match="detail changed during"):
        generate(engine,lid,"desk_area","Desk area","",zoom=payload(ref))
    state = engine.load()
    assert state.records[ref["key"]].asset.asset_id == original
    assert state.location_reference_sets[lid]["detail_references"]["desk_area"]["description"] == "Keep this edit"


@pytest.mark.parametrize("selection",[None,{},dict(left=0,top=0,right=2,bottom=1),dict(left=.5,top=.5,right=.1,bottom=.9),
    dict(left=0,top=0,right=.001,bottom=.001),dict(left=float("nan"),top=0,right=1,bottom=1)])
def test_invalid_selection(selection):
    with pytest.raises(ValueError):
        rectangle(selection,(1536,864))


@pytest.mark.parametrize("other_task",[False,True])
def test_dialog_cancel_and_confirm_only_generates_selected_detail(master_pack,monkeypatch,other_task):
    engine,lid,provider,ref = detail(master_pack)
    def picker(**kwargs):
        return SimpleNamespace(rectangle=payload(ref)["rectangle"])
    monkeypatch.setattr("story_channel.detail_zoom_ui.picker",picker)
    launches = []
    def launch(engine,callback,label,**kwargs):
        launches.append(kwargs)
        callback()
        return {"launched":True}
    monkeypatch.setattr("story_channel.background_assets_ui.launch_background",launch)
    def display(engine,lid,ref):
        import streamlit as st
        from story_channel.detail_zoom_ui import open_zoom,zoom_dialog
        st.button("Open zoom",on_click=lambda:open_zoom(engine,lid,"desk_area",ref))
        if target := st.session_state.get("detail-zoom-target"):
            zoom_dialog(engine,target)
    app = AppTest.from_function(display,args=(engine,lid,ref),default_timeout=25).run()
    if other_task:
        from concurrent.futures import Future
        app.session_state["workspace-task"] = {"future":Future(),"queue_enabled":True,"label":"Unrelated generation"}
    app.button[0].click().run()
    assert not app.exception
    assert not app.button(key="confirm-detail-zoom").disabled
    app.button(key="cancel-detail-zoom").click().run()
    assert not app.exception and not launches and len(provider.calls) == 4
    app.button[0].click().run()
    app.button(key="confirm-detail-zoom").click().run()
    assert not app.exception
    assert len(launches) == 1 and launches[0]["subject"] == lid+"__detail_desk_area"
    assert len(provider.calls) == 5


def test_dialog_restores_saved_selection_when_component_remounts(master_pack,monkeypatch):
    engine,lid,_,ref = detail(master_pack)
    received = []
    def picker(**kwargs):
        received.append(kwargs)
        return SimpleNamespace(rectangle=None)
    monkeypatch.setattr("story_channel.detail_zoom_ui.picker",picker)
    def display(engine,lid,ref,selection):
        import streamlit as st
        from story_channel.detail_zoom_ui import open_zoom,zoom_dialog
        if not st.session_state.get("detail-zoom-target"):
            open_zoom(engine,lid,"desk_area",ref)
            st.session_state["detail-zoom-target"]["rectangle"] = selection
        zoom_dialog(engine,st.session_state["detail-zoom-target"])
    selection = payload(ref)["rectangle"]
    app = AppTest.from_function(display,args=(engine,lid,ref,selection),default_timeout=25).run()
    assert not app.exception
    assert received[-1]["data"]["rectangle"] == selection
    assert received[-1]["default"]["rectangle"] == selection
    assert not app.button(key="confirm-detail-zoom").disabled
