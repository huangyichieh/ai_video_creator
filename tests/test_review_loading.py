from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from test_generation_hints import SCRIPT


@pytest.mark.parametrize("callback",[False,True])
@pytest.mark.parametrize("fail",[False,True])
def test_review_label_is_task_owned_and_clears_after_success_or_failure(callback,fail):
    script = SCRIPT.replace("generation_button","review_button").replace('"Generate"','"AI Review"')
    gate = Event()
    app = AppTest.from_string(script,default_timeout=15)
    app.session_state["gate"] = gate
    app.session_state["fail"] = fail
    app.session_state["callback"] = callback
    app.session_state["generation-context-project"] = "project-a"
    app.run()
    try:
        app.button(key="active").click().run()
        assert not app.exception
        assert app.button(key="active").label == "Reviewing..."
        assert app.button(key="active").disabled
        assert not any(button.label == "Generating…" for button in app.button)
        app.session_state["generation-context-project"] = "project-b"
        app.run()
        assert app.button(key="active").label == "AI Review"
        app.session_state["generation-context-project"] = "project-a"
        app.run()
        assert app.button(key="active").label == "Reviewing..."
    finally:
        gate.set()
        future = app.session_state["workspace-task"]["future"]
        if fail:
            with pytest.raises(ValueError):
                future.result(timeout=10)
        else:
            future.result(timeout=10)
        app.run()
    assert not app.exception
    assert app.button(key="active").label == "AI Review"


def test_episode_ai_review_uses_loading_label_while_request_runs(world,monkeypatch):
    from story_channel.studio import Episodes
    episode = Episodes(world.manager,world.project_id).create("Episode","An outline to review",60)
    gate = Event()
    def slow_review(*args,**kwargs):
        assert gate.wait(20)
    monkeypatch.setattr("story_channel.studio_ui.review_episode",slow_review)
    def display(manager,pid):
        import streamlit as st
        from story_channel.pipeline import Pipeline
        from story_channel.studio_ui import stage_review
        from story_channel.ui_tasks import collect
        st.session_state["generation-context-project"] = pid
        collect()
        stage_review(Pipeline(manager,pid),"outline")
    app = AppTest.from_function(display,args=(world.manager,episode.project_id),default_timeout=15).run()
    key = f"episode-review-{episode.project_id}-outline"
    try:
        app.button(key=key).click().run()
        assert not app.exception
        assert app.button(key=key).label == "Reviewing..."
        assert app.button(key=key).disabled
    finally:
        gate.set()
        app.session_state["workspace-task"]["future"].result(timeout=10)
        app.run()
    assert app.button(key=key).label == "AI review"


from test_studio import world
