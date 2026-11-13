import hashlib
import json
from pathlib import Path

import pytest

from backend.services.project_json_migration import migrate_project_markdown
from story_channel.config import Settings
from story_channel.projects import ProjectManager
from story_channel.project_documents import read_document


def test_lossless_migration_preserves_canonical_json_and_history(tmp_path):
    root = tmp_path / "outputs"
    project = root / "03_stories" / "example"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text("{}")
    originals = {
        "outline.md": b"\xef\xbb\xbf# World\r\n\r\n" + "辦公室世界".encode(),
        "story.md": b"An older readable export with unique notes.\n",
        "character_bible.md": b"Character export\n",
        "history/outline/old.md": b"Original outline\r\n",
        "notes.md": b"Standalone notes\n",
    }
    for name, raw in originals.items():
        file = project / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)
    structured = {"story.json": b'{"hook":"Latest edited story"}',
                  "characters.json": b'{"characters":[]}'}
    for name, raw in structured.items():
        (project / name).write_bytes(raw)
    media = project / "image.png"
    media.write_bytes(b"unchanged-media")
    docs = tmp_path / "README.md"
    docs.write_text("Repository documentation")

    report = migrate_project_markdown(root, tmp_path / "database/archive")
    assert len(report["documents"]) == len(originals)
    assert report["remainingMarkdown"] == []
    entries = json.loads(Path(report["archives"][0]).read_text(encoding="utf-8"))["documents"]
    for item in entries:
        name = item["source"].removeprefix("03_stories/example/")
        assert item["text"].encode("utf-8") == originals[name]
        assert item["sha256"] == hashlib.sha256(originals[name]).hexdigest()
    assert read_document(project / "outline.json") == originals["outline.md"].decode("utf-8-sig").replace("\r\n", "\n")
    assert read_document(project / "history/outline/old.json") == "Original outline\n"
    assert read_document(project / "notes.json") == "Standalone notes\n"
    for name, raw in structured.items():
        assert (project / name).read_bytes() == raw
    assert media.read_bytes() == b"unchanged-media"
    assert docs.read_text() == "Repository documentation"
    assert migrate_project_markdown(root, tmp_path / "database/archive")["documents"] == []


def test_invalid_existing_json_keeps_all_sources(tmp_path):
    project = tmp_path / "outputs/03_stories/example"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text("{}")
    (project / "outline.md").write_text("outline")
    (project / "story.md").write_text("story")
    (project / "story.json").write_text("{")
    with pytest.raises(ValueError):
        migrate_project_markdown(tmp_path / "outputs", tmp_path / "archive")
    assert (project / "outline.md").read_text() == "outline"
    assert (project / "story.md").read_text() == "story"
    assert not (project / "outline.json").exists()


def test_archive_verification_failure_does_not_delete_sources(tmp_path, monkeypatch):
    import backend.services.project_json_migration as migration
    project = tmp_path / "outputs/03_stories/example"
    project.mkdir(parents=True)
    (project / "manifest.json").write_text("{}")
    (project / "outline.md").write_text("Keep me")
    original = migration.atomic_write

    def corrupt(path, text):
        data = json.loads(text)
        data["documents"][0]["text"] = "corrupted"
        original(path, json.dumps(data))

    monkeypatch.setattr(migration, "atomic_write", corrupt)
    with pytest.raises(ValueError, match="archive verification"):
        migration.migrate_project_markdown(tmp_path / "outputs", tmp_path / "archive")
    assert (project / "outline.md").read_text() == "Keep me"


def test_manager_creates_and_edits_json_outline_without_markdown(tmp_path):
    from story_channel.pipeline import Pipeline
    source = tmp_path / "input.txt"
    source.write_text("An island of memories.", encoding="utf-8")
    manager = ProjectManager(Settings(data_root=tmp_path / "channel", llm_provider="demo"))
    project = manager.create("JSON project", source)
    path = manager.project_path(project.project_id)
    pipeline = Pipeline(manager, project.project_id)
    pipeline.edit("outline", "A keeper saves the island.")
    assert manager.read_outline(project.project_id) == "A keeper saves the island."
    assert not list(path.rglob("*.md"))
    assert len(list((path / "history/outline").glob("*.json"))) == 1
    pipeline.run()
    pipeline.approve("story")
    pipeline.run()
    assert not list(path.rglob("*.md"))
    from story_channel.approval_exports import readable_export
    content = readable_export(path / "script.json")
    assert content.startswith("# Script")
    assert not list(path.rglob("*.md"))


def test_automatic_migration_keeps_outline_hash_and_manifest(tmp_path, monkeypatch):
    from story_channel.files import input_hash
    monkeypatch.setenv("STORY_STORAGE_ROOT", str(tmp_path / "storage"))
    settings = Settings(data_root=tmp_path / "storage/outputs/channel", llm_provider="demo")
    manager = ProjectManager(settings)
    source = tmp_path / "input.txt"
    source.write_text("Existing story", encoding="utf-8")
    project = manager.create("Existing", source)
    path = manager.project_path(project.project_id)
    (path / "outline.json").unlink()
    raw = b"\xef\xbb\xbfLine one\r\nLine two\r\n"
    (path / "outline.md").write_bytes(raw)
    before = input_hash(manager.read_outline(project.project_id))
    manifest = (path / "manifest.json").read_bytes()
    manager = ProjectManager(settings)
    assert len(manager.document_migration["documents"]) == 1
    assert input_hash(manager.read_outline(project.project_id)) == before
    assert (path / "manifest.json").read_bytes() == manifest
    assert not (path / "outline.md").exists()
