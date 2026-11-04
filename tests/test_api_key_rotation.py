import httpx
import openai
import pytest

from story_channel.api_keys_ui import save_api_keys
from story_channel.creative import Story
from story_channel.llm import OpenAILLMProvider, ProviderError
from story_channel.media_providers import OpenAIMedia
from story_channel.models import ProviderConfig
from test_openai_adapter import response_payload


@pytest.mark.parametrize("kind", ["text", "media"])
def test_cached_clients_use_replacements_and_stop_after_key_is_cleared(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("OPENAI_API_KEY", "generation-old")
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "admin-only")
    seen = []
    def handle(request):
        seen.append(request.headers["Authorization"])
        payload = response_payload() if kind == "text" else {"object": "list", "data": []}
        return httpx.Response(200, json=payload)
    constructor = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: constructor(
        http_client=httpx.Client(transport=httpx.MockTransport(handle)), **kwargs))
    adapter = OpenAILLMProvider() if kind == "text" else OpenAIMedia()
    def request():
        if kind == "text":
            return adapter.generate("Test", Story, ProviderConfig(provider="openai", model="test-model"))
        return adapter._request(lambda client: client.models.list())
    try:
        request()
        original_client = adapter.client
        save_api_keys(tmp_path / ".env", {"OPENAI_API_KEY": "generation-new"})
        request()
        assert adapter.client is original_client
        assert seen == ["Bearer generation-old", "Bearer generation-new"]
        save_api_keys(tmp_path / ".env", {"OPENAI_API_KEY": ""})
        with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
            request()
        assert len(seen) == 2
    finally:
        adapter.client.close()


def test_injected_client_credentials_are_preserved(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-environment-key")
    seen = []
    def handle(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"object": "list", "data": []})
    with openai.OpenAI(api_key="injected-key", http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        OpenAIMedia(client=client)._request(lambda active: active.models.list())
    assert seen == ["Bearer injected-key"]
