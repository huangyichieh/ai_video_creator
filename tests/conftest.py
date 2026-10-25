"""Keep background billing in UI tests away from credentials in the local .env."""
import pytest


@pytest.fixture(autouse=True)
def disable_live_billing(monkeypatch):
    # Billing tests explicitly install their own fake key and mocked transport.
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "")
