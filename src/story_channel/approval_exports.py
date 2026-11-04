"""Complete readable exports for human approval; JSON remains authoritative."""
import json
import re

from .files import atomic_write
from .markdown_exports import readable_text


def redundant_json_sources(files):
    """Hide complete records already represented by a combined source, never partial matches."""
    sources = []
    for path in files:
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, (dict, list)) and data:
                sources.append((path, data))
        except (OSError, ValueError, UnicodeError):
            continue  # Keep unreadable sources visible for inspection.

    def nested(container, target):
        children = container.values() if isinstance(container, dict) else container
        return any(child == target or nested(child, target)
                   for child in children if isinstance(child, (dict, list)))

    redundant = set()
    for index, (path, data) in enumerate(sources):
        for other_index, (_, other) in enumerate(sources):
            if index == other_index:
                continue
            if (other_index < index and data == other) or (data != other and nested(other, data)):
                redundant.add(path)
                break
    return redundant


def json_markdown(data, title, *, item_headings=True):
    def label(value):
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value)).replace("_", " ").capitalize()

    def section(name, level):
        return "#" * min(level, 6) + " " + label(name)

    def render(value, level):
        if isinstance(value, dict):
            parts = []
            # Keep each spoken line on its own line, including any extra line fields.
            if "characterId" in value and "text" in value:
                parts.append(f"{value['characterId']}: {readable_text(value['text'])}")
                value = {key: item for key, item in value.items() if key not in ("characterId", "text")}
            for key, item in value.items():
                parts.extend((section(key, level), render(item, level + 1)))
            return "\n\n".join(parts) or "None"
        if isinstance(value, list):
            return "\n\n".join(
                (section(f"Item {index}", level) + "\n\n" + render(item, level + 1)
                 if item_headings else render(item, level))
                if isinstance(item, (dict, list)) else "- " + render(item, level + 1).replace("\n", "\n  ")
                for index, item in enumerate(value, 1)) or "None"
        if value is None:
            return "Not set"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return readable_text(str(value)) or "(empty)"

    return f"# {title}\n\n{render(data, 2)}\n"


def readable_export(source):
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    from .location_labels import location_labels,display_locations
    labels = location_labels(source.parent)
    aliases = {"characters.json": "character_bible.md", "scenes.json": "storyboard.md"}
    destination = source.with_name(aliases.get(source.name, source.with_suffix(".md").name))
    if source.name == "scenes.json":
        from .creative import Storyboard
        from .markdown_exports import storyboard_markdown
        text = storyboard_markdown(Storyboard.model_validate(data),labels)
    elif source.name == "dialogue_review.json":
        from .dialogue_review import SavedDialogueReview
        from .markdown_exports import dialogue_review_markdown
        saved = SavedDialogueReview.model_validate(data)
        text = dialogue_review_markdown(saved.report, saved.provider, saved.model)
    else:
        title = "AI Review (Story)" if source.name == "review_report.json" else destination.stem.replace("_", " ").title()
        text = json_markdown(display_locations(data,labels), title, item_headings=source.name != "script.json")
    if not destination.exists() or destination.read_text(encoding="utf-8") != text:
        atomic_write(destination, text)
    return destination


