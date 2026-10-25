from pathlib import Path

import httpx
import pytest

from story_channel.animation import AnimationDirector
from story_channel.costs import CostTracker
from story_channel.identity import IdentityManager
from story_channel.llm import ProviderError
from story_channel.request_tracking import request_rows
from story_channel.voice_samples import saved_voice_sample
from test_assets_ui import prepare
from test_voice_validation import install_lookup, project


@pytest.mark.parametrize("narrator", [False, True])
def test_provider_sample_is_read_only_and_attributed_to_saved_voice(project, monkeypatch, narrator):
    manager, project_id = project
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/voices/saved-id"
        return httpx.Response(200, json={"voice_id": "saved-id", "name": "Saved voice",
                                       "preview_url": "https://storage.googleapis.com/example.mp3"})

    install_lookup(monkeypatch, handler)
    speaker = "narrator" if narrator else "husband_01"
    sample = saved_voice_sample(manager, project_id, speaker, "saved-id", demo=False, narrator=narrator)
    assert sample["source"] == "https://storage.googleapis.com/example.mp3"
    assert sample["voice_id"] == "saved-id"
    assert len(calls) == 1 and calls[0].method == "GET"
    assert request_rows(manager.project_path(project_id) / "requests.jsonl")[0]["step"] == f"voice_sample:{speaker}"
    assert not CostTracker(manager, project_id).records()


@pytest.mark.parametrize("url", [None, "http://example.com/a.mp3", "file:///private.mp3", "javascript:alert(1)"])
def test_unavailable_or_invalid_preview_never_generates_audio(project, monkeypatch, url):
    manager, project_id = project
    install_lookup(monkeypatch, lambda _: httpx.Response(200, json={"voice_id": "saved-id", "preview_url": url}))
    with pytest.raises(ProviderError, match="No existing sample"):
        saved_voice_sample(manager, project_id, "narrator", "saved-id", demo=False, narrator=True)


def test_demo_reuses_only_current_intact_saved_audition(tmp_path, monkeypatch):
    manager, manifest = prepare(tmp_path)
    identity = IdentityManager(manager, manifest.project_id)
    library = identity.prepare()
    expected = library.model_dump_json(by_alias=True)
    cid = next(iter(library.voices))
    library.voices[cid].voice_id = "nova"
    identity.save(library, expected=expected)
    director = AnimationDirector(manager, manifest.project_id)
    director.prepare()
    director.preview_voice(cid, "Keep the beacon burning. We will find our way through this storm together, one step at a time.")

    def unexpected(*args, **kwargs):
        pytest.fail("Demo playback must not call a provider")

    monkeypatch.setattr("story_channel.voice_samples.lookup_elevenlabs_voice", unexpected)
    sample = saved_voice_sample(manager, manifest.project_id, cid, "nova", demo=True)
    assert Path(sample["source"]).exists() and sample["kind"] == "Offline demo placeholder"
    with pytest.raises(ProviderError, match="No existing sample"):
        saved_voice_sample(manager, manifest.project_id, cid, "echo", demo=True)
    Path(sample["source"]).write_bytes(b"changed")
    with pytest.raises(ProviderError, match="No existing sample"):
        saved_voice_sample(manager, manifest.project_id, cid, "nova", demo=True)
