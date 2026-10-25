import json

from story_channel.config import Settings


def test_local_env_loads_key_and_settings_without_serializing_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("STORY_LLM_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=local-test-key\nSTORY_LLM_MODEL=local-model\n", encoding="utf-8-sig")
    settings = Settings.load()
    import os
    assert os.environ["OPENAI_API_KEY"] == "local-test-key"
    assert settings.llm_model == "local-model"
    assert "local-test-key" not in settings.model_dump_json()


def test_existing_environment_overrides_local_file_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("STORY_LLM_MODEL", "environment-model")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=file-key\nSTORY_LLM_MODEL=file-model\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"llmModel": "json-model"}), encoding="utf-8")
    settings = Settings.load(config)
    import os
    assert os.environ["OPENAI_API_KEY"] == "environment-key"
    assert settings.llm_model == "environment-model"


def test_no_parent_env_discovery_or_value_interpolation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("STORY_LLM_MODEL", "test-model")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=parent-key\n", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    Settings.load()
    import os
    assert "OPENAI_API_KEY" not in os.environ
    (child / ".env").write_text("OPENAI_API_KEY=literal-${VALUE}\n", encoding="utf-8")
    Settings.load()
    assert os.environ["OPENAI_API_KEY"] == "literal-${VALUE}"
