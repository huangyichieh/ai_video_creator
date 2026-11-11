from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from test_scene_production import episode
from test_studio import world


def test_real_named_card_paints_loading_before_slow_preflight_and_stays_local(episode,monkeypatch):
    import streamlit as st
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    engine.generate("scene_images",subject=sid)
    started,release = Event(),Event()
    observed = []
    original = st.button
    def button(label,*args,**kwargs):
        if kwargs.get("key") == "retry-asset-scene_images:"+sid:
            observed.append(label)
        return original(label,*args,**kwargs)
    monkeypatch.setattr(st,"button",button)
    def slow(*args,**kwargs):
        # The button must already have been rendered in its active state.
        assert observed[-1] == "Generating…"
        started.set()
        release.wait(15)
        return engine.load()
    monkeypatch.setattr(engine,"generate",slow)
    def display(engine):
        import streamlit as st
        from story_channel.scene_image_cards_ui import render
        st.session_state["parent-renders"] = st.session_state.get("parent-renders",0)+1
        st.session_state["generation-context-project"] = engine.project_id
        render(engine)
        st.caption("Parent page remains mounted")
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    assert not app.exception
    parent_renders = app.session_state["parent-renders"]
    try:
        app.button(key="retry-asset-scene_images:"+sid).click().run()
        assert not app.exception
        assert started.wait(2)
        assert app.button(key="retry-asset-scene_images:"+sid).label == "Generating…"
        assert app.button(key="retry-asset-scene_images:"+sid).disabled
        assert app.session_state["parent-renders"] == parent_renders
        # AppTest exposes this targeted run's deltas; the unchanged parent
        # render counter verifies that the surrounding page was not rebuilt.
        assert engine.load().records["scene_images:"+sid].status == "succeeded"  # Preflight has not written a new record.
    finally:
        release.set()
        if task := app.session_state.get("workspace-task"):
            task["future"].result(timeout=5)


def test_fast_failure_is_shown_on_own_scene_card(episode,monkeypatch):
    _,project,pipeline = episode
    engine = AssetPipeline(pipeline.manager,project.project_id)
    sid = pipeline.artifact("storyboard").scenes[0].scene_id
    engine.generate("scene_images",subject=sid)
    def fail(*args,**kwargs):
        raise ValueError("Scene preflight failed: required prop image is missing")
    monkeypatch.setattr(engine,"generate",fail)
    def display(engine):
        from story_channel.scene_image_cards_ui import render
        render(engine)
    app = AppTest.from_function(display,args=(engine,),default_timeout=25).run()
    app.button(key="retry-asset-scene_images:"+sid).click().run()
    assert not app.exception
    task = app.session_state["workspace-task"]
    try:
        task["future"].result(timeout=5)
    except ValueError:
        pass
    app.run()
    assert not app.exception
    assert any("required prop image is missing" in error.value for error in app.error)
    assert not app.button(key="retry-asset-scene_images:"+sid).disabled
