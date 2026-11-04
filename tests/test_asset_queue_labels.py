from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from test_studio import world


def test_queue_button_transitions_to_generating_and_restores_each_finished_asset(world, monkeypatch):
    world.generate_characters()
    world.generate_characters("Guest")
    first, second = [character.character_id for character in world.pipeline.artifact("characters").characters]
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    started_first, started_second, release_first, release_second = Event(), Event(), Event(), Event()
    original = engine.image_provider.generate
    calls = []
    def generate(prompt, references, config):
        calls.append(prompt)
        started, release = (started_first, release_first) if len(calls) == 1 else (started_second, release_second)
        started.set()
        if not release.wait(20):
            raise RuntimeError("Test did not release asset")
        return original(prompt, references, config)
    engine.image_provider.generate = generate
    first_key = "retry-asset-character_references:" + first
    second_key = "retry-asset-character_references:" + second
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.generate, "character_references", subject=first)
        try:
            assert started_first.wait(10)
            app.session_state["workspace-task"] = {"future": future, "label": "Generating assets", "asset_engine": engine,
                "generation_button_key": first_key, "generation_button_project": world.project_id,
                "dialog_owner": None, "inline": True, "success": None, "failure": None, "stop_event": None,
                "progress_path": engine.state_path}
            app.run()
            assert not app.exception
            assert app.button(key=first_key).label == "Generating…"
            assert app.button(key=second_key).label == "Queue asset"
            app.button(key=second_key).click().run()
            assert not app.exception
            assert app.button(key=second_key).label == "Queuing…"
            assert app.button(key=second_key).disabled
            assert engine.task_activity("character_references", second) == "queued"
            release_first.set()
            assert started_second.wait(10)
            app.run()
            assert not app.exception
            assert app.button(key=first_key).label == "Generate"
            assert app.button(key=second_key).label == "Generating…"
            assert app.button(key=second_key).disabled
        finally:
            release_first.set()
            release_second.set()
        result = future.result(timeout=10)
    app.run()
    assert not app.exception
    assert app.button(key=second_key).label == "Generate"
    assert len(calls) == 2
    assert all(result.records["character_references:" + cid].status == "succeeded" for cid in (first, second))


def test_finished_asset_does_not_keep_batch_owners_generating_label():
    app = AppTest.from_string('''
from concurrent.futures import Future
import streamlit as st
from story_channel.generation_ui import generation_button
st.session_state["workspace-task"] = {"future": Future(), "generation_button_key": "asset"}
generation_button("Generate", key="asset", activity=st.session_state.get("activity", "queued"))
''').run()
    assert app.button(key="asset").label == "Queuing…"
    assert app.button(key="asset").disabled
    app.session_state["activity"] = "running"
    app.run()
    assert app.button(key="asset").label == "Generating…"
    app.session_state["activity"] = "finished"
    app.run()
    assert app.button(key="asset").label == "Generate"
