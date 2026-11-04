from threading import Event

import pytest
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from story_channel.generation_ui import generation_button
from story_channel.ui_tasks import launch, collect, busy
collect()
gate = st.session_state["gate"]
fail = st.session_state["fail"]
def work():
    if not gate.wait(20):
        raise RuntimeError("Timed out waiting for test")
    if fail:
        raise ValueError("Generation failed")
def start():
    launch(work, "Generating test data", rerun_on_start=False, inline=True)
def page(value):
    st.session_state["page"] = value
if st.session_state.get("page") == "other":
    st.button("Back", key="back", on_click=page, args=("generate",))
else:
    st.button("Other page", key="other", on_click=page, args=("other",))
    if st.session_state["callback"]:
        generation_button("Generate", key="active", disabled=busy(), on_click=start)
    else:
        first, second = st.columns(2)
        clicked = generation_button("Generate", key="active", disabled=busy(), container=first)
        generation_button("Generate", key="idle", disabled=busy(), container=second)
        if clicked:
            start()
            st.rerun()
'''


def hints(app):
    assert not app.get("html")
    return [button.label for button in app.button if button.label == "Generating…"]


@pytest.mark.parametrize("callback", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_hint_owns_correct_button_survives_navigation_and_clears_on_completion(callback, fail):
    gate = Event()
    app = AppTest.from_string(SCRIPT, default_timeout=10)
    app.session_state["gate"] = gate
    app.session_state["callback"] = callback
    app.session_state["fail"] = fail
    app.session_state["generation-context-project"] = "project-a"
    app.run()
    assert not hints(app)
    try:
        app.button(key="active").click().run()
        assert not app.exception
        assert len(hints(app)) == 1
        assert "Generating…" in hints(app)[0]
        assert app.button(key="active").disabled
        assert app.session_state["workspace-task"]["generation_button_key"] == "active"
        app.session_state["generation-context-project"] = "project-b"
        app.run()
        assert not hints(app)
        app.session_state["generation-context-project"] = "project-a"
        app.run()
        assert len(hints(app)) == 1
        app.button(key="other").click().run()
        assert not hints(app)
        app.button(key="back").click().run()
        assert len(hints(app)) == 1
    finally:
        gate.set()
        future = app.session_state["workspace-task"]["future"]
        if fail:
            with pytest.raises(ValueError, match="Generation failed"):
                future.result(timeout=10)
        else:
            future.result(timeout=10)
        app.run()
    assert not app.exception
    assert not hints(app)


def test_validation_without_a_launch_does_not_tag_later_save_as_generation():
    app = AppTest.from_string('''
import streamlit as st
from concurrent.futures import Future
from story_channel.generation_ui import generation_button
from story_channel.ui_tasks import launch
generation_button("Generate", key="invalid")
if st.button("Save", key="save"):
    launch(lambda: None, "Saving", rerun_on_start=False)
''').run()
    app.button(key="invalid").click().run()
    app.button(key="save").click().run()
    assert not app.exception
    assert app.session_state["workspace-task"]["generation_button_key"] is None
