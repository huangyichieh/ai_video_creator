"""JSON persistence for free-text project documents; Markdown is an export format."""
import json
from pathlib import Path

from .files import atomic_write, read_text


def write_document(path: Path, text: str) -> None:
    if path.suffix != ".json":
        raise ValueError("Project documents must be stored as JSON")
    atomic_write(path, json.dumps({"text": text}, ensure_ascii=False, indent=2) + "\n")


def read_document(path: Path) -> str:
    data = json.loads(read_text(path))
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ValueError(f"Invalid project text document: {path.name}")
    return data["text"]


def read_outline(path: Path) -> str:
    file = path / "outline.json"
    if file.exists():
        return read_document(file)
    # Read-only compatibility for an old project pending its verified migration.
    return read_text(path / "outline.md")
