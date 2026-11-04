from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.library_media import LibraryMedia
from story_channel.locking import project_lock
from story_channel.models import UsageRecord
from story_channel.service_limits import LIMITS, activity, service_slot
from test_studio import world


SCRIPT = '''
import streamlit as st
from story_channel.ui_tasks import launch, collect, generation_busy, workspace_tasks
from story_channel.generation_ui import generation_button
from story_channel.service_limits import service_slot
collect()
gates, started = st.session_state["gates"], st.session_state["started"]
def start(name, service):
    def run():
        with service_slot(service):
            started[name].set()
            assert gates[name].wait(15)
    launch(run, name, concurrent_safe=True, queue_enabled=True, service=service,
           resource=name, stop_event=st.session_state["stops"][name], rerun_on_start=False)
for name, service in [("openai-1","openai"),("openai-2","openai"),("runway-1","runway"),
                      ("eleven-1","elevenlabs"),("eleven-2","elevenlabs"),("eleven-3","elevenlabs")]:
    generation_button("Generate " + name,key=name,disabled=generation_busy(),on_click=start,args=(name,service))
'''


def test_ui_runs_all_three_services_with_2_1_2_limits_and_queues_excess():
    names = ["openai-1", "openai-2", "runway-1", "eleven-1", "eleven-2", "eleven-3"]
    gates, started, stops = ({name: Event() for name in names} for _ in range(3))
    app = AppTest.from_string(SCRIPT)
    for key, value in {"gates": gates, "started": started, "stops": stops, "generation-context-project": "one-project"}.items():
        app.session_state[key] = value
    app.run()
    tasks = []
    try:
        for name in names:
            app.button(key=name).click().run()
        assert not app.exception
        assert all(started[name].wait(3) for name in names[:5])
        assert {name: row["active"] for name, row in activity().items()} == {"openai": 2, "runway": 1, "elevenlabs": 2}
        assert not started["eleven-3"].is_set()
        assert app.button(key="eleven-3").label == "Queuing…"
        tasks = [app.session_state["workspace-task"], *app.session_state["workspace-parallel-tasks"]]
        gates["openai-1"].set()
        tasks[0]["future"].result(timeout=3)
        app.run()
        # An idle OpenAI slot must not start excess ElevenLabs requests.
        assert not started["eleven-3"].is_set()
        gates["eleven-1"].set()
        next(task for task in tasks if task["label"] == "eleven-1")["future"].result(timeout=3)
        app.run()
        assert started["eleven-3"].wait(3)
    finally:
        for gate in gates.values():
            gate.set()
        for task in [app.session_state["workspace-task"], *app.session_state["workspace-parallel-tasks"]]:
            task["future"].result(timeout=5)
        app.run()
    assert not app.exception
    assert not any(row["active"] for row in activity().values())


@pytest.mark.parametrize("same_character", [False, True])
def test_parallel_voice_samples_merge_revisions_and_preserve_all_usage(world, same_character):
    world.generate_characters()
    world.generate_characters("Guest")
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    identity = IdentityManager(world.manager, world.project_id)
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    for profile in library.voices.values():
        profile.voice_id = "nova"
    identity.save(library, expected=expected)
    characters = list(library.voices)
    ids = [characters[0], characters[0] if same_character else characters[1]]
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    original = engine.tts_provider.generate
    barrier = Barrier(2)
    def generate(*args):
        with service_slot("elevenlabs"):
            barrier.wait(timeout=5)
            return original(*args)
    engine.tts_provider.generate = generate
    before = len(CostTracker(world.manager, world.project_id).records())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(media.talking_sample, cid, "A sample", emotion, video=False)
                   for cid, emotion in zip(ids, ("neutral", "happy"))]
        results = [future.result(timeout=10) for future in futures]
    saved = media.load()
    if same_character:
        assert sorted(result["revision"] for result in results) == [1, 2]
        assert len(saved["samples"][ids[0]]) == 2
    else:
        assert all(len(saved["samples"][cid]) == 1 for cid in ids)
    assert len(CostTracker(world.manager, world.project_id).records()) == before + 2


def test_voice_api_can_start_while_an_asset_writer_holds_the_project_lock(world):
    world.generate_characters()
    engine = AssetPipeline(world.manager, world.project_id, demo=True)
    identity = IdentityManager(world.manager, world.project_id)
    library = identity.load()
    expected = library.model_dump_json(by_alias=True)
    cid = next(iter(library.voices))
    library.voices[cid].voice_id = "nova"
    identity.save(library, expected=expected)
    media = LibraryMedia(world.manager, world.project_id)
    media.assets = engine
    started = Event()
    original = engine.tts_provider.generate
    def generate(*args):
        started.set()
        return original(*args)
    engine.tts_provider.generate = generate
    with ThreadPoolExecutor(max_workers=1) as pool:
        with project_lock(engine.path):
            future = pool.submit(media.talking_sample, cid, "A neutral sample", video=False)
            assert started.wait(5)
            assert future.result(timeout=5)["status"] == "succeeded"
        assert future.result(timeout=5)["status"] == "succeeded"


def test_parallel_cost_writes_and_stale_manifest_save_keep_all_records(world):
    manager, pid = world.manager, world.project_id
    stale = manager.load(pid)
    before = CostTracker(manager, pid).records()
    def record(index):
        CostTracker(manager, pid).record(UsageRecord(provider="openai", model="test", step=f"parallel:{index}",
            cost=Decimal("0.01"), estimated_cost=Decimal("0.01")))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(30)))
    stale.title = "Updated title"
    manager.save(stale)
    records = CostTracker(manager, pid).records()
    assert len(records) == len(before) + 30
    assert manager.load(pid).actual_cost == sum((row.cost for row in records), Decimal(0))
    assert manager.load(pid).title == "Updated title"


def test_same_character_requests_wait_and_stop_cancels_the_pending_request():
    names = ["openai-1", "openai-2", "runway-1", "eleven-1", "eleven-2", "eleven-3"]
    gates, started, stops = ({name: Event() for name in names} for _ in range(3))
    script = SCRIPT.replace("resource=name", 'resource="same-character"') + '''
from story_channel.ui_tasks import stop_tasks
st.button("Stop",key="stop-all",on_click=stop_tasks)
'''
    app = AppTest.from_string(script)
    for key, value in {"gates": gates, "started": started, "stops": stops}.items():
        app.session_state[key] = value
    app.run()
    app.button(key="eleven-1").click().run()
    task = app.session_state["workspace-task"]
    try:
        assert started["eleven-1"].wait(3)
        app.button(key="eleven-2").click().run()
        waiting = app.session_state["workspace-task-queue"][0]
        assert not started["eleven-2"].is_set()
        app.button(key="stop-all").click().run()
        assert waiting["future"].cancelled()
        assert stops["eleven-1"].is_set()
    finally:
        for gate in gates.values():
            gate.set()
        task["future"].result(timeout=5)
        app.run()
    assert not started["eleven-2"].is_set()
    assert not app.exception


def test_collecting_simultaneous_results_preserves_each_jobs_error_for_its_owner():
    app = AppTest.from_string('''
import streamlit as st
from concurrent.futures import Future
from story_channel.ui_tasks import collect, take_task_result
failed, succeeded = Future(), Future()
failed.set_exception(RuntimeError("Voice request failed"))
succeeded.set_result("image")
def job(future,key):
    return {"future":future,"label":key,"success":None,"generation_button_key":key,"generation_button_project":"p"}
st.session_state["workspace-task"] = job(failed,"voice")
st.session_state["workspace-parallel-tasks"] = [job(succeeded,"image")]
collect()
assert st.session_state["workspace-task-message"][0] == "success"
assert take_task_result("p",["voice"])[0] == ("error","Voice request failed")
assert take_task_result("p",["image"])[0][0] == "success"
''').run()
    assert not app.exception
