import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_channel.cli import main
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.files import input_hash
from story_channel.logging_config import configure_logging
from story_channel.models import AudioAsset, GeneratedAsset, Manifest, ProviderConfig, Scene, UsageRecord
from story_channel.projects import CHANNEL_DIRS, PROJECT_DIRS, ProjectManager
from story_channel.providers import ImageProvider, LLMProvider, LocalStorage, StorageProvider, TTSProvider, VideoProvider


@pytest.fixture
def project(tmp_path):
    outline = tmp_path / "outline.md"
    outline.write_text("# 故事大綱\n守燈人尋找失落的記憶。\n", encoding="utf-8-sig")
    manager = ProjectManager(Settings(data_root=tmp_path / "channel"))
    manifest = manager.create("最後的燈塔", outline, project_id="test-story")
    return manager, manifest, outline


def test_project_roundtrip_and_layout(project):
    manager, manifest, outline = project
    path = manager.project_path(manifest.project_id)
    assert all((manager.root / name).is_dir() for name in CHANNEL_DIRS)
    assert all((path / name).is_dir() for name in PROJECT_DIRS)
    assert manager.read_outline(manifest.project_id) == outline.read_text(encoding="utf-8-sig")
    assert manager.load(manifest.project_id) == manifest
    payload = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    assert payload["projectId"] == "test-story"
    assert payload["status"] == "draft"
    assert payload["approvedSteps"] == []
    manifest.current_step = "world"
    manager.save(manifest)
    assert manager.load(manifest.project_id).current_step == "world"
    before = (path / "manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        manager.create("collision", outline, project_id=manifest.project_id)
    assert (path / "manifest.json").read_bytes() == before


@pytest.mark.parametrize("project_id", ["../escape", "D:/escape", "a/b", "..", ""])
def test_project_path_rejects_traversal(tmp_path, project_id):
    with pytest.raises(ValueError):
        ProjectManager(Settings(data_root=tmp_path)).project_path(project_id)


def test_invalid_outline_creates_no_project(tmp_path):
    outline = tmp_path / "empty.md"
    outline.write_text(" \n", encoding="utf-8")
    manager = ProjectManager(Settings(data_root=tmp_path / "channel"))
    with pytest.raises(ValueError):
        manager.create("empty", outline)
    assert not manager.root.exists()


def test_corrupt_or_mismatched_manifest_is_rejected(project):
    manager, manifest, _ = project
    path = manager.project_path(manifest.project_id) / "manifest.json"
    data = manifest.model_dump(mode="json", by_alias=True)
    data["projectId"] = "different"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        manager.load(manifest.project_id)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationError):
        manager.load(manifest.project_id)


@pytest.mark.parametrize("fields", [{"target_duration_sec": 0}, {"actual_cost": -1},
                                     {"actual_cost": "NaN"}, {"title": " "},
                                     {"status": "unknown"}, {"created_at": "2026-01-01T00:00:00"},
                                     {"unknown": True}])
def test_manifest_validation(fields):
    with pytest.raises(ValidationError):
        Manifest(**({"title": "test"} | fields))


def test_shared_models_and_independent_defaults():
    first, second = Manifest(title="one"), Manifest(title="two")
    first.approved_steps.append("story")
    assert second.approved_steps == []
    scene = Scene(sceneId="s1", order=1, durationSec=5, locationId="l1")
    assert Scene.model_validate_json(scene.model_dump_json(by_alias=True)) == scene
    with pytest.raises(ValidationError):
        Scene(sceneId="s1", order=1, durationSec=-1, locationId="l1")
    with pytest.raises(ValidationError):
        GeneratedAsset(type="image", provider="fake", model="fake", promptHash="bad", filePath="a.png")


def test_cost_ledger_exact_totals_idempotency_and_recovery(project):
    manager, manifest, _ = project
    tracker = CostTracker(manager, manifest.project_id)
    one = UsageRecord(provider="fake", model="fake", step="world", input_tokens=100,
                      estimated_cost="0.2", cost="0.1")
    tracker.record(one)
    tracker.record(one)
    tracker.record(UsageRecord(provider="fake", model="fake", step="story", cost="0.2"))
    assert len(tracker.records()) == 2
    loaded = manager.load(manifest.project_id)
    assert loaded.actual_cost == Decimal("0.3")
    assert loaded.estimated_cost == Decimal("0.2")
    loaded.actual_cost = Decimal("0")
    manager.save(loaded)
    tracker.sync()
    assert manager.load(manifest.project_id).actual_cost == Decimal("0.3")
    with pytest.raises(ValueError, match="different data"):
        tracker.record(one.model_copy(update={"cost": Decimal("2")}))
    with pytest.raises(ValidationError):
        UsageRecord(provider="fake", model="fake", step="world", input_tokens=-1)


def test_settings_environment_overrides_aliases(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{"dataRoot":"original","defaultTargetDurationSec":100}', encoding="utf-8")
    monkeypatch.setenv("STORY_DATA_ROOT", "override")
    monkeypatch.setenv("STORY_DEFAULT_TARGET_DURATION_SEC", "200")
    settings = Settings.load(config)
    from backend.paths import PROJECT_ROOT
    assert settings.data_root == PROJECT_ROOT / "override"
    assert settings.default_target_duration_sec == 200
    monkeypatch.setenv("STORY_LOG_LEVEL", "INVALID")
    with pytest.raises(ValidationError):
        Settings.load(config)


def test_storage_contract_and_confinement(tmp_path):
    storage = LocalStorage(tmp_path / "storage")
    assert isinstance(storage, StorageProvider)
    assert not storage.exists("nested/story.md")
    storage.write_text("nested/story.md", "中文")
    assert storage.read_text("nested/story.md") == "中文"
    assert storage.exists("nested/story.md")
    for key in ("../escape", str(tmp_path / "outside.md"), "."):
        with pytest.raises(ValueError):
            storage.write_text(key, "bad")


def test_provider_contracts_with_offline_adapters():
    class FakeLLM:
        def generate(self, prompt, response_model, config):
            return response_model(title=prompt)

    class FakeImage:
        def generate(self, prompt, references, config):
            return GeneratedAsset(type="image", provider=config.provider, model=config.model,
                                  prompt_hash=input_hash(prompt), file_path="images/test.png")

    class FakeVideo:
        def generate_clip(self, prompt, references, config):
            return GeneratedAsset(type="video", provider=config.provider, model=config.model,
                                  prompt_hash=input_hash(prompt), file_path="videos/test.mp4")

    class FakeTTS:
        def generate(self, text, voice_id, config):
            return AudioAsset(provider=config.provider, model=config.model, voice_id=voice_id,
                              prompt_hash=input_hash(text), file_path="audio/test.wav")

    config = ProviderConfig(provider="offline", model="fake")
    assert isinstance(FakeLLM(), LLMProvider)
    assert isinstance(FakeImage(), ImageProvider)
    assert isinstance(FakeVideo(), VideoProvider)
    assert isinstance(FakeTTS(), TTSProvider)
    assert FakeLLM().generate("title", Manifest, config).title == "title"
    assert FakeImage().generate("prompt", [], config).type == "image"
    assert FakeVideo().generate_clip("prompt", [], config).type == "video"
    assert FakeTTS().generate("hello", "narrator", config).voice_id == "narrator"


def test_hash_is_canonical():
    assert input_hash({"b": 2, "a": "中文"}) == input_hash({"a": "中文", "b": 2})
    assert input_hash({"a": 1}) != input_hash({"a": 2})


def test_utf8_logging(tmp_path):
    path = tmp_path / "logs" / "test.log"
    configure_logging("INFO", path)
    logging.getLogger("story_channel.test").info("專案已建立")
    assert "專案已建立" in path.read_text(encoding="utf-8")
    # Close test-owned handlers before pytest removes temporary files on Windows.
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def test_cli_create_show_outline(tmp_path, capsys):
    root = tmp_path / "channel"
    outline = tmp_path / "outline.md"
    outline.write_text("A lighthouse story.", encoding="utf-8")
    common = ["--data-root", str(root)]
    assert main(common + ["create", str(outline), "--title", "test"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert main(common + ["show", manifest["projectId"]]) == 0
    assert json.loads(capsys.readouterr().out) == manifest
    assert main(common + ["outline", manifest["projectId"]]) == 0
    assert capsys.readouterr().out.strip() == "A lighthouse story."
    assert main(common + ["show", "missing"]) == 1
    assert "Error:" in capsys.readouterr().err
    configure_logging()  # Release the file handler.
