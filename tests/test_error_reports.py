import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.effect_providers import MediaHTTP
from story_channel.balanced_providers import RunwayMedia
from story_channel.error_reports import record_failure, reports, clear_reports
from story_channel.llm import ProviderError
from story_channel.models import ProviderConfig
from story_channel.request_tracking import tracked_call
from test_request_tracking import create
from test_studio import world


@pytest.fixture
def project(tmp_path):
    manager = ProjectManager(Settings(data_root=tmp_path / "data"))
    return manager, create(manager, tmp_path, "Reports")


def test_failure_responses_accumulate_redacted_and_success_clears_only_its_task(project, monkeypatch):
    manager, project = project
    path = manager.project_path(project.project_id)
    monkeypatch.setenv("RUNWAYML_API_SECRET", "current-sensitive-secret")
    mode = [400]
    def handle(request):
        return httpx.Response(mode[0], json={"error": "Bad input", "issues": [{"path": ["referenceImages"], "message": "Invalid image"}],
            "api_key": "current-sensitive-secret", "echo": "Bearer injected-sensitive-secret"}, headers={"x-request-id": "request-123"})
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(handle)))
    for _ in range(2):
        with pytest.raises(ProviderError):
            tracked_call(manager, project.project_id, "assets:character_references", provider._request,
                         "POST", "https://provider.example/generate", headers={"Authorization": "Bearer injected-sensitive-secret"},
                         task_key="assets:character_references:keeper")
    record_failure(path, "assets:character_references:guest", ValueError("Different failed character"))
    saved = reports(path, "assets:character_references:keeper")
    assert len(saved) == 2
    response = saved[0]["responses"][0]
    assert response["status_code"] == 400 and response["request_id"] == "request-123"
    assert response["body"]["issues"][0]["path"] == ["referenceImages"]
    stored = "".join(file.read_text() for file in (path / "error_reports").glob("*.json"))
    assert "current-sensitive-secret" not in stored and "injected-sensitive-secret" not in stored
    mode[0] = 200
    tracked_call(manager, project.project_id, "assets:character_references", provider._request,
                 "POST", "https://provider.example/generate", task_key="assets:character_references:keeper")
    assert not reports(path, "assets:character_references:keeper")
    assert len(reports(path, "assets:character_references:guest")) == 1


def test_runway_http_200_failed_task_retains_actual_provider_json(project, tmp_path, monkeypatch):
    manager, project = project
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-key")
    body = {"id": "job-1", "status": "FAILED", "failureCode": "INTERNAL.BAD_OUTPUT.CODE01", "failure": "An unexpected error occurred."}
    provider = RunwayMedia(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))))
    config = ProviderConfig(provider="runway", model="gen4_image", options={"job_id": "job-1", "output_path": str(tmp_path / "image.png")})
    with pytest.raises(ProviderError):
        tracked_call(manager, project.project_id, "assets:character_references", provider.task,
                     "/text_to_image", {}, config, "image_count", 1, task_key="assets:character_references:keeper")
    saved = reports(manager.project_path(project.project_id))
    assert saved[0]["responses"][0]["body"] == body
    assert saved[0]["responses"][0]["status_code"] == 200


def test_validation_failure_and_network_timeout_are_reported(project):
    manager, project = project
    path = manager.project_path(project.project_id)
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": "truncated JSON {"}))))
    def parse():
        response = provider._request("POST", "https://provider.example/generate")
        return json.loads(response.json()["output"])
    with pytest.raises(ValueError):
        tracked_call(manager, project.project_id, "review", parse)
    assert reports(path, "review")[0]["responses"][0]["body"]["output"] == "truncated JSON {"
    def timeout(request):
        raise httpx.ReadTimeout("Timeout", request=request)
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(timeout)))
    with pytest.raises(ProviderError):
        tracked_call(manager, project.project_id, "story", provider._request, "POST", "https://provider.example/generate")
    assert reports(path, "story")[0]["responses"][0]["event"] == "no_response"


def test_parallel_tasks_do_not_clear_other_task_reports(project):
    manager, project = project
    path = manager.project_path(project.project_id)
    def call(task):
        def operation():
            if task == "failed":
                raise ValueError("Failure")
            return "Done"
        try:
            return tracked_call(manager, project.project_id, "assets:scene_images", operation, task_key=task)
        except ValueError:
            return "Failed"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(call, ["failed", "succeeded", "failed", "succeeded"]))
    assert len(reports(path, "failed")) == 2
    assert not reports(path, "succeeded")


def test_application_validation_keeps_response_and_previous_reports_until_success(project):
    manager, project = project
    path = manager.project_path(project.project_id)
    record_failure(path, "story", ValueError("Earlier failure"))
    provider = MediaHTTP(httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, json={"story": "Contradicts the approved world"}))))
    def reject(response):
        raise ValueError("Story contradicts the world")
    with pytest.raises(ValueError, match="contradicts"):
        tracked_call(manager, project.project_id, "story", provider._request,
                     "POST", "https://provider.example/story", validate_result=reject)
    entries = reports(path, "story")
    assert len(entries) == 2
    assert entries[0]["responses"][0]["body"]["story"] == "Contradicts the approved world"
    tracked_call(manager, project.project_id, "story", provider._request,
                 "POST", "https://provider.example/story", validate_result=lambda response: response)
    assert not reports(path, "story")


@pytest.mark.parametrize("storage_error", [OSError("Disk unavailable"), ValueError("Corrupt report")])
def test_report_storage_failure_does_not_mask_provider_result_or_error(project, monkeypatch, storage_error):
    import story_channel.error_reports as storage
    manager, project = project
    def broken(*args, **kwargs):
        raise storage_error
    monkeypatch.setattr(storage, "record_failure", broken)
    monkeypatch.setattr(storage, "clear_reports", broken)
    original = ProviderError("Original provider failure")
    def failed():
        raise original
    with pytest.raises(ProviderError) as caught:
        tracked_call(manager, project.project_id, "story", failed)
    assert caught.value is original
    assert tracked_call(manager, project.project_id, "story", lambda: "Success") == "Success"


def test_error_report_dialog_opens_from_existing_dialog_and_returns_to_it(project):
    manager, project = project
    path = manager.project_path(project.project_id)
    record_failure(path, "story", ValueError("Generation failed"), [{"status_code": 400, "body": {"message": "Invalid prompt"}}])
    app = AppTest.from_string('''
import streamlit as st
from story_channel.error_report_ui import render_error, show_error_report
st.session_state["generation-context-project"] = "project"
@st.dialog("Original editor")
def editor():
    st.write("Original dialog content")
    render_error("Generation failed", project_path=st.session_state["project_path"], task_key="story", key="report")
if st.session_state.get("active-stage-browser"):
    editor()
show_error_report()
''')
    app.session_state["project_path"] = str(path)
    app.session_state["active-stage-browser"] = (project.project_id, "story")
    app.run()
    app.button(key="report").click().run()
    assert not app.exception
    assert any("Invalid prompt" in element.value for element in app.json)
    app.button(key="close-error-report").click().run()
    assert not app.exception
    assert any(element.value == "Original dialog content" for element in app.markdown)
    clear_reports(path, "story")
    assert not list((path / "error_reports").glob("*.json"))


def test_asset_error_button_opens_saved_response_and_success_removes_reports(world, monkeypatch):
    from pathlib import Path
    from story_channel.assets import AssetPipeline
    from story_channel.media_providers import DemoImageProvider
    world.generate_characters()
    cid = world.pipeline.artifact("characters").characters[0].character_id
    class FailedImage(MediaHTTP):
        def generate(self, prompt, references, config):
            self._request("POST", "https://provider.example/images", json={})
    provider = FailedImage(httpx.Client(transport=httpx.MockTransport(lambda request:
        httpx.Response(400, json={"error": "Invalid character input", "field": "referenceImages"}))))
    engine = AssetPipeline(world.manager, world.project_id, demo=True, image_provider=provider)
    with pytest.raises(ProviderError):
        engine.generate("character_references", subject=cid)
    task = "assets:character_references:" + cid
    assert reports(engine.path, task)
    monkeypatch.setenv("STORY_DATA_ROOT", str(world.manager.root))
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_ASSET_PROVIDER", "demo")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=20).run()
    app.button(key="nav-assets").click().run()
    assert not app.exception
    next(button for button in app.button if button.label == "Error Report").click().run()
    assert not app.exception
    assert any("Invalid character input" in element.value for element in app.json)
    app.button(key="close-error-report").click().run()
    engine.image_provider = DemoImageProvider()
    engine.generate("character_references", subject=cid)
    assert not reports(engine.path, task)
    app.run()
    assert not app.exception
    assert not any(button.label == "Error Report" for button in app.button)
