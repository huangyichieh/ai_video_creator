from decimal import Decimal

import pytest

from story_channel.config import Settings
from story_channel.cost_estimates import estimate_usage, project_cost_estimate
from story_channel.costs import CostTracker
from story_channel.models import UsageRecord
from story_channel.projects import ProjectManager


@pytest.mark.parametrize("provider,model,units,expected", [
    ("openai", "gpt-6.1-sol", {"input_tokens": 1000, "output_tokens": 1000}, ".012"),
    ("runway", "gen4_image", {"image_count": 2}, ".16"),
    ("runway", "klingO3_pro", {"video_seconds": 5}, ".85"),
    ("runway", "act_two", {"video_seconds": 5}, ".25"),
    ("elevenlabs", "eleven_v4", {"audio_characters": 1000}, ".08"),
    ("elevenlabs", "eleven_text_to_sound_v2", {"audio_seconds": 5}, ".05"),
    ("openai", "whisper-1", {"audio_seconds": 60}, ".006"),
    ("demo", "gen4_image", {"image_count": 2}, "0"),
])
def test_fallback_rates(provider, model, units, expected):
    record = UsageRecord(provider=provider, model=model, step="test", pricing_known=False, **units)
    assert estimate_usage(record, Settings())[0] == Decimal(expected)


def test_configured_and_recorded_costs_take_priority():
    record = UsageRecord(provider="runway", model="gen4_image", step="image", image_count=2, pricing_known=False)
    assert estimate_usage(record, Settings(runway_image_cost_per_image=0))[0] == 0
    assert estimate_usage(record, Settings(runway_image_cost_per_image=".5"))[0] == 1
    record.cost = Decimal(".75")
    assert estimate_usage(record, Settings())[0] == Decimal(".75")


def test_streaming_wav_header_does_not_inflate_estimates(tmp_path):
    from story_channel.media_utils import silence, wav_duration
    raw = bytearray(silence(1))
    raw[4:8] = (4294967295).to_bytes(4, "little")
    raw[40:44] = (4294967295).to_bytes(4, "little")
    audio = tmp_path / "streamed.wav"
    audio.write_bytes(raw)
    assert wav_duration(audio) == 1
    record = UsageRecord(provider="openai", model="whisper-1", step="assets:subtitles", audio_seconds=89478.48529166667, pricing_known=False)
    assert estimate_usage(record, Settings())[0] is None


def test_historical_usage_failures_job_dedup_and_project_isolation(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "data"))
    source = tmp_path / "outline.md"
    source.write_text("A story", encoding="utf-8")
    first, other = manager.create("One", source), manager.create("Two", source)
    tracker = CostTracker(manager, first.project_id)
    tracker.record(UsageRecord(provider="openai", model="gpt-6.1-sol", step="review", output_tokens=1000, pricing_known=False, outcome="failed"))
    for seconds in (5, 5, 0):
        tracker.record(UsageRecord(provider="runway", model="klingO3_pro", step="video", video_seconds=seconds, provider_job_id="same-task", pricing_known=False))
    tracker.record(UsageRecord(provider="elevenlabs", model="eleven_v4", step="preview", audio_characters=1000, pricing_known=False))
    tracker.record(UsageRecord(provider="openai", model="gpt-6.1-sol", step="unmetered", pricing_known=False))
    before = tracker.path.read_bytes()
    total, entries = project_cost_estimate(manager, first.project_id)
    assert total == Decimal(".94")
    assert sum(entry["amount"] is None for entry in entries) == 1
    assert project_cost_estimate(manager, other.project_id)[0] == 0
    assert tracker.path.read_bytes() == before


def test_project_metric_and_analytics_show_all_provider_estimates(tmp_path, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    manager = ProjectManager(Settings(data_root=tmp_path / "data"))
    source = tmp_path / "outline.md"
    source.write_text("A story", encoding="utf-8")
    project = manager.create("Cost test", source)
    for provider, model, units in [("runway", "gen4_image", {"image_count": 2}), ("elevenlabs", "eleven_v4", {"audio_characters": 1000})]:
        CostTracker(manager, project.project_id).record(UsageRecord(provider=provider, model=model, step="test", pricing_known=False, **units))
    monkeypatch.setenv("STORY_DATA_ROOT", str(manager.root))
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run()
    assert not app.exception
    assert next(item.value for item in app.metric if item.label == "Project billed cost (USD)") == "$0.24"
    app.button(key="nav-analytics").click().run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if "Estimated USD" in item.value.columns)
    assert set(table["Estimated USD"]) == {"0.16", "0.08"}
