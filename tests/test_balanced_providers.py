import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from story_channel.balanced_providers import KlingVideoProvider, ActTwoProvider, RunwayImageProvider, ElevenLabsTTSProvider
from story_channel.config import Settings
from story_channel.models import ProviderConfig
from story_channel.llm import ProviderError, TransientProviderError
from story_channel.media_utils import silence, wav_duration
from test_assets import assets


def reference(tmp_path):
    path = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "blue").save(path)
    return path


def test_balanced_defaults():
    settings = Settings()
    assert settings.llm_model == "gpt-6.1-sol"
    assert (settings.image_provider, settings.image_model) == ("runway", "gen4_image")
    assert (settings.tts_provider, settings.tts_model) == ("elevenlabs", "eleven_v4")
    assert settings.narrator_voice == ""
    assert settings.runway_video_cost_per_second is None


@pytest.mark.parametrize("kind", ["kling", "act_two", "image"])
def test_runway_requests_upload_without_auth_to_storage_and_resume(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-runway-secret")
    ref = reference(tmp_path)
    calls, submitted = [], []
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(image, "PNG")
    def handle(request):
        calls.append(request)
        if request.url.host in ("storage.example", "cdn.example"):
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=image.getvalue() if kind == "image" else b"video-output")
        assert request.headers["Authorization"] == "Bearer test-runway-secret"
        assert request.headers["X-Runway-Version"] == "2024-11-06"
        if request.url.path == "/v1/uploads":
            return httpx.Response(200, json={"uploadUrl": "https://storage.example/upload", "fields": {"policy": "test"}, "runwayUri": "runway://uploaded-media"})
        if request.method == "POST":
            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "task-123"})
        return httpx.Response(200, json={"id": "task-123", "status": "SUCCEEDED", "output": ["https://cdn.example/output"]})
    provider = {"kling": KlingVideoProvider, "act_two": ActTwoProvider, "image": RunwayImageProvider}[kind](httpx.Client(transport=httpx.MockTransport(handle)))
    config = ProviderConfig(provider="runway", model={"kling": "klingO3_pro", "act_two": "act_two", "image": "gen4_image"}[kind],
        options={"output_path": str(tmp_path / ("out.png" if kind == "image" else "out.mp4")), "seconds": 8, "size": "1280x720", "performance_video": str(ref)})
    if kind == "kling":
        config.options["last_frame"] = str(ref)
    method = provider.generate if kind == "image" else provider.generate_clip
    asset = method("A character opens a heavy door.", [ref], config)
    assert asset.provider == "runway" and asset.generation_params["jobId"] == "task-123"
    unit = "image_count" if kind == "image" else "video_seconds"
    assert provider.last_usage[unit] == (1 if kind == "image" else 8)
    method("A character opens a heavy door.", [ref], config)
    assert len(submitted) == 1 and provider.last_usage[unit] == 0
    body = submitted[0]
    if kind == "kling":
        assert body["model"] == "klingO3_pro" and body["audio"] is False
        assert [item["position"] for item in body["promptImage"]] == ["first", "last"]
    elif kind == "act_two":
        assert body["reference"]["type"] == "video" and body["bodyControl"] is True
    else:
        assert body["referenceImages"] and body["model"] == "gen4_image"


def test_runway_unknown_submission_is_not_automatically_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    def handle(request):
        raise httpx.ReadTimeout("simulated", request=request)
    provider = KlingVideoProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    config = ProviderConfig(provider="runway", model="klingO3_pro", options={"output_path": str(tmp_path / "clip.mp4")})
    with pytest.raises(ProviderError, match="outcome unknown") as error:
        provider.task("/image_to_video", {}, config, "video_seconds", 8)
    assert not isinstance(error.value, TransientProviderError)


def test_eleven_v4_fixed_voice_emotion_and_wav_conversion(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-eleven-secret")
    calls = []
    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model_id"] == "eleven_v4" and body["text"].startswith("[sad]")
        assert request.url.path.endswith("/fixed-voice-123")
        assert request.headers["xi-api-key"] == "test-eleven-secret"
        return httpx.Response(200, content=silence(4))
    provider = ElevenLabsTTSProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    config = ProviderConfig(provider="elevenlabs", model="eleven_v4", options={"output_path": str(tmp_path / "voice.wav"), "performance": {"emotion": "sad", "pace": .9}})
    asset = provider.generate("We will find our way.", "fixed-voice-123", config)
    assert asset.provider == "elevenlabs" and wav_duration(Path(asset.file_path)) == pytest.approx(4)
    with pytest.raises(ProviderError, match="explicit ElevenLabs Voice ID"):
        provider.generate("Do not guess the voice.", "alloy", config)
    assert len(calls) == 1


def test_act_two_driving_upload_is_shot_scoped_and_invalidates_approval(assets, tmp_path):
    from story_channel.animation import AnimationDirector
    from story_channel.ffmpeg_renderer import FFmpeg
    from story_channel.render_models import RenderOptions
    director = AnimationDirector(assets.manager, assets.project_id)
    state = director.prepare()
    shot = state.plan.shots[0]
    shot.camera = "Important dialogue close-up, face clearly visible"
    director.save_plan(state.plan)
    before = director.load()
    clip = tmp_path / "performance.mp4"
    FFmpeg().visual(reference(tmp_path), clip, 4, RenderOptions(width=320, height=180, fps=12))
    state = director.import_performance(shot.shot_id, clip.read_bytes(), expected=before.plan.model_dump_json(by_alias=True))
    saved = state.plan.shots[0]
    assert saved.performance_video and saved.performance_character_id == "keeper"
    assert all(not s.performance_video for s in state.plan.shots[1:])
    digest = director.direction_hash(state)
    assets.local_path(saved.performance_video).write_bytes(b"changed")
    assert director.direction_hash(state) != digest
    state = director.import_performance(shot.shot_id, None, expected=state.plan.model_dump_json(by_alias=True))
    assert not state.plan.shots[0].performance_video


def test_asset_manual_retry_resumes_runway_image_job_without_second_charge(assets, monkeypatch):
    from decimal import Decimal
    from story_channel.costs import CostTracker
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    monkeypatch.setattr("story_channel.provider_prompts.compile_prompt", lambda pipeline, text, **_: "A consistent character reference pack")
    assets.demo = False
    assets.settings.llm_max_retries = 0
    assets.settings.runway_image_cost_per_image = Decimal(".08")
    submitted, failed = [], []
    output = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(output, "PNG")
    def handle(request):
        if request.url.host == "cdn.example":
            return httpx.Response(200, content=output.getvalue())
        if request.method == "POST":
            submitted.append(request)
            return httpx.Response(200, json={"id": "saved-image-job"})
        if not failed:
            failed.append(True)
            raise httpx.ReadTimeout("retry saved task", request=request)
        return httpx.Response(200, json={"status": "SUCCEEDED", "output": ["https://cdn.example/image"]})
    assets.image_provider = RunwayImageProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(ProviderError):
        assets.generate("character_references")
    record = next(r for r in assets.load().records.values() if r.stage == "character_references")
    assert record.remote_job_id == "saved-image-job"
    assets.generate("character_references")
    assert len(submitted) == 1
    costs = [r for r in CostTracker(assets.manager, assets.project_id).records() if r.step == "assets:character_references"]
    assert sum(r.cost for r in costs) == Decimal(".08")
    assert [r.image_count for r in costs] == [1, 0]
