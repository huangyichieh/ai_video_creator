from concurrent.futures import Future
from types import SimpleNamespace
from streamlit.testing.v1 import AppTest
from story_channel.assets import AssetPipeline
from story_channel.models import utc_now
from test_scene_production import episode
from test_studio import world


def test_only_changed_scene_refreshes_and_terminal_error_does_not_repeat(episode,monkeypatch):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    scenes = pipeline.artifact("storyboard").scenes
    first,second = scenes[0].scene_id,scenes[1].scene_id
    engine.generate("scene_images",subject=first)
    from story_channel.scene_image_cards_ui import signature
    requests = []
    monkeypatch.setattr("story_channel.asset_view_ui.refresh_target",requests.append)
    def display(engine,first,second):
        import streamlit as st
        from story_channel.scene_image_cards_ui import watch
        watch(engine,first,"first-card",st.session_state["first-baseline"])
        watch(engine,second,"second-card",st.session_state["second-baseline"])
    app = AppTest.from_function(display,args=(engine,first,second))
    app.session_state["first-baseline"] = signature(engine,first)
    app.session_state["second-baseline"] = signature(engine,second)
    app.run()
    state = engine.load()
    record = state.records["scene_images:"+first]
    record.remote_status = "RUNNING 35%"
    record.updated_at = utc_now()
    engine._save(state)
    app.run()
    assert not app.exception and not requests
    record.status,record.error = "failed","Provider request failed"
    engine._save(state)
    app.run()
    assert not app.exception and requests == ["first-card"]
    app.session_state["first-baseline"] = signature(engine,first)
    app.run()
    assert requests == ["first-card"]


def test_scene_preview_keeps_existing_image_while_running_and_has_no_status_box(episode,monkeypatch):
    import streamlit as st
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    state = engine.generate("scene_images",subject=sid)
    record = state.records["scene_images:"+sid]
    path = engine.local_path(record.asset.file_path)
    record.status = "running"
    engine._save(state)
    engine._task_activity["scene_images:"+sid] = "running"
    images = []
    original = st.image
    def image(value,*args,**kwargs):
        images.append(value)
        return original(value,*args,**kwargs)
    monkeypatch.setattr(st,"image",image)
    def display(engine,sid):
        from story_channel.scene_image_cards_ui import card
        card(engine,sid,"test-scene")
    app = AppTest.from_function(display,args=(engine,sid),default_timeout=25)
    app.session_state["workspace-task"] = {"id":"running","future":Future(),"asset_engine":engine,
        "label":"Generating scene images","phase":"running"}
    app.run()
    assert not app.exception and images == [str(path)]
    assert not app.status
    assert app.button(key="retry-asset-scene_images:"+sid).label == "Generating…"
    assert app.button(key="retry-asset-scene_images:"+sid).disabled
    assert "Download" in [button.label for button in app.get("download_button")]


def test_global_task_progress_does_not_rebuild_scene_images_page(tmp_path,monkeypatch):
    import streamlit as st
    folder = tmp_path/"episode"
    folder.mkdir()
    (folder/"production.json").write_text('{}')
    progress = [1]
    engine = SimpleNamespace(activity_snapshot=lambda:progress[0])
    monkeypatch.setattr("streamlit.runtime.scriptrunner.get_script_run_ctx",lambda:SimpleNamespace(fragment_ids_this_run=["status-timer"]))
    def forbidden(*args,**kwargs):
        raise AssertionError("Progress changes must not rerun the whole scene page")
    monkeypatch.setattr(st,"rerun",forbidden)
    def display(folder):
        from story_channel.ui_tasks import task_status
        task_status(folder)
    app = AppTest.from_function(display,args=(folder,))
    app.session_state["workspace_page"] = "Animations"
    app.session_state["page-tab-animations-episode"] = "Scene images"
    app.session_state["workspace-task"] = {"id":"running","future":Future(),"asset_engine":engine,
        "asset-activity-snapshot":0,"progress_path":None,"label":"Generating scene images","queue_enabled":False}
    app.run()
    assert not app.exception
    progress[0] = 2
    (folder/"production.json").write_text('{"progress":50}')
    app.run()
    assert not app.exception
