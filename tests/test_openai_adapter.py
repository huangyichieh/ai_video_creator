import json

import httpx
import pytest
from openai import OpenAI

from story_channel.creative import Story
from story_channel.llm import OpenAILLMProvider, ProviderError, TransientProviderError, strict_schema
from story_channel.models import ProviderConfig
from story_channel.modules import MODULES


def response_payload():
    story = dict(title="A test", targetDurationSec=120, **{key: key for key in ("hook", "setup", "conflict", "escalation", "twist", "climax", "ending")})
    return {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed", "model": "test-model",
            "output": [{"id": "msg_test", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(story), "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
                      "input_tokens_details": {"cached_tokens": 20}, "output_tokens_details": {"reasoning_tokens": 0}}}


def test_real_sdk_structured_output_with_mock_http():
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response_payload())

    with OpenAI(api_key="test-only", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handle))) as client:
        adapter = OpenAILLMProvider(client=client)
        result = adapter.generate("test prompt", Story, ProviderConfig(provider="openai", model="test-model"))
        assert result.ending == "ending"
        assert adapter.last_usage == {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 20}
        assert requests[0]["text"]["format"]["type"] == "json_schema"
        assert requests[0]["text"]["format"]["strict"] is True
        assert requests[0]["store"] is False


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_failures_are_sanitized_and_retryable(status):
    transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"error": {"message": "sensitive upstream text", "type": "api_error"}}))
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=transport)) as client:
        adapter = OpenAILLMProvider(client=client)
        expected = ProviderError if status == 401 else TransientProviderError
        with pytest.raises(expected) as error:
            adapter.generate("test", Story, ProviderConfig(provider="openai", model="test-model"))
        assert "sensitive" not in str(error.value)
        assert adapter.last_usage == {}


@pytest.mark.parametrize("incomplete", [True, False])
def test_refusal_and_incomplete_responses_retain_usage(incomplete):
    payload = response_payload()
    if incomplete:
        payload["status"] = "incomplete"
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
        payload["output"] = []
    else:
        payload["output"][0]["content"] = [{"type": "refusal", "refusal": "Cannot comply"}]
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))) as client:
        adapter = OpenAILLMProvider(client=client)
        message = "token limit" if incomplete else "incomplete response or refusal"
        with pytest.raises(ProviderError, match=message):
            adapter.generate("test", Story, ProviderConfig(provider="openai", model="test-model"))
        assert adapter.last_usage["input_tokens"] == 100


def test_truncated_completed_json_is_retryable_and_retains_usage():
    payload = response_payload()
    payload["output"][0]["content"][0]["text"] = '{"title":"incomplete'
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))) as client:
        adapter = OpenAILLMProvider(client=client)
        with pytest.raises(TransientProviderError, match="invalid or truncated JSON") as error:
            adapter.generate("test", Story, ProviderConfig(provider="openai", model="test-model"))
        assert 'incomplete\'' not in str(error.value)
        assert adapter.last_usage["output_tokens"] == 50


def test_missing_api_key_is_actionable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAILLMProvider().generate("test", Story, ProviderConfig(provider="openai", model="test-model"))


@pytest.mark.parametrize("code,kind", [
    ("insufficient_quota", "insufficient_quota"),
    ("credit_balance_exhausted", "insufficient_quota"),
    ("organization_spend_limit_exceeded", "insufficient_quota"),
    ("project_spend_limit_exceeded", "insufficient_quota"),
    ("organization_usage_limit_exceeded", "insufficient_quota"),
    (None, "insufficient_quota"),
])
def test_billing_429_is_actionable_and_not_retried(code, kind):
    transport = httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {
        "code": code, "type": kind, "message": "sensitive upstream text"}}))
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=transport)) as client:
        with pytest.raises(ProviderError) as error:
            OpenAILLMProvider(client=client).generate("test", Story, ProviderConfig(provider="openai", model="test-model"))
        assert not isinstance(error.value, TransientProviderError)
        assert "Retrying will not fix" in str(error.value)
        assert "sensitive" not in str(error.value)


def test_temporary_rate_limit_remains_retryable():
    transport = httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {
        "code": "rate_limit_exceeded", "type": "rate_limit_error", "message": "sensitive upstream text"}}))
    with OpenAI(api_key="test-only", max_retries=0, http_client=httpx.Client(transport=transport)) as client:
        with pytest.raises(TransientProviderError, match="Wait before retrying"):
            OpenAILLMProvider(client=client).generate("test", Story, ProviderConfig(provider="openai", model="test-model"))


@pytest.mark.parametrize("module", list(MODULES.values()))
def test_all_creative_schemas_support_sdk_strict_conversion(module):
    schema = strict_schema(module.model)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema.get("$defs", {}).values():
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
