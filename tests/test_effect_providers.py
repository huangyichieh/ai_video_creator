import json
from pathlib import Path

import httpx
import pytest

from story_channel.config import Settings
from story_channel.effect_providers import SyncLipSyncProvider, ElevenLabsSFXProvider
from story_channel.llm import ProviderError, TransientProviderError
from story_channel.models import ProviderConfig
from story_channel.projects import ProjectManager
from story_channel.request_tracking import tracked_call, request_rows


def project(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "data"))
    outline = tmp_path / "outline.md"
    outline.write_text("A keeper protects a lighthouse", encoding="utf-8")
    return manager, manager.create("Effects", outline).project_id


def test_sync_upload_poll_download_and_request_ids(tmp_path, monkeypatch):
    manager, pid = project(tmp_path)
    monkeypatch.setenv("SYNC_API_KEY", "private-sync-key")
    video, audio = tmp_path / "video.mp4", tmp_path / "dialogue.wav"
    video.write_bytes(b"video-upload")
    audio.write_bytes(b"dialogue-upload")
    sent, updates = [], []
    def handler(request):
        sent.append(request)
        if request.method == "POST":
            assert request.headers["x-api-key"] == "private-sync-key"
            assert request.headers["Idempotency-Key"] == "stable-operation-key"
            data = request.read()
            assert b"video-upload" in data and b"dialogue-upload" in data
            assert b"lipsync-2" in data and b"coordinates" in data and b"cut_off" in data
            return httpx.Response(201, json={"id": "sync-job-1", "status": "PENDING"}, headers={"request-id": "req_submit"})
        if request.url.host == "api.sync.so":
            if request.url.path.endswith("/download"):
                return httpx.Response(200, text="https://cdn.example/synced.mp4")
            return httpx.Response(200, json={"id": "sync-job-1", "status": "COMPLETED", "outputDuration": 3,
                                          "outputUrl": "https://cdn.example/synced.mp4"}, headers={"x-request-id": "req_poll"})
        assert "x-api-key" not in request.headers and "xi-api-key" not in request.headers
        return httpx.Response(200, content=b"synced-video")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = SyncLipSyncProvider(client)
        config = ProviderConfig(provider="sync", model="lipsync-2", options={"output_path": str(tmp_path / "synced.mp4"),
            "duration": 3, "poll_interval": 0, "idempotency_key": "stable-operation-key",
            "face_target": {"auto_detect": False, "frame_number": 0, "coordinates": [50, 60]},
            "job_update": lambda *args: updates.append(args)})
        asset = tracked_call(manager, pid, "finishing:lipsync", provider.generate, video, audio, config)
        assert Path(asset.file_path).read_bytes() == b"synced-video"
        assert provider.last_usage["video_seconds"] == 3
        assert set(provider.last_usage["request_ids"]) == {"req_submit", "req_poll"}
        assert updates[0][0] == "sync-job-1" and updates[-1][1] == "COMPLETED"
    log = (manager.project_path(pid) / "requests.jsonl").read_text()
    assert "private-sync-key" not in log and "dialogue-upload" not in log
    assert all(row["story_project_id"] == pid for row in request_rows(manager.project_path(pid) / "requests.jsonl"))


def test_sync_timeout_resumes_job_without_submission(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNC_API_KEY", "test-only")
    video, audio = tmp_path / "v.mp4", tmp_path / "a.wav"
    video.write_bytes(b"v")
    audio.write_bytes(b"a")
    posts = []
    ready = False
    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(201, json={"id": "saved-job", "status": "PENDING"})
        if request.url.host == "api.sync.so":
            if request.url.path.endswith("/download"):
                return httpx.Response(200, text="https://cdn.example/video.mp4")
            return httpx.Response(200, json={"id": "saved-job", "status": "COMPLETED" if ready else "PROCESSING",
                                          "outputUrl": "https://cdn.example/video.mp4"})
        return httpx.Response(200, content=b"complete")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = SyncLipSyncProvider(client)
        config = ProviderConfig(provider="sync", model="lipsync-2", options={"output_path": str(tmp_path / "out.mp4"),
            "duration": 2, "idempotency_key": "keep-this-key", "poll_timeout": 0})
        with pytest.raises(TransientProviderError, match="still processing"):
            provider.generate(video, audio, config)
        assert provider.last_usage["video_seconds"] == 2
        ready = True
        config.options["job_id"] = "saved-job"
        provider.generate(video, audio, config)
        assert len(posts) == 1 and provider.last_usage["video_seconds"] == 0


def test_elevenlabs_sound_request_and_billable_units(tmp_path, monkeypatch):
    manager, pid = project(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-eleven-key")
    def handler(request):
        assert request.url.path == "/v1/sound-generation"
        assert request.headers["xi-api-key"] == "private-eleven-key"
        data = json.loads(request.content)
        assert data == {"text": "Rain against a window", "model_id": "eleven_text_to_sound_v2", "duration_seconds": 4,
                        "loop": True, "prompt_influence": .9}
        assert request.url.params["output_format"] == "mp3_44100_128"
        return httpx.Response(200, content=b"sound-data", headers={"character-cost": "40", "request-id": "req_sfx"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = ElevenLabsSFXProvider(client)
        config = ProviderConfig(provider="elevenlabs", model="eleven_text_to_sound_v2",
                                options={"output_path": str(tmp_path / "sound.mp3"), "duration": 4, "loop": True})
        tracked_call(manager, pid, "finishing:sfx", provider.generate, "Rain against a window", config)
        assert provider.last_usage["billing_units"] == 40 and provider.last_usage["audio_seconds"] == 4
        assert provider.last_usage["request_ids"] == ["req_sfx"]
    assert "private-eleven-key" not in (manager.project_path(pid) / "requests.jsonl").read_text()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_sound_errors_do_not_expose_response_or_credentials(tmp_path, monkeypatch, status):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private upstream text"))) as client:
        provider = ElevenLabsSFXProvider(client)
        config = ProviderConfig(provider="elevenlabs", model="eleven_text_to_sound_v2",
                                options={"duration": 1, "output_path": str(tmp_path / "sfx.mp3")})
        with pytest.raises(ProviderError) as error:
            provider.generate("test", config)
        assert str(status) in str(error.value) and "private upstream" not in str(error.value)
        assert provider.last_usage == {}
