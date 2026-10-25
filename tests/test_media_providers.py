import base64
import io
import json

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from story_channel.media_providers import OpenAIAlignmentProvider, OpenAIImageProvider, OpenAITTSProvider
from story_channel.media_utils import silence, wav_duration
from story_channel.models import ProviderConfig
from story_channel.llm import ProviderError, TransientProviderError
from story_channel.providers import AlignmentProvider, ImageProvider, TTSProvider


def test_media_adapters_use_real_sdk_with_mock_http(tmp_path):
    image = io.BytesIO()
    Image.new("RGB", (64, 64), "blue").save(image, "PNG")
    requests = []

    def handler(request):
        requests.append(request)
        if "/images/" in request.url.path:
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(image.getvalue()).decode()}],
                                            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}})
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=silence(1), headers={"content-type": "audio/wav"})
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"task": "transcribe", "language": "english", "duration": 1, "text": "Hello",
                       "segments": [{"id": 0, "seek": 0, "start": .1, "end": .9, "text": "Hello", "tokens": [],
                                     "temperature": 0, "avg_logprob": 0, "compression_ratio": 1, "no_speech_prob": 0}],
                       "words": [{"start": .1, "end": .9, "word": "Hello"}]})
        raise AssertionError(request.url.path)

    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        images = OpenAIImageProvider(client)
        assert isinstance(images, ImageProvider)
        config = ProviderConfig(provider="openai", model="gpt-image-1", options={"output_path": str(tmp_path / "image.png")})
        result = images.generate("A lighthouse", [], config)
        assert images.last_usage["image_count"] == 1
        config.options["output_path"] = str(tmp_path / "scene.png")
        images.generate("Use this reference", [tmp_path / "image.png"], config)
        assert requests[0].url.path.endswith("images/generations")
        assert requests[1].url.path.endswith("images/edits")
        speech = OpenAITTSProvider(client)
        assert isinstance(speech, TTSProvider)
        audio = speech.generate("Hello", "alloy", ProviderConfig(provider="openai", model="gpt-4o-mini-tts",
                                 options={"output_path": str(tmp_path / "voice.wav"), "style_prompt": "Quiet and mysterious", "generic_prompt": "Fairy tale",
                                          "modification": {"prompt": "Slow down and sound hopeful"}}))
        assert audio.duration == 1
        assert json.loads(requests[2].content)["voice"] == "alloy"
        assert "Quiet and mysterious" in json.loads(requests[2].content)["instructions"]
        assert "Fairy tale" in json.loads(requests[2].content)["instructions"]
        assert "Slow down and sound hopeful" in json.loads(requests[2].content)["instructions"]
        assert json.loads(requests[2].content)["input"] == "Hello"
        alignment = OpenAIAlignmentProvider(client)
        assert isinstance(alignment, AlignmentProvider)
        timed = alignment.align(tmp_path / "voice.wav", "Hello", "en-US", ProviderConfig(provider="openai", model="whisper-1"))
        assert timed.segments[0].start == .1
        assert timed.words[0].end == .9
        assert b"verbose_json" in requests[3].content
        assert b"timestamp_granularities" in requests[3].content


@pytest.mark.parametrize("status,kind", [(401, ProviderError), (429, TransientProviderError), (500, TransientProviderError)])
def test_media_error_is_sanitized(tmp_path, status, kind):
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, json={"error": {"message": "private upstream content", "type": "api_error"}})))) as client:
        with pytest.raises(kind) as error:
            OpenAIImageProvider(client).generate("image", [], ProviderConfig(provider="openai", model="gpt-image-1", options={"output_path": str(tmp_path / "image.png")}))
        assert "private" not in str(error.value)


def test_media_quota_is_not_retried(tmp_path):
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(429, json={"error": {"type": "insufficient_quota", "code": "insufficient_quota"}})))) as client:
        with pytest.raises(ProviderError) as error:
            OpenAIImageProvider(client).generate("image", [], ProviderConfig(provider="openai", model="gpt-image-1", options={"output_path": str(tmp_path / "image.png")}))
        assert not isinstance(error.value, TransientProviderError)


def test_image_bad_request_reports_safe_code_parameter_and_request_id(tmp_path):
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(400, headers={"x-request-id": "req_diagnostic1234"}, json={"error": {
            "message": "private prompt and secret content", "code": "unsupported_parameter", "param": "output_format"}})))) as client:
        with pytest.raises(ProviderError) as caught:
            OpenAIImageProvider(client).generate("private prompt", [], ProviderConfig(provider="openai", model="gpt-image-1",
                options={"output_path": str(tmp_path / "image.png")}))
        text = str(caught.value)
        assert "unsupported_parameter" in text and "output_format" in text and "req_diagnostic1234" in text
        assert "private" not in text and "secret" not in text
