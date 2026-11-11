from streamlit.testing.v1 import AppTest
from streamlit.proto.WidgetStates_pb2 import WidgetStates

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from test_scene_production import episode
from test_studio import world


def display(manager,pid):
    import streamlit as st
    from story_channel.scene_production_ui import direction_cards
    st.session_state["parent-renders"] = st.session_state.get("parent-renders",0)+1
    direction_cards(manager,pid)
    st.caption("Other animation tabs stay mounted")


def test_expanding_direction_and_typing_does_not_rebuild_page_or_call_ai(episode,monkeypatch):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    engine.generate("scene_images")
    AnimationDirector(engine.manager,engine.project_id).prepare()
    def forbidden(*args,**kwargs):
        raise AssertionError("Browsing direction must not launch AI work")
    monkeypatch.setattr("story_channel.scene_production.SceneProduction.propose",forbidden)
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert not app.exception
    before = app.session_state["parent-renders"]
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    from story_channel.scene_production import SceneProduction
    from story_channel.scene_production_ui import scene_direction_title
    service=SceneProduction(engine.manager,engine.project_id)
    action=" / ".join(shot.action.strip() for shot in service.direction(sid).shots if shot.action.strip())
    expected=scene_direction_title(sid,True,action)
    panel = next(p for p in app.expander if p.label==expected)
    states = WidgetStates()
    states.widgets.add(id=panel.proto.id,bool_value=True)
    app._run(states)
    assert not app.exception
    assert app.session_state["parent-renders"] == before
    request = next(w for w in app.text_area if w.label == "Direction request (optional)")
    request.set_value("A slower camera movement").run()
    assert not app.exception
    assert app.session_state["parent-renders"] == before
    assert "workspace-task" not in app.session_state
