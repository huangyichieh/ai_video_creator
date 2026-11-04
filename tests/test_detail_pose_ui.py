from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from story_channel.location_details import add_draft,save_camera
from test_location_master_pack import master_pack
from test_studio import world


def test_preview_is_local_cached_and_updates_when_saved_pose_changes(master_pack,monkeypatch):
    engine,lid,provider,_ = master_pack
    add_draft(engine,lid,"desk_area","Desk area","Desk close-up")
    setup = {"position":{"x":0,"y":0},"yaw":0,"heightMeters":1.6,"pitch":0}
    save_camera(engine,lid,"desk_area",setup,80)
    monkeypatch.setattr("story_channel.detail_camera_ui.picker",lambda **kw:SimpleNamespace(camera=kw["data"]["camera"]))
    monkeypatch.setattr("story_channel.detail_camera_pose_ui.picker",lambda **kw:SimpleNamespace(pose=kw["data"]["pose"]))
    def display(engine,lid):
        from story_channel.location_details_ui import camera_fields
        camera_fields(engine.load().location_reference_sets[lid],"preview-test",engine=engine,lid=lid,identity="desk_area")
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=20).run()
    assert not app.exception and not app.get("image")
    app.session_state[f"detail-camera-preview-{engine.project_id}-{lid}-desk_area"] = True
    app.run()
    root = engine.path/"references"/"detail_camera_previews"/lid/"desk_area"
    files = list(root.glob("*.png"))
    assert not app.exception and app.get("image") and len(files) == 1
    original = files[0].read_bytes()
    app.run()
    assert len(list(root.glob("*.png"))) == 1
    save_camera(engine,lid,"desk_area",{**setup,"heightMeters":2.5,"pitch":-30},80)
    app.run()
    assert not app.exception and app.get("image")
    assert len(list(root.glob("*.png"))) == 2
    assert any(file.read_bytes() != original for file in root.glob("*.png"))
    assert not provider.calls


def test_generate_click_snapshots_latest_durable_pose_not_old_render(master_pack,monkeypatch):
    from test_location_details import prepare
    engine,lid,provider,_ = prepare(master_pack)
    add_draft(engine,lid,"desk_area","Desk area","Desk close-up")
    old = {"position":{"x":0,"y":0},"yaw":0,"heightMeters":1.6,"pitch":0}
    latest = {**old,"heightMeters":2.5,"pitch":-30}
    save_camera(engine,lid,"desk_area",latest,85)
    monkeypatch.setattr("story_channel.location_details_ui.camera_fields",lambda *a,**k:{"camera_setup":old,"fov":70})
    captured = []
    monkeypatch.setattr("story_channel.location_details.generate",lambda *a,**kw:captured.append(kw))
    monkeypatch.setattr("story_channel.location_details_ui.launch_background",lambda engine,callback,*a,**k:callback())
    def display(engine,lid):
        from story_channel.location_details_ui import render
        render(engine,lid)
    app = AppTest.from_function(display,args=(engine,lid),default_timeout=20).run()
    calls = len(provider.calls)
    app.button(key=f"location-details-{engine.project_id}-{lid}-regenerate-desk_area").click().run()
    assert not app.exception
    assert captured[0]["camera_setup"]["heightMeters"] == 2.5
    assert captured[0]["camera_setup"]["pitch"] == -30
    assert captured[0]["fov"] == 85
    assert len(provider.calls) == calls
