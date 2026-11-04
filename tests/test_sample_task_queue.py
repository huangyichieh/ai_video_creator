from threading import Event
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest
from test_studio import world


SCRIPT = '''
import streamlit as st
from story_channel.character_library_ui import launch_media
from story_channel.ui_tasks import launch, collect, generation_busy
from story_channel.generation_ui import generation_button
collect()
gate, calls, media = st.session_state["gate"], st.session_state["calls"], st.session_state["media"]
fail = st.session_state["fail"]
def sample():
    calls.append("sample")
    assert gate.wait(10)
    if fail:
        raise ValueError("sample failed")
def start_sample():
    launch_media(media, sample, "Generating sample", button_key="sample")
def start_other():
    launch(lambda: calls.append("visual"), "Generating visual", rerun_on_start=False)
generation_button("Generate voice sample", key="sample", disabled=generation_busy(), on_click=start_sample)
generation_button("Generate visual", key="visual", disabled=generation_busy(), on_click=start_other)
'''


@pytest.mark.parametrize("fail", [False, True])
def test_voice_sample_allows_queue_and_continues_after_success_or_failure(fail):
    gate, calls = Event(), []
    app = AppTest.from_string(SCRIPT)
    for key, value in {"gate": gate, "calls": calls, "media": SimpleNamespace(), "fail": fail,
                       "generation-context-project": "project"}.items():
        app.session_state[key] = value
    app.run()
    app.button(key="sample").click().run()
    active = app.session_state["workspace-task"]
    try:
        assert not app.button(key="visual").disabled
        app.button(key="visual").click().run()
        assert not app.exception
        assert app.session_state["workspace-task"] is active
        assert len(app.session_state["workspace-task-queue"]) == 1
        assert app.button(key="visual").label == "Queuing…"
        assert app.button(key="visual").disabled
        assert calls == ["sample"]
    finally:
        gate.set()
        try:
            active["future"].result(timeout=5)
        except ValueError:
            assert fail
    app.run()
    app.session_state["workspace-task"]["future"].result(timeout=5)
    app.run()
    assert not app.exception
    assert calls == ["sample", "visual"]
    assert not app.session_state["workspace-task-queue"]
    assert app.button(key="visual").label == "Generate visual"


def test_stop_after_current_task_cancels_waiting_work():
    gate, calls = Event(), []
    app = AppTest.from_string(SCRIPT)
    for key, value in {"gate": gate, "calls": calls, "media": SimpleNamespace(), "fail": False}.items():
        app.session_state[key] = value
    app.run()
    app.button(key="sample").click().run()
    active = app.session_state["workspace-task"]
    app.button(key="visual").click().run()
    waiting = app.session_state["workspace-task-queue"][0]
    active["stop_event"].set()
    gate.set()
    active["future"].result(timeout=5)
    app.run()
    assert calls == ["sample"]
    assert waiting["future"].cancelled()
    assert not app.session_state["workspace-task-queue"]


def test_assets_page_runs_visual_while_voice_sample_is_still_running(world, monkeypatch):
    from pathlib import Path
    from story_channel.library_media import LibraryMedia
    world.generate_characters()
    cid = world.pipeline.artifact("characters").characters[0].character_id
    gate, started = Event(), Event()

    def sample(*args, **kwargs):
        started.set()
        assert gate.wait(20)

    monkeypatch.setattr(LibraryMedia, "talking_sample", sample)
    for name, value in {"STORY_DATA_ROOT": str(world.manager.root), "STORY_LLM_PROVIDER": "demo",
                        "STORY_ASSET_PROVIDER": "demo", "OPENAI_ADMIN_KEY": ""}.items():
        monkeypatch.setenv(name, value)
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    app.session_state[f"page-tab-character-{world.project_id}-{cid}"] = "Voice"
    app.run()
    app.button(key="studio-audio-sample-" + cid).click().run()
    active = app.session_state["workspace-task"]
    try:
        assert started.wait(5)
        app.session_state[f"page-tab-character-{world.project_id}-{cid}"] = "Visual"
        app.run()
        generate = app.button(key="retry-asset-character_references:" + cid)
        assert not generate.disabled
        generate.click().run()
        assert not app.exception
        assert not active["future"].done()
        parallel = app.session_state["workspace-parallel-tasks"]
        if parallel:
            parallel[0]["future"].result(timeout=10)
        from story_channel.assets import AssetPipeline
        state = AssetPipeline(world.manager, world.project_id, demo=True).load()
        assert state.records["character_references:" + cid].asset
    finally:
        gate.set()
        active["future"].result(timeout=5)
    app.run()
    assert not app.exception
    assert world.manager.project_path(world.project_id).joinpath("production.json").exists()
