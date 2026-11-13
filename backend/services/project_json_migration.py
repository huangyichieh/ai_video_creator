"""Lossless, restart-safe conversion of project Markdown to JSON.

Existing structured JSON is authoritative and never overwritten. Every original
Markdown byte is preserved as UTF-8 text in a verified JSON audit archive before
the original is removed. Repository documentation is outside the data root.
"""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from backend.services.story_channel.files import atomic_write
from backend.services.story_channel.locking import project_lock
from backend.services.story_channel.project_documents import write_document


ALIASES = {"character_bible.md": "characters.json", "storyboard.md": "scenes.json",
           "world.md": "world_source.json", "world_settings.md": "world_source.json"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def migrate_project_markdown(root: Path, archive_root: Path) -> dict:
    root, archive_root = Path(root).resolve(), Path(archive_root).resolve()
    report = {"schemaVersion": 1, "dataRoot": str(root), "documents": []}
    projects = root / "03_stories"
    if not projects.exists():
        return report
    # Only project directories are application data; never touch README/docs.
    for project in sorted(projects.iterdir()):
        if not project.is_dir() or not (project / "manifest.json").is_file():
            continue
        files = sorted(project.rglob("*.md"))
        if not files:
            continue
        with project_lock(project, allow_deleted=True):
            records = []
            destinations = set()
            for source in files:
                if source.is_symlink() or not source.resolve().is_relative_to(project.resolve()):
                    raise ValueError("Markdown migration cannot follow external links")
                raw = source.read_bytes()
                text = raw.decode("utf-8")  # Preserve BOM, newlines and every byte.
                target = source.with_name(ALIASES.get(source.name, source.with_suffix(".json").name))
                if target in destinations:
                    target = source.with_name(source.name + ".json")
                destinations.add(target)
                record = {"source": source.relative_to(root).as_posix(),
                          "target": target.relative_to(root).as_posix(),
                          "sha256": digest(raw), "bytes": len(raw), "text": text,
                          "existingJson": target.exists()}
                if target.exists():
                    json.loads(target.read_text(encoding="utf-8-sig"))
                    record["targetSha256"] = digest(target.read_bytes())
                records.append((source, target, raw, record))
            # Archive is committed and re-read before any source deletion.
            archive = archive_root / f"{project.name}-{uuid4().hex}.json"
            payload = {"schemaVersion": 1, "projectId": project.name,
                       "documents": [item[3] for item in records]}
            atomic_write(archive, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            saved = json.loads(archive.read_text(encoding="utf-8"))
            for (_, _, raw, _), entry in zip(records, saved["documents"], strict=True):
                if entry["text"].encode("utf-8") != raw or digest(raw) != entry["sha256"]:
                    raise ValueError("Markdown archive verification failed; sources retained")
            for source, target, raw, record in records:
                if not record["existingJson"]:
                    # Match the old UTF-8-sig/universal-newline reader so input
                    # hashes and existing approvals do not change on Windows.
                    text = record["text"].removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
                    write_document(target, text)
                    converted = json.loads(target.read_text(encoding="utf-8"))
                    if converted["text"] != text:
                        raise ValueError("JSON conversion verification failed; source retained")
                elif digest(target.read_bytes()) != record["targetSha256"]:
                    raise ValueError("Existing JSON changed during migration; source retained")
                if source.read_bytes() != raw:
                    raise ValueError("Markdown changed during migration; source retained")
                source.unlink()
                report["documents"].append({key: value for key, value in record.items() if key != "text"})
            report.setdefault("archives", []).append(str(archive))
    report["remainingMarkdown"] = [p.relative_to(root).as_posix() for p in projects.rglob("*.md")]
    return report


def audit_and_migrate(root: Path, archive_root: Path) -> tuple[dict, Path]:
    """Verify every original data file as well as all converted Markdown bytes."""
    root, archive_root = root.resolve(), archive_root.resolve()
    def fingerprint(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    before = {p.relative_to(root).as_posix(): fingerprint(p)
              for p in root.rglob("*") if p.is_file() and p.name != ".project.lock"}
    audit_file = archive_root / ("audit-" + uuid4().hex + ".json")
    audit = {"dataRoot": str(root), "originalHashes": before, "verified": False}
    atomic_write(audit_file, json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    report = migrate_project_markdown(root, archive_root)
    archived = {}
    for file in report.get("archives", []):
        for item in json.loads(Path(file).read_text(encoding="utf-8"))["documents"]:
            raw = item["text"].encode("utf-8")
            if digest(raw) != item["sha256"] or len(raw) != item["bytes"]:
                raise ValueError("Post-migration archive verification failed")
            archived[item["source"]] = item["sha256"]
    errors = []
    for name, original in before.items():
        file = root / name
        if name.endswith(".md"):
            if archived.get(name) != original or file.exists():
                errors.append(name)
        elif not file.is_file() or fingerprint(file) != original:
            errors.append(name)
    # Check all JSON documents, including unchanged histories and new outlines.
    invalid_json = []
    json_count = 0
    for file in root.rglob("*.json"):
        try:
            json.loads(file.read_text(encoding="utf-8-sig"))
            json_count += 1
        except (ValueError, UnicodeError):
            invalid_json.append(file.relative_to(root).as_posix())
    remaining = [p.relative_to(root).as_posix() for p in root.rglob("*.md")]
    audit.update({"migration": report, "changedOrUnverified": errors,
                  "invalidJson": invalid_json, "remainingMarkdown": remaining,
                  "verifiedJsonFiles": json_count,
                  "verified": not errors and not invalid_json and not remaining})
    atomic_write(audit_file, json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    if not audit["verified"]:
        raise ValueError(f"Migration audit requires attention: {audit_file}")
    return audit, audit_file


if __name__ == "__main__":
    from backend.paths import data_path, storage_paths
    from backend.services.story_channel.config import Settings
    audit, file = audit_and_migrate(data_path(Settings.load().data_root),
                                   storage_paths().database / "markdown-archive")
    print(json.dumps({"verified": audit["verified"],
                      "convertedMarkdown": len(audit["migration"]["documents"]),
                      "verifiedOriginalFiles": len(audit["originalHashes"]),
                      "verifiedJsonFiles": audit["verifiedJsonFiles"],
                      "remainingMarkdown": audit["remainingMarkdown"],
                      "auditFile": str(file)}, indent=2))
