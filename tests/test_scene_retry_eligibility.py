from concurrent.futures import Future

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from test_scene_production import episode
from test_studio import world


def failed_scene(pipeline):
    engine = AssetPipeline(pipeline.manager,pipeline.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    state = engine.generate("scene_images",subject=sid)
    record = state.records["scene_images:"+sid]
    record.status = "failed"
    record.error = "Too many scene assets for a reliable Runway identity prompt. Split this shot into fewer subjects."
    engine._save(state)
    return engine,sid


def test_retry_refreshes_after_unrelated_task_finishes_without_scene_record_change(episode,monkeypatch):
    _,_,pipeline = episode
    engine,sid = failed_scene(pipeline)
    scene_before = engine.load().records["scene_images:"+sid].model_dump_json()
    requests = []
    monkeypatch.setattr("story_channel.asset_view_ui.refresh_target",requests.append)
    monkeypatch.setattr("story_channel.scene_image_cards_ui.polling_tick",lambda:True)
    def display(engine,sid):
        import streamlit as st
        from story_channel.scene_image_cards_ui import signature,watch,card
        st.session_state.setdefault("old-signature",signature(engine,sid))
        watch(engine,sid,"eligibility-card",st.session_state["old-signature"])
        card(engine,sid,"eligibility-card")
    app = AppTest.from_function(display,args=(engine,sid),default_timeout=25)
    future = Future()
    app.session_state["workspace-task"] = {"id":"unrelated","future":future,"label":"Saving other content",
        "phase":"running","queue_enabled":False}
    app.run()
    assert not app.exception
    assert app.button(key="retry-asset-scene_images:"+sid).disabled
    assert any("current task" in item.value for item in app.caption)
    assert requests == []
    future.set_result(None)
    app.run()
    assert not app.exception
    assert requests == ["eligibility-card"]
    assert not app.button(key="retry-asset-scene_images:"+sid).disabled
    assert engine.load().records["scene_images:"+sid].model_dump_json() == scene_before


def test_retry_explains_real_gate_and_refreshes_when_approval_is_restored(episode):
    _,project,pipeline = episode
    engine,sid = failed_scene(pipeline)
    manifest = pipeline.manager.load(project.project_id)
    approval = manifest.approvals.pop("storyboard")
    pipeline.manager.save(manifest)
    reason = engine.generation_blocker("scene_images",engine.load())
    assert reason and not engine.can_generate("scene_images",engine.load())
    def display(engine,sid):
        import streamlit as st
        from story_channel.scene_image_cards_ui import signature,card
        st.session_state["signature"] = signature(engine,sid)
        card(engine,sid,"gated-retry")
    app = AppTest.from_function(display,args=(engine,sid),default_timeout=25).run()
    assert not app.exception
    assert app.button(key="retry-asset-scene_images:"+sid).disabled
    assert any("Cannot generate:" in item.value and reason in item.value for item in app.caption)
    before = app.session_state["signature"]
    manifest = pipeline.manager.load(project.project_id)
    manifest.approvals["storyboard"] = approval
    pipeline.manager.save(manifest)
    app.run()
    assert not app.exception
    assert before != app.session_state["signature"]
    assert not app.button(key="retry-asset-scene_images:"+sid).disabled
    assert engine.generation_blocker("scene_images",engine.load()) is None
