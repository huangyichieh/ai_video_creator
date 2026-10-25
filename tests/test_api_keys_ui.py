from pathlib import Path

import os
import pytest
from dotenv import dotenv_values
from streamlit.testing.v1 import AppTest
from streamlit.proto.TextInput_pb2 import TextInput

from story_channel.api_keys_ui import API_KEYS, save_api_keys, masked_key


def test_saved_key_preview_shows_only_ends():
    assert masked_key("1234-middle-secret-5678") == "1234…5678"
    assert "short" not in masked_key("short")
    assert "12345678" not in masked_key("12345678")


def test_save_keys_preserves_other_settings_and_updates_environment(tmp_path, monkeypatch):
    for key in API_KEYS:
        monkeypatch.setenv(key, "old-test-value")
    path = tmp_path / ".env"
    path.write_text("# Keep this comment\nSTORY_LLM_PROVIDER=demo\nOPENAI_API_KEY='old-test-value'\n", encoding="utf-8")
    changes = {key: "test-'quoted-$value-" + str(i) for i, key in enumerate(API_KEYS)}
    save_api_keys(path, changes)
    loaded = dotenv_values(path, interpolate=False)
    assert all(loaded[key] == value == os.environ[key] for key, value in changes.items())
    assert loaded["STORY_LLM_PROVIDER"] == "demo"
    assert "# Keep this comment" in path.read_text(encoding="utf-8")
    save_api_keys(path, {"ELEVENLABS_API_KEY": ""})
    assert dotenv_values(path)["ELEVENLABS_API_KEY"] == os.environ["ELEVENLABS_API_KEY"] == ""
    before = path.read_bytes()
    with pytest.raises(ValueError):
        save_api_keys(path, {"OPENAI_API_KEY": "bad\nvalue"})
    assert path.read_bytes() == before


def test_key_dialog_masks_keeps_cancels_and_saves_keys(tmp_path, monkeypatch):
    def unavailable(*args):
        raise ValueError("Quota unavailable in this test")
    monkeypatch.setattr("story_channel.provider_quota.fetch_quota", unavailable)
    monkeypatch.chdir(tmp_path)
    for key in API_KEYS:
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("STORY_LLM_PROVIDER", "demo")
    monkeypatch.setenv("STORY_DATA_ROOT", str(tmp_path / "channel"))
    monkeypatch.setenv("OPENAI_API_KEY", "existing-test-key")
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY='existing-test-key'\nOTHER_SETTING=keep\n", encoding="utf-8")
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py")).run()
    app.button(key="open-api-keys").click().run()
    assert not app.exception
    assert "SYNC_API_KEY" not in {item.label for item in app.text_input}
    assert app.button(key="open-api-keys").proto.help == "API key"
    assert any(item.value == "Current key: exis…-key" for item in app.text)
    assert not any("existing-test-key" in item.value for item in app.text)
    assert sum(item.value == ":red[API key is not provided.]" for item in app.markdown) == 3
    assert all(app.text_input(key=f"api-key-draft-{key}").value == "" for key in API_KEYS)
    assert all(app.text_input(key=f"api-key-draft-{key}").proto.type == TextInput.PASSWORD for key in API_KEYS)
    app.text_input(key="api-key-draft-OPENAI_API_KEY").set_value("discarded-test-key")
    app.button(key="cancel-api-keys").click().run()
    assert dotenv_values(path)["OPENAI_API_KEY"] == "existing-test-key"
    app.button(key="open-api-keys").click().run()
    app.text_input(key="api-key-draft-ELEVENLABS_API_KEY").set_value("new-test-eleven-key")
    app.button(key="save-api-keys").click().run()
    assert not app.exception
    assert dotenv_values(path)["ELEVENLABS_API_KEY"] == os.environ["ELEVENLABS_API_KEY"] == "new-test-eleven-key"
    assert dotenv_values(path)["OPENAI_API_KEY"] == "existing-test-key"
    assert dotenv_values(path)["OTHER_SETTING"] == "keep"
    app.button(key="open-api-keys").click().run()
    assert sum(item.value == ":red[API key is not provided.]" for item in app.markdown) == 2
    app.checkbox(key="api-key-clear-OPENAI_API_KEY").check()
    app.button(key="save-api-keys").click().run()
    app.button(key="open-api-keys").click().run()
    assert sum(item.value == ":red[API key is not provided.]" for item in app.markdown) == 3
