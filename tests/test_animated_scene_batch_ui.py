from threading import Event

from streamlit.testing.v1 import AppTest
from streamlit.proto.WidgetStates_pb2 import WidgetStates

from story_channel.scene_production import SceneProduction
from test_animated_scene_cards_ui import prepare
from test_scene_production import episode
from test_studio import world


def display(manager,pid):
    import streamlit as st
    from story_channel.scene_production_ui import animated_scenes
    st.session_state["parent-renders"] = st.session_state.get("parent-renders",0)+1
    st.session_state["generation-context-project"] = pid
    animated_scenes(manager,pid)


def test_video_settings_collapse_and_expand_stays_local(episode):
    engine,_ = prepare(episode)
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert not app.exception
    before = app.session_state["parent-renders"]
    for opened in (False,True):
        panel = next(panel for panel in app.expander if panel.label == "Video settings")
        states = WidgetStates()
        states.widgets.add(id=panel.proto.id,bool_value=opened)
        app._run(states)
        assert not app.exception
        assert app.session_state["parent-renders"] == before
    assert "workspace-task" not in app.session_state


def test_generate_all_queues_every_scene_in_ascending_order_without_page_reload(episode,monkeypatch):
    engine,scenes = prepare(episode)
    ordered = [scene.scene_id for scene in sorted(scenes,key=lambda scene:scene.order)]
    started,release = Event(),Event()
    calls = []
    def generate(self,sid,options,**kwargs):
        calls.append(sid)
        if sid == ordered[0]:
            started.set()
            release.wait(20)
        return {}
    monkeypatch.setattr(SceneProduction,"generate_visual",generate)
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    before = app.session_state["parent-renders"]
    try:
        app.button(key=f"generate-all-animated-scenes-{engine.project_id}").click().run()
        assert not app.exception and started.wait(2)
        assert app.session_state["parent-renders"] == before
        assert calls == ordered[:1]
        tasks = [app.session_state["workspace-task"],*app.session_state.get("workspace-task-queue",[])]
        assert [task["asset_subject"] for task in tasks] == ordered
        assert app.button(key=f"scene-generate-visual-{engine.project_id}-{ordered[0]}").label == "Generating…"
        for sid in ordered[1:]:
            assert app.button(key=f"scene-generate-visual-{engine.project_id}-{sid}").label == "Queuing…"
        assert app.button(key=f"generate-all-animated-scenes-{engine.project_id}").disabled
        release.set()
        # Drive the existing completion/dispatch mechanism and verify execution,
        # rather than only the order in which queue entries were constructed.
        for _ in ordered:
            task = app.session_state.get("workspace-task")
            if task:
                task["future"].result(timeout=5)
            app.run()
        assert not app.exception
        assert calls == ordered
    finally:
        release.set()
        for task in app.session_state.get("workspace-task-queue",[]):
            task["future"].cancel()
        app.session_state["workspace-task-queue"] = []
        if task := app.session_state.get("workspace-task"):
            task["future"].result(timeout=5)
