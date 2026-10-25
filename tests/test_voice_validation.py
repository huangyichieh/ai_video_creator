import httpx
import pytest

from story_channel.animation import AnimationDirector
from story_channel.assets import AssetPipeline
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.llm import ProviderError
from story_channel.projects import ProjectManager
from story_channel.request_tracking import request_rows
from story_channel.voice_validation import validate_provider_voice
from test_assets_ui import prepare


@pytest.fixture
def project(tmp_path, monkeypatch):
    manager = ProjectManager(Settings(data_root=tmp_path / "data", llm_provider="demo"))
    outline = tmp_path / "outline.md"
    outline.write_text("A story", encoding="utf-8")
    manifest = manager.create("Voice validation", outline)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-test-key")
    return manager, manifest.project_id


def install_lookup(monkeypatch, handler):
    original_client = httpx.Client
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-test-key")
    monkeypatch.setattr("story_channel.voice_validation.Client",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))


def test_read_only_lookup_checks_exact_id_and_records_request_attribution(project):
    manager, project_id = project
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["xi-api-key"] == "private-test-key"
        assert request.url.path == "/v1/voices/voice-123"
        return httpx.Response(200, json={"voice_id": "voice-123", "name": "A voice"},
                              headers={"x-request-id": "req_voice_lookup"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert validate_provider_voice(manager, project_id, "voice-123", speaker="husband_01", demo=False, client=client) == "A voice"
    assert len(requests) == 1
    rows = request_rows(manager.project_path(project_id) / "requests.jsonl")
    assert rows[0]["request_id"] == "req_voice_lookup"
    assert rows[0]["story_project_id"] == project_id and rows[0]["step"] == "voice_validation:husband_01"
    assert "private-test-key" not in (manager.project_path(project_id) / "requests.jsonl").read_text()
    assert not CostTracker(manager, project_id).records()


@pytest.mark.parametrize("status, message", [(404, "not found"), (401, "API key"), (403, "read permissions"),
                                           (429, "rate limited"), (500, "HTTP 500"), (422, "HTTP 422"), (302, "HTTP 302")])
def test_lookup_errors_do_not_echo_private_response(project, status, message):
    manager, project_id = project
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private-test-key upstream body"))) as client:
        with pytest.raises(ProviderError, match=message) as error:
            validate_provider_voice(manager, project_id, "bad-voice", speaker="wife_01", demo=False, client=client)
    assert "private-test-key" not in str(error.value)
    assert "not changed" in str(error.value)


@pytest.mark.parametrize("body", ["not-json", '[]', '{"voice_id":"another-voice"}', '{}'])
def test_invalid_success_response_cannot_verify_voice(project, body):
    manager, project_id = project
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))) as client:
        with pytest.raises(ProviderError, match="invalid voice lookup response"):
            validate_provider_voice(manager, project_id, "voice-123", speaker="husband_01", demo=False, client=client)


def test_missing_key_and_timeout_block_validation(project, monkeypatch):
    manager, project_id = project
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    with pytest.raises(ProviderError, match="Set ELEVENLABS_API_KEY"):
        validate_provider_voice(manager, project_id, "voice-123", speaker="husband_01", demo=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "private-test-key")

    def timeout(request):
        raise httpx.ReadTimeout("private-test-key upstream error", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="Could not reach ElevenLabs") as error:
            validate_provider_voice(manager, project_id, "voice-123", speaker="husband_01", demo=False, client=client)
    assert "private-test-key" not in str(error.value)
    assert request_rows(manager.project_path(project_id) / "requests.jsonl")[0]["event"] == "no_response"


def test_demo_and_other_providers_do_not_make_elevenlabs_requests(project):
    manager, project_id = project

    def unexpected(request):
        pytest.fail("No network request is expected")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        assert validate_provider_voice(manager, project_id, "demo-id", speaker="husband_01", demo=True, client=client) is None
        manager.settings.tts_provider = "openai"
        assert validate_provider_voice(manager, project_id, "nova", speaker="husband_01", demo=False, client=client) is None


@pytest.mark.parametrize("backend", ["identity", "animation", "narrator"])
@pytest.mark.parametrize("valid", [False, True])
def test_save_paths_verify_before_modifying_saved_data(tmp_path, monkeypatch, backend, valid):
    manager, manifest = prepare(tmp_path)
    project_id = manifest.project_id
    identity = IdentityManager(manager, project_id)
    if backend == "identity":
        library = identity.prepare()
        cid = next(iter(library.voices))
        expected = library.model_dump_json(by_alias=True)
        library.voices[cid].voice_id = "checked-voice"
        file = manager.project_path(project_id) / "identity_library.json"
        save = lambda: identity.save(library, expected=expected)
    elif backend == "animation":
        director = AnimationDirector(manager, project_id)
        state = director.prepare()
        cid = next(iter(state.plan.voices))
        state.plan.voices[cid].voice_id = "checked-voice"
        file = director.path / "animation.json"
        save = lambda: director.save_plan(state.plan)
    else:
        cid = "narrator"
        file = manager.project_path(project_id) / "production.json"
        save = lambda: assets.set_voice("narrator", "checked-voice")
    assets = AssetPipeline(manager, project_id, demo=False)
    assets.inspect()
    if backend == "animation":
        director.assets.demo = False
    baseline = file.read_bytes()
    calls = []

    def lookup(request):
        calls.append(request)
        return httpx.Response(200 if valid else 404, json={"voice_id": "checked-voice", "name": "Checked voice"})

    install_lookup(monkeypatch, lookup)
    if valid:
        save()
        assert file.read_bytes() != baseline
    else:
        with pytest.raises(ProviderError, match="not found"):
            save()
        assert file.read_bytes() == baseline
    assert len(calls) == 1 and calls[0].method == "GET"


def test_unchanged_voice_ids_do_not_trigger_lookup(tmp_path, monkeypatch):
    manager, manifest = prepare(tmp_path)
    identity = IdentityManager(manager, manifest.project_id)
    library = identity.prepare()
    cid = next(iter(library.voices))
    expected = library.model_dump_json(by_alias=True)
    library.voices[cid].voice_id = "already-saved-voice"
    library = identity.save(library, expected=expected)  # Offline setup.
    AssetPipeline(manager, manifest.project_id, demo=False).inspect()

    def unexpected(request):
        pytest.fail("Unchanged voice IDs do not need another lookup")

    install_lookup(monkeypatch, unexpected)
    expected = library.model_dump_json(by_alias=True)
    library.voices[cid].speaking_style = "gentle and measured"
    identity.save(library, expected=expected)
