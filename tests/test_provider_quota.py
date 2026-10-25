from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from story_channel import provider_quota
from story_channel.api_keys_ui import API_KEYS


@pytest.mark.parametrize("key,payload,remaining", [
    ("RUNWAYML_API_SECRET", {"creditBalance": 42.75}, "42.75"),
    ("ELEVENLABS_API_KEY", {"character_count": 250, "character_limit": 1000,
                            "next_character_count_reset_unix": 1800000000}, "750"),
    ("ELEVENLABS_API_KEY", {"character_count": 1100, "character_limit": 1000}, "0"),
])
def test_quota_read_only_request(monkeypatch, key, payload, remaining):
    def get(self, url, *, headers):
        assert self.timeout.read == 10
        assert not self.follow_redirects
        if key == "RUNWAYML_API_SECRET":
            assert url == "https://api.dev.runwayml.com/v1/organization"
            assert headers == {"Authorization": "Bearer fake-secret", "X-Runway-Version": "2024-11-06"}
        else:
            assert url == "https://api.elevenlabs.io/v1/user/subscription"
            assert headers == {"xi-api-key": "fake-secret"}
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(httpx.Client, "get", get)
    quota = provider_quota.fetch_quota(key, "fake-secret")
    assert quota.remaining == Decimal(remaining)
    assert quota.checked_at.tzinfo == timezone.utc
    if "next_character_count_reset_unix" in payload:
        assert quota.resets_at.timestamp() == 1800000000


@pytest.mark.parametrize("status,payload", [
    (401, {"error": "fake-secret"}), (403, {}), (429, {}), (500, {}),
    (302, {}), (200, {}), (200, {"creditBalance": "fake-secret"}),
    (200, {"creditBalance": "NaN"}),
])
def test_quota_failures_are_safe(monkeypatch, status, payload):
    monkeypatch.setattr(httpx.Client, "get", lambda *a, **kw: httpx.Response(status, json=payload))
    with pytest.raises(ValueError) as error:
        provider_quota.fetch_quota("RUNWAYML_API_SECRET", "fake-secret")
    assert "fake-secret" not in str(error.value)


def test_quota_transport_error_is_safe(monkeypatch):
    def get(*args, **kwargs):
        raise httpx.ConnectError("fake-secret")
    monkeypatch.setattr(httpx.Client, "get", get)
    with pytest.raises(ValueError, match="Could not reach"):
        provider_quota.fetch_quota("RUNWAYML_API_SECRET", "fake-secret")


def test_dialog_quota_uses_saved_key_and_invalidates_old_balance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in API_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("RUNWAYML_API_SECRET", "old-fake-secret")
    calls = []
    def fetch(key, secret):
        calls.append((key, secret))
        return provider_quota.Quota(Decimal("42.75"), "credits", datetime.now(timezone.utc))
    monkeypatch.setattr(provider_quota, "fetch_quota", fetch)
    app = AppTest.from_string("from story_channel.api_keys_ui import api_key_button\napi_key_button()").run()
    app.button(key="open-api-keys").click().run()
    assert calls == [("RUNWAYML_API_SECRET", "old-fake-secret")]
    assert app.button(key="refresh-quota-ELEVENLABS_API_KEY").disabled
    app.text_input(key="api-key-draft-RUNWAYML_API_SECRET").set_value("replacement-fake-secret").run()
    assert len(calls) == 1  # Editing does not repeat the automatic checks.
    app.button(key="refresh-quota-RUNWAYML_API_SECRET").click().run()
    assert calls == [("RUNWAYML_API_SECRET", "old-fake-secret")] * 2
    assert app.metric[0].value == "42.75"
    app.button(key="save-api-keys").click().run()
    app.button(key="open-api-keys").click().run()
    assert app.metric[0].value == "42.75"
    assert len(calls) == 3  # Reopening refreshes using the replacement key.
    app.button(key="refresh-quota-RUNWAYML_API_SECRET").click().run()
    assert calls[-1] == ("RUNWAYML_API_SECRET", "replacement-fake-secret")
    assert not app.exception


def test_openai_spending_reads_every_cost_page_without_limit_endpoint(monkeypatch):
    requests = []
    def get(self, url, *, headers, params=None):
        assert headers == {"Authorization": "Bearer fake-admin"}
        requests.append((url, dict(params) if params else None))
        assert url == "https://api.openai.com/v1/organization/costs"
        start = datetime.fromtimestamp(params["start_time"], timezone.utc)
        assert (start.day, start.hour, start.minute, start.second) == (1, 0, 0, 0)
        assert "project_ids" not in params
        second = params.get("page") == "page2"
        return httpx.Response(200, json={"data": [{"results": [{"amount": {
            "value": "9.25" if second else "6.50", "currency": "usd"}}]}],
            "has_more": not second, "next_page": None if second else "page2"})
    monkeypatch.setattr(httpx.Client, "get", get)
    quota = provider_quota.fetch_quota("OPENAI_ADMIN_KEY", "fake-admin")
    assert quota.limit is None
    assert quota.used == Decimal("15.75")
    assert quota.remaining is None
    assert quota.unit == "USD"
    assert len(requests) == 2


def test_openai_spending_never_publishes_partial_costs(monkeypatch):
    def get(self, url, **kwargs):
        assert url.endswith("/costs")
        return httpx.Response(200, json={"data": [], "has_more": True, "next_page": "repeated"})
    monkeypatch.setattr(httpx.Client, "get", get)
    with pytest.raises(ValueError, match="unreadable"):
        provider_quota.fetch_quota("OPENAI_ADMIN_KEY", "fake-admin")


def test_open_dialog_checks_every_saved_provider_and_isolates_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in API_KEYS:
        monkeypatch.setenv(key, "fake-credential")
    calls = []
    def fetch(key, secret):
        calls.append(key)
        now = datetime.now(timezone.utc)
        if key == "RUNWAYML_API_SECRET":
            raise ValueError("Quota unavailable: access denied")
        if key == "OPENAI_ADMIN_KEY":
            return provider_quota.Quota(None, "USD", now, used=Decimal("19.88"))
        return provider_quota.Quota(Decimal(750), "characters", now, Decimal(250), Decimal(1000))
    monkeypatch.setattr(provider_quota, "fetch_quota", fetch)
    app = AppTest.from_string("from story_channel.api_keys_ui import api_key_button\napi_key_button()").run()
    app.button(key="open-api-keys").click().run()
    assert set(calls) == set(API_KEYS) - {"OPENAI_API_KEY"}
    assert {metric.value for metric in app.metric} == {"19.88", "250", "750"}
    assert any("access denied" in item.value for item in app.warning)
    assert next(metric for metric in app.metric if metric.label == "OpenAI reported spending (USD)").value == "19.88"
    assert not any("Fallback monthly budget" in item.label for item in app.text_input)
    app.text_input(key="api-key-draft-OPENAI_API_KEY").set_value("new-fake-key").run()
    assert len(calls) == 3
    assert not app.exception


