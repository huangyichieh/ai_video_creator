from concurrent.futures import Future

import pytest
from streamlit.testing.v1 import AppTest


def test_asset_dot_changes_without_closing_panel_or_losing_draft():
    app = AppTest.from_string('''
import streamlit as st
from story_channel.ui_panels import persistent_expander,asset_title
with persistent_expander(asset_title("Office",st.session_state.get("exists",False)),key="asset",expanded=True):
    st.text_input("Draft",key="draft")
''').run()
    assert app.expander[0].label == "🔴 Office"
    app.text_input[0].set_value("Keep my edit").run()
    app.session_state["exists"] = True
    app.run()
    assert not app.exception
    assert app.expander[0].label == "🟢 Office" and app.expander[0].proto.expanded
    assert app.text_input[0].value == "Keep my edit"


def test_panel_keeps_user_choice_when_status_default_and_visibility_change():
    app = AppTest.from_string('''
import streamlit as st
from story_channel.ui_panels import persistent_expander
from story_channel.ui_tasks import preserve_page_widgets
preserve_page_widgets()
if st.session_state.get("visible", True):
    panel = persistent_expander(st.session_state.get("title", "Pending"),
        key="test-panel", expanded=st.session_state.get("default", True))
    if panel.open:
        with panel:
            st.text_area("Draft", key="studio-draft-panel", persist_state="session")
''').run()
    assert app.expander[0].proto.expanded
    app.text_area[0].set_value("Keep my draft").run()
    app.session_state["test-panel"] = False
    app.run()
    assert not app.text_area
    app.session_state["title"] = "Approved · revision 2"
    app.session_state["default"] = False
    app.run()
    assert not app.exception and not app.expander[0].proto.expanded
    app.session_state["visible"] = False
    app.run()
    app.session_state["visible"] = True
    app.run()
    assert not app.expander[0].proto.expanded
    app.session_state["test-panel"] = True
    app.run()
    assert app.text_area[0].value == "Keep my draft"
    app.session_state["title"] = "Pending · revision 3"
    app.run()
    assert not app.exception and app.expander[0].proto.expanded


@pytest.mark.parametrize("phase,expected",[("queued","Queuing…"),("waiting","Queuing…"),
    ("running","Reviewing..."),("saving","Saving…")])
def test_button_tracks_queue_service_and_save_phases_and_clears_after_completion(phase,expected):
    app = AppTest.from_string('''
import streamlit as st
from story_channel.generation_ui import review_button
review_button(key="review")
''')
    future = Future()
    app.session_state["generation-context-project"] = "episode"
    task = {"future":future,"phase":phase,"generation_button_project":"episode","generation_button_key":"review"}
    app.session_state["workspace-task-queue" if phase == "queued" else "workspace-parallel-tasks"] = [task]
    app.run()
    assert not app.exception
    assert app.button[0].label == expected and app.button[0].disabled
    future.set_result(None)
    app.run()
    assert app.button[0].label == "AI Review" and not app.button[0].disabled


def test_idle_watchers_poll_less_frequently_than_active_jobs(monkeypatch):
    # Capture the scheduling messages, rather than waiting for real timers.
    app = AppTest.from_string('''
import streamlit as st
from story_channel.ui_panels import polling_fragment
@polling_fragment
def status():
    st.caption("Saved progress")
status()
''')
    intervals = []
    from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext
    original = ScriptRunContext.enqueue
    def enqueue(self,msg):
        if msg.HasField("auto_rerun"):
            intervals.append(msg.auto_rerun.interval)
        return original(self,msg)
    monkeypatch.setattr(ScriptRunContext,"enqueue",enqueue)
    app.run()
    assert not app.exception and intervals[-1] == 5
    future = Future()
    app.session_state["workspace-task"] = {"future":future}
    app.run()
    assert not app.exception and intervals[-1] == 1
    future.set_result(None)
    app.run()
    assert not app.exception and intervals[-1] == 5
