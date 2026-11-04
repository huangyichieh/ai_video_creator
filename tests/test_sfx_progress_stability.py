from concurrent.futures import Future
import pytest

from streamlit.testing.v1 import AppTest

from test_studio import world


def task(identity):
    return {"id":identity,"future":Future(),"phase":"running","ui_owned_asset_project":"episode",
        "generation_button_project":"episode","resource":"storyboard-audio:SFX:"+identity,
        "sfx_batch_project":"episode","label":"Generating "+identity,"success":None,"failure":None,
        "inline":True,"queue_enabled":True,"allow_parallel":True}


@pytest.mark.parametrize("saw_running",[True,False])
def test_partial_batch_completion_refreshes_only_batch_and_affected_card(monkeypatch,saw_running):
    refreshed,reruns = [],[]
    monkeypatch.setattr("story_channel.asset_view_ui.refresh_target",refreshed.append)
    def rerun(*args,**kwargs):
        reruns.append(kwargs)
        raise AssertionError("Audio progress must not request a full-app rerun")
    monkeypatch.setattr("streamlit.rerun",rerun)
    app = AppTest.from_string('''
from pathlib import Path
import streamlit as st
from story_channel.ui_tasks import task_status
from story_channel.storyboard_audio_ui import watch_batch
from story_channel.audio_asset_cards import watch
st.session_state["workspace_page"]="Animations"
task_status(Path("episode"))
if st.session_state.get("poll"):
    watch_batch("episode","sfx-batch-control-episode",st.session_state["batch-baseline"])
    watch("episode","storyboard-audio:SFX:one","audio-card-one",st.session_state["card-baseline"])
st.text_input("Unrelated draft",key="draft",persist_state="session")
''')
    one,two = task("one"),task("two")
    app.session_state["workspace-task"] = one
    app.session_state["workspace-parallel-tasks"] = [two]
    app.session_state["batch-baseline"] = [("one","running",False),("two","running",False)]
    app.session_state["card-baseline"] = ([("one","running",False)] if saw_running else [],True,0)
    app.run()
    app.text_input[0].set_value("Keep this draft").run()
    one["future"].set_result(None)
    app.session_state["poll"] = True
    app.run()
    assert not app.exception and not reruns
    assert refreshed == ["sfx-batch-control-episode","audio-card-one"]
    assert app.session_state["workspace-task"]["id"] == "two"
    assert app.text_input[0].value == "Keep this draft"
    two["future"].set_exception(RuntimeError("Failed sound"))
    app.run()
    assert not app.exception and not reruns
    assert app.session_state["workspace-task-message"] == ("error","Failed sound")
    assert "workspace-task" not in app.session_state


def test_queued_batch_item_disables_its_own_generate_button_and_keeps_card_open(world):
    from story_channel.audio_assets import add_sfx
    identity = add_sfx(world.manager,world.project_id,"Clock tick","A clock ticking")
    pending = task(identity)
    pending.update(phase="queued",ui_owned_asset_project=world.project_id)
    def display(manager,pid):
        from story_channel.audio_asset_cards import library
        library(manager,pid,"SFX")
    app = AppTest.from_function(display,args=(world.manager,world.project_id))
    app.session_state["workspace-task-queue"] = [pending]
    app.run()
    key = f"audio-library-{world.project_id}-SFX-{identity}-card"
    app.session_state[key] = True
    for phase,label in (("queued","Queuing…"),("running","Generating…"),("saving","Saving…")):
        pending["phase"] = phase
        app.run()
        assert not app.exception
        assert next(panel for panel in app.expander if panel.key == key).proto.expanded
        button = next(button for button in app.button if button.key and button.key.endswith("-elevenlabs"))
        assert button.label == label and button.disabled
    app.session_state["workspace-task-queue"] = []
    pending["future"].cancel()


def test_shared_sync_waits_for_queued_audio_even_when_no_request_is_running(world,monkeypatch):
    from story_channel.studio import Episodes
    episode = Episodes(world.manager,world.project_id).create("Queued episode","An outline",12)
    pending = task("waiting")
    pending.update(phase="queued",ui_owned_asset_project=episode.project_id)
    def forbidden(*args,**kwargs):
        raise AssertionError("Do not rebuild/synchronize the page while audio is queued")
    monkeypatch.setattr("story_channel.check_asset_sync.synchronize",forbidden)
    monkeypatch.setattr("streamlit.rerun",forbidden)
    def display(manager,pid):
        from story_channel.storyboard_asset_check_ui import render,watch_shared
        render(manager,pid,[],allow_animation=False)
        watch_shared(manager,pid,"older-shared-version")
    app = AppTest.from_function(display,args=(world.manager,episode.project_id))
    app.session_state["workspace-task-queue"] = [pending]
    app.run()
    assert not app.exception
    assert app.button(key="check-assets-refresh-"+episode.project_id).disabled
    app.session_state["workspace-task-queue"] = []
    pending["future"].cancel()
