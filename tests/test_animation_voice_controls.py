from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from test_animations_workflow import ready_assets


def display(manager,pid):
    import streamlit as st
    from story_channel.animation_inputs_ui import input_stage
    st.session_state['generation-context-project']=pid
    input_stage(manager,pid,images=False)


def test_voice_batch_uses_backend_gates_and_shows_running_state(tmp_path,monkeypatch):
    renderer,_=ready_assets(tmp_path)
    engine=renderer.assets
    engine.generate('scene_images')
    assert engine.generation_blocker('voices',engine.inspect()) is None
    def old_gate(*args,**kwargs):
        raise ValueError('Approve character and background references first')
    monkeypatch.setattr(AnimationDirector,'require_ready',old_gate)
    started,release=Event(),Event()
    calls=[]
    def generate(self,stage,**kwargs):
        calls.append((stage,kwargs))
        started.set()
        assert release.wait(15)
        return self.load()
    monkeypatch.setattr(AssetPipeline,'generate',generate)
    app=AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    key='animation-input-all-voices-'+engine.project_id
    assert not app.exception
    assert not app.button(key=key).disabled
    assert not any('approve every Animated' in info.value for info in app.info)
    try:
        app.button(key=key).click().run()
        assert not app.exception and started.wait(2)
        assert calls==[('voices',{})]
        button=app.button(key=key)
        assert button.disabled and button.label.startswith('Generating')
    finally:
        release.set()
        task=app.session_state.get('workspace-task')
        if task:
            task['future'].result(timeout=5)


def test_voice_batch_shows_actual_missing_prerequisite(tmp_path):
    renderer,_=ready_assets(tmp_path)
    engine=renderer.assets
    blocker=engine.generation_blocker('voices',engine.inspect())
    assert blocker
    app=AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert not app.exception
    assert app.button(key='animation-input-all-voices-'+engine.project_id).disabled
    assert any(info.value==blocker for info in app.info)
