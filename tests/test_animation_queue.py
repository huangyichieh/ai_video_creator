from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from story_channel.animation_queue import AnimationQueue
from story_channel.animations_workflow import animation_snapshot, generate_animations
from test_animations_workflow import ready_assets


def test_queue_replaced_on_every_run_and_pipeline_lists_upcoming_input_tasks(tmp_path):
    engine, director = ready_assets(tmp_path)
    queue = AnimationQueue(engine)
    action = animation_snapshot(engine)[0]
    first = queue.start(action, pipeline=False)
    assert queue.rows() and all(row["Status"] == "Queued" for row in queue.rows())
    generate_animations(engine, queue_run_id=first)
    assert all(row["Status"] == "Completed" for row in queue.rows())
    director.approve_plan()
    director.approve_performance()
    action = animation_snapshot(engine)[0]
    second = queue.start(action, pipeline=True)
    assert second != first
    assert {item["stage"] for item in queue.load()["items"]} == {"voices", "subtitles", "consistency"}
    assert all(row["Status"] == "Queued" for row in queue.rows())
    # An older worker cannot put previous-run results back into the new list.
    AnimationQueue(engine, first).finish("failed", "Old run")
    assert queue.load()["run_id"] == second and queue.load()["status"] == "queued"
    generate_animations(engine, pipeline=True, queue_run_id=second)
    assert all(row["Status"] in ("Completed", "Reused") for row in queue.rows())
    assert queue.load()["status"] == "completed"


def test_active_task_is_generating_others_wait_and_stop_preserves_results(tmp_path, monkeypatch):
    engine, _ = ready_assets(tmp_path)
    action = animation_snapshot(engine)[0]
    queue = AnimationQueue(engine)
    token = queue.start(action, pipeline=False)
    entered, release, stop = Event(), Event(), Event()
    original = engine.assets.image_provider.generate

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(20)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine.assets.image_provider, "generate", delayed)
    engine.assets.max_tasks = 1
    engine.stop_requested = engine.assets.stop_requested = stop.is_set
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generate_animations, engine, queue_run_id=token)
        try:
            assert entered.wait(20)
            rows = queue.rows()
            assert sum(row["Status"] == "Generating…" for row in rows) == 1
            assert sum(row["Status"] == "Queued" for row in rows) == len(rows) - 1
            stop.set()
        finally:
            release.set()
        future.result(timeout=20)
    rows = queue.rows()
    assert sum(row["Status"] == "Completed" for row in rows) == 1
    assert all(row["Status"] in ("Completed", "Stopped") for row in rows)
    assert queue.load()["status"] == "stopped"


def test_failure_marks_only_failed_task_and_retry_clears_old_errors(tmp_path, monkeypatch):
    engine, _ = ready_assets(tmp_path)
    action = animation_snapshot(engine)[0]
    queue = AnimationQueue(engine)
    token = queue.start(action, pipeline=False)
    def fail(*args, **kwargs):
        raise RuntimeError("Test provider failure")
    monkeypatch.setattr(engine.assets.image_provider, "generate", fail)
    with pytest.raises(RuntimeError):
        generate_animations(engine, queue_run_id=token)
    assert queue.load()["status"] == "failed"
    assert any(row["Status"] == "Failed" and row["Error"] for row in queue.rows())
    action = animation_snapshot(engine)[0]
    new_token = queue.start(action, pipeline=False)
    assert new_token != token
    queue.stage_started(action.stage)
    assert all(row["Status"] == "Queued" and not row["Error"] for row in queue.rows())


def test_waiting_table_shows_live_run_and_replaces_it_for_next_pipeline(tmp_path, monkeypatch):
    from story_channel.media_providers import DemoImageProvider
    from test_animations_workflow import open_animations
    from test_ui import finish_background
    engine, director = ready_assets(tmp_path)
    entered, release = Event(), Event()
    original = DemoImageProvider.generate
    def delayed(self, *args, **kwargs):
        entered.set()
        assert release.wait(20)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(DemoImageProvider, "generate", delayed)
    app = open_animations(engine, monkeypatch)
    app.button(key="nav-animations").click().run()
    try:
        app.button(key=f"animation-workflow-next-{engine.project_id}").click().run()
        assert entered.wait(20) and not app.exception
        app.run()  # Refresh after the worker enters; the click can render while still queued.
        first = AnimationQueue(engine).load()["run_id"]
        table = next(frame.value for frame in app.dataframe if "Order" in frame.value.columns)
        assert "Generating…" in set(table["Status"])
        assert set(table["Stage"]) == {"Scene images"}
        release.set()
        finish_background(app)
        director.approve_plan()
        director.approve_performance()
        app.run()
        app.button(key=f"animation-workflow-run-{engine.project_id}").click().run()
        finish_background(app)
        assert not app.exception
        assert AnimationQueue(engine).load()["run_id"] != first
        table = next(frame.value for frame in app.dataframe if "Order" in frame.value.columns)
        assert set(table["Stage"]) == {"Voice tracks", "Subtitle alignment", "Scene-input QA"}
        assert "Scene images" not in set(table["Stage"])
    finally:
        release.set()
