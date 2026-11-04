from pathlib import Path
from threading import Event

from streamlit.testing.v1 import AppTest

from story_channel.assets import AssetPipeline
from story_channel.asset_models import AssetRecord
from story_channel.media_providers import DemoAlignmentProvider
from story_channel.subtitles_ui import subtitle_progress
from story_channel.media_utils import timestamp
from test_assets_ui import prepare


def voices_ready(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    from test_animations_workflow import ready_assets
    renderer, director = ready_assets(tmp_path)
    manager = renderer.manager
    project = manager.load(renderer.project_id)
    engine = renderer.assets
    engine.generate("scene_images")
    director.approve_plan()
    director.approve_performance()
    engine.generate("voices")
    return manager, project, engine


def test_subtitle_progress_includes_pending_failed_and_stale_tracks(tmp_path):
    manager, project = prepare(tmp_path)
    engine = AssetPipeline(manager, project.project_id)
    tracks, scenes = subtitle_progress(engine, engine.inspect())
    assert len(tracks) == len(scenes) == 3
    assert all(row["Status"] == "Waiting for voice" for row in tracks)
    assert all(row["Subtitle files"] == "Pending" for row in scenes)
    manager, project, engine = voices_ready(tmp_path / "ready")
    state = engine.inspect()
    tracks, scenes = subtitle_progress(engine, state)
    assert all(row["Status"] == "Ready" for row in tracks)
    subject = tracks[0]["Track"]
    state.records[f"subtitles:{subject}"] = AssetRecord(stage="subtitles", subject=subject, status="failed", error="Synthetic alignment failure")
    tracks, scenes = subtitle_progress(engine, state)
    assert tracks[0]["Status"] == "Failed"
    assert tracks[0]["Error"] == "Synthetic alignment failure"
    assert scenes[0]["Status"] == "Failed"
    state = engine.generate("subtitles")
    tracks, scenes = subtitle_progress(engine, state)
    assert all(row["Status"] == "Completed" for row in tracks)
    assert all(row["Subtitle files"] == "Available" for row in scenes)
    record = next(r for r in state.records.values() if r.stage == "subtitles")
    engine.local_path(record.asset.file_path).unlink()
    tracks, scenes = subtitle_progress(engine, state)
    assert any(row["Status"] == "Outdated" for row in tracks)
    assert any(row["Subtitle files"] == "Outdated" for row in scenes)


def test_live_subtitle_metrics_and_scene_exports(tmp_path, monkeypatch):
    manager, project, engine = voices_ready(tmp_path)
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    release = Event()
    original = DemoAlignmentProvider.align

    def delayed(self, *args, **kwargs):
        assert release.wait(timeout=20)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(DemoAlignmentProvider, "align", delayed)
    app = AppTest.from_file(str(Path(__file__).parent / "legacy_component_app.py"), default_timeout=30).run()
    app.button(key="nav-final").click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Subtitle tracks").value == "3"
    assert next(m for m in app.metric if m.label == "Completed").value == "0"
    task = None
    try:
        app.button(key="nav-animations").click().run()
        app.button(key=f"animation-input-next-{project.project_id}").click().run()
        task = app.session_state["workspace-task"]
        app.button(key="nav-final").click().run()
        assert not app.exception
        assert next(m for m in app.metric if m.label == "Generating").value == "3"
        assert any("subtitle" in status.label.lower() for status in app.status)
        release.set()
        task["future"].result(timeout=20)
        app.run()
        assert not app.exception
        assert next(m for m in app.metric if m.label == "Completed").value == "3"
        assert next(m for m in app.metric if m.label == "Generating").value == "0"
        scene_table = next(df.value for df in app.dataframe if "Subtitle files" in df.value.columns)
        assert scene_table["Subtitle files"].tolist() == ["Available"] * 3
        cursor = 0.0
        for scene in engine.load().scenes.values():
            label = f"{scene.scene_id} · {timestamp(cursor, vtt=True)} – {timestamp(cursor + scene.duration, vtt=True)}"
            expander = next(e for e in app.expander if e.label == label)
            assert not expander.proto.expanded
            assert app.get("download_button")
            assert any(button.key == f"download-scene-{scene.vtt}" for button in app.get("download_button"))
            cursor += scene.duration
        assert not any("WEBVTT" in code.value for code in app.code)
    finally:
        release.set()
        if task:
            task["future"].result(timeout=20)
