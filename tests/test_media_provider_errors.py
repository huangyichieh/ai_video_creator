import io
import json

import httpx
import pytest
from PIL import Image

from story_channel.balanced_providers import RunwayImageProvider, RunwayMedia
from story_channel.effect_providers import MediaHTTP
from story_channel.llm import ProviderError, TransientProviderError
from story_channel.models import ProviderConfig


def test_runway_text_only_generation_omits_empty_references(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    image = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(image, "PNG")
    bodies = []
    def handle(request):
        if request.method == "POST":
            assert request.url.path == "/v1/text_to_image"
            body = json.loads(request.content)
            assert "referenceImages" not in body
            assert body["promptText"] == "一位穿藍色外套的角色"
            bodies.append(body)
            return httpx.Response(200, json={"id": "text-only-job"})
        if request.url.path.startswith("/v1/tasks/"):
            return httpx.Response(200, json={"status": "SUCCEEDED", "output": ["https://cdn.example/image.png"]})
        return httpx.Response(200, content=image.getvalue())
    provider = RunwayImageProvider(httpx.Client(transport=httpx.MockTransport(handle)))
    result = provider.generate("一位穿藍色外套的角色", [], ProviderConfig(provider="runway", model="gen4_image",
        options={"output_path": str(tmp_path / "result.png")}))
    assert bodies and result.generation_params["jobId"] == "text-only-job"


def test_http_validation_errors_show_field_details_without_credentials(monkeypatch):
    secret = "sensitive-secret-do-not-show"
    monkeypatch.setenv("RUNWAYML_API_SECRET", secret)
    def handle(request):
        return httpx.Response(400, json={"error": "Validation error", "issues": [
            {"path": ["referenceImages"], "message": "Must contain at least one image"},
            {"path": ["authorization"], "message": "Invalid Bearer " + secret}], "input": {"secret": secret}})
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(ProviderError) as error:
        provider._request("POST", "https://provider.example/generate")
    message = str(error.value)
    assert "HTTP 400" in message and "referenceImages: Must contain at least one image" in message
    assert secret not in message
    assert not isinstance(error.value, TransientProviderError)


def test_failed_runway_task_preserves_specific_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    def handle(request):
        return httpx.Response(200, json={"status": "FAILED", "failureCode": "INTERNAL.BAD_OUTPUT.CODE01",
                                         "failure": "An unexpected error occurred."})
    provider = RunwayMedia(httpx.Client(transport=httpx.MockTransport(handle)))
    destination = tmp_path / "result.png"
    updates = []
    config = ProviderConfig(provider="runway", model="gen4_image", options={"output_path": str(destination),
        "job_id": "existing-job", "job_update": lambda *args: updates.append(args)})
    with pytest.raises(ProviderError, match="INTERNAL.BAD_OUTPUT.CODE01") as error:
        provider.task("/text_to_image", {}, config, "image_count", 1)
    assert "existing-job" in str(error.value) and "unexpected error" in str(error.value)
    receipt = json.loads(destination.with_suffix(".png.task.json").read_text())
    assert receipt["status"] == "FAILED" and "BAD_OUTPUT" in receipt["failure"]
    assert updates[-1][1] == "failed"


@pytest.mark.parametrize("status", ["CANCELED", "CANCELLED"])
def test_runway_cancellation_updates_saved_job_status(tmp_path, monkeypatch, status):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    provider = RunwayMedia(httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"status": status}))))
    updates = []
    config = ProviderConfig(provider="runway", model="gen4_image", options={"output_path": str(tmp_path / "result.png"),
        "job_id": "existing-job", "job_update": lambda *args: updates.append(args)})
    with pytest.raises(ProviderError, match=status.lower()):
        provider.task("/text_to_image", {}, config, "image_count", 1)
    assert updates[-1][1] == "cancelled"


def test_non_json_provider_error_does_not_dump_response_body():
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(502, text="<html>private proxy diagnostics</html>"))))
    with pytest.raises(TransientProviderError) as error:
        provider._request("GET", "https://provider.example/result")
    assert "HTTP 502" in str(error.value) and "private proxy diagnostics" not in str(error.value)
