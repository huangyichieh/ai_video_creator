from threading import Event
import pytest

from streamlit.testing.v1 import AppTest
from streamlit.proto.WidgetStates_pb2 import WidgetStates

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.scene_production import SceneProduction
from test_scene_production import episode
from test_studio import world


def display(manager,pid):
    import streamlit as st
    from story_channel.animated_scene_cards_ui import render
    st.session_state["parent-renders"] = st.session_state.get("parent-renders",0)+1
    st.session_state["generation-context-project"] = pid
    st.session_state["workspace_page"] = "Animations"
    st.session_state[f"page-tab-animations-{pid}"] = "Animated scenes"
    render(manager,pid)


def prepare(episode):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    engine.generate("scene_images")
    AnimationDirector(engine.manager,engine.project_id).prepare()
    return engine,pipeline.artifact("storyboard").scenes


def test_immediate_feedback_other_cards_editable_and_video_tasks_queue(episode,monkeypatch):
    import streamlit as st
    engine,scenes = prepare(episode)
    first,second = scenes[0].scene_id,scenes[1].scene_id
    started,release,second_started,second_release = Event(),Event(),Event(),Event()
    observed,html = [],[]
    original_button,original_html = st.button,st.html
    def button(label,*args,**kwargs):
        if kwargs.get("key") == f"scene-generate-visual-{engine.project_id}-{first}":
            observed.append(label)
        return original_button(label,*args,**kwargs)
    def markup(value,*args,**kwargs):
        html.append(value)
        return original_html(value,*args,**kwargs)
    monkeypatch.setattr(st,"button",button)
    monkeypatch.setattr(st,"html",markup)
    def generate(self,sid,options,**kwargs):
        if sid == first:
            assert observed[-1] == "Generating…"
            started.set()
            release.wait(20)
        else:
            second_started.set()
            second_release.wait(20)
        return {}
    monkeypatch.setattr(SceneProduction,"generate_visual",generate)
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    renders = app.session_state["parent-renders"]
    try:
        app.button(key=f"scene-generate-visual-{engine.project_id}-{first}").click().run()
        assert not app.exception and started.wait(2)
        assert app.session_state["parent-renders"] == renders
        assert app.button(key=f"scene-generate-visual-{engine.project_id}-{first}").label == "Generating…"
        assert any("Generating..." in item and "scene-image-loading-icon" in item for item in html)
        app.run()  # A deliberate refresh must also keep every scene visible.
        assert not app.exception
        assert len([box for box in app.markdown if box.value in ["**"+s.scene_id+"**" for s in scenes]]) == len(scenes)
        assert not app.button(key=f"scene-generate-visual-{engine.project_id}-{second}").disabled
        # The second scene's editor remains usable while the first is blocked.
        panel = [p for p in app.expander if p.label == "Direction and generation details"][1]
        states = WidgetStates()
        states.widgets.add(id=panel.proto.id,bool_value=True)
        app._run(states)
        assert not app.exception
        assert not next(w for w in app.text_area if w.label == "Scene direction (editable)").disabled
        app.button(key=f"scene-generate-visual-{engine.project_id}-{second}").click().run()
        assert not app.exception and not second_started.is_set()
        assert app.button(key=f"scene-generate-visual-{engine.project_id}-{second}").label == "Queuing…"
        assert len(app.session_state["workspace-task-queue"]) == 1
        first_task = app.session_state["workspace-task"]
        release.set()
        first_task["future"].result(timeout=5)
        app.run()
        assert second_started.wait(2)
        assert app.button(key=f"scene-generate-visual-{engine.project_id}-{second}").label == "Generating…"
    finally:
        release.set()
        second_release.set()
        for task in [app.session_state.get("workspace-task"),*app.session_state.get("workspace-parallel-tasks",[])]:
            if task:
                task["future"].result(timeout=5)


def test_failed_retry_shows_error_and_restores_previous_video(episode,monkeypatch):
    engine,scenes = prepare(episode)
    from story_channel.render_models import RenderOptions
    from story_channel.rendering import RenderPipeline
    sid = scenes[0].scene_id
    render = RenderPipeline(engine.manager,engine.project_id)
    render.configure(options=RenderOptions(width=320,height=180,fps=12,music=False,sfx=False,subtitles="none"))
    SceneProduction(engine.manager,engine.project_id).generate_visual(sid,render.load().options)
    def fail(*args,**kwargs):
        raise RuntimeError("Video provider rejected the request")
    monkeypatch.setattr(SceneProduction,"generate_visual",fail)
    app = AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert app.get("video")
    app.button(key=f"scene-generate-visual-{engine.project_id}-{sid}").click().run()
    task = app.session_state["workspace-task"]
    try:
        task["future"].result(timeout=5)
    except RuntimeError:
        pass
    app.run()
    assert not app.exception and app.get("video")
    assert any("Video provider rejected" in error.value for error in app.error)
    assert not app.button(key=f"scene-generate-visual-{engine.project_id}-{sid}").disabled


@pytest.mark.parametrize("regenerate",[False,True])
def test_visible_direction_edits_apply_and_generation_reads_updated_direction(episode,monkeypatch,regenerate):
    engine,scenes=prepare(episode)
    sid,other=scenes[0].scene_id,scenes[1].scene_id
    service=SceneProduction(engine.manager,engine.project_id)
    original=service.direction(sid)
    untouched=service.direction(other).model_dump()
    captured=[]
    def generate(self,scene,options,**kwargs):
        captured.append(self.direction(scene).shots[0].action)
        return {}
    monkeypatch.setattr(SceneProduction,"generate_visual",generate)
    app=AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    target=f"animated-scene-card-{engine.project_id}-{sid}"
    key=f"{target}-direct-{service.direction_hash(sid)}-{original.shots[0].shot_id}-action"
    assert not app.text_area(key=key).disabled
    renders=app.session_state["parent-renders"]
    app.text_area(key=key).set_value("Slowly turn toward the window and wave.").run()
    assert not app.exception
    assert app.session_state["parent-renders"]==renders
    assert service.direction(sid).model_dump()==original.model_dump()
    assert not captured
    assert not app.button(key=target+"-save-changes").disabled
    assert not app.button(key=target+"-save_generate").disabled
    assert app.button(key=f"scene-generate-visual-{engine.project_id}-{sid}").disabled
    camera_key=key.removesuffix("action")+"camera"
    app.text_area(key=camera_key).set_value("Static camera, then a slow pan toward the window.").run()
    app.button(key=target+("-save_generate" if regenerate else "-save-changes")).click().run()
    assert not app.exception
    app.session_state["workspace-task"]["future"].result(timeout=5)
    assert service.direction(sid).shots[0].action=="Slowly turn toward the window and wave."
    assert service.direction(sid).shots[0].camera=="Static camera, then a slow pan toward the window."
    assert service.direction(other).model_dump()==untouched
    app.run()
    assert not app.exception
    assert app.button(key=target+"-save-changes").disabled
    assert app.button(key=target+"-save_generate").disabled
    if not regenerate:
        assert not captured
        app.button(key=f"scene-generate-visual-{engine.project_id}-{sid}").click().run()
        app.session_state["workspace-task"]["future"].result(timeout=5)
    assert captured==["Slowly turn toward the window and wave."]
