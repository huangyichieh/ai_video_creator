"""Edit an asset's canonical source without replacing unrelated list entries."""
from .creative import Location, PromptItem
from .models import Model, Scene
from .modules import MODULES


class SceneSource(Model):
    scene: Scene


class SceneImageSource(SceneSource):
    prompt: PromptItem | None = None


def scene_id(stage, subject):
    return subject.rsplit("_", 2)[0] if stage == "voices" else subject


def read_source(pipeline, stage, subject):
    if stage == "backgrounds":
        locations = pipeline.artifact("world").locations
        direct = next((location for location in locations if location.location_id == subject), None)
        if direct:
            return direct
        base, _, view = subject.rpartition("__")
        matched = next((location for location in locations if location.location_id == base), None)
        if matched and view in ("left", "right", "back"):
            return matched
        raise ValueError("Unknown background location or view.")
    sid = scene_id(stage, subject)
    scene = next(c for c in pipeline.artifact("storyboard").scenes if c.scene_id == sid)
    if stage == "voices":
        return SceneSource(scene=scene)
    try:
        prompt = next((p for p in pipeline.artifact("prompts").prompts if p.scene_id == sid), None)
    except OSError:
        prompt = None
    return SceneImageSource(scene=scene, prompt=prompt)


def edit_source(pipeline, stage, subject, text, *, expected):
    model = Location if stage == "backgrounds" else SceneSource if stage == "voices" else SceneImageSource
    draft = model.model_validate_json(text)
    baseline = model.model_validate_json(expected)
    # Capture full files first so edit_bundle also catches intervening writes.
    modules = ["world"] if stage == "backgrounds" else ["storyboard"] + (["prompts"] if stage == "scene_images" and draft.prompt else [])
    originals = {name: (pipeline.path / MODULES[name].filename).read_text(encoding="utf-8-sig") for name in modules}
    if read_source(pipeline, stage, subject) != baseline:
        raise ValueError("This source changed in another editor. Revert all changes to load its latest JSON.")
    changes = {}

    def replace(name, field, identity, target, item):
        document = MODULES[name].model.model_validate_json(originals[name])
        entries = getattr(document, field)
        index = next(i for i, entry in enumerate(entries) if getattr(entry, identity) == target)
        if getattr(item, identity) != target:
            raise ValueError(f"Keep {identity} unchanged when editing an asset source.")
        if entries[index] != item:
            entries[index] = item
            changes[name] = document.model_dump_json(by_alias=True)

    if stage == "backgrounds":
        replace("world", "locations", "location_id", baseline.location_id, draft)
    else:
        sid = scene_id(stage, subject)
        replace("storyboard", "scenes", "scene_id", sid, draft.scene)
        if stage == "scene_images":
            if (draft.prompt is None) != (baseline.prompt is None):
                raise ValueError("Keep the scene prompt entry; edit its fields rather than adding or removing it.")
            if draft.prompt:
                replace("prompts", "prompts", "scene_id", sid, draft.prompt)
    pipeline.edit_bundle(changes, expected=originals)


def modify_source_with_openai(pipeline, stage, subject, instructions, *, expected):
    from .locking import project_lock
    from .modification import modify_content
    with project_lock(pipeline.path):
        pipeline._gate({"character_references": "characters", "backgrounds": "world", "scene_images": "storyboard", "voices": "storyboard"}[stage],
                       pipeline.manager.load(pipeline.project_id))
        if stage == "character_references":
            current = next(c for c in pipeline.artifact("characters").characters if c.character_id == subject)
        else:
            current = read_source(pipeline, stage, subject)
        if type(current).model_validate_json(expected) != current:
            raise ValueError("The source changed. Reopen Modify with OpenAI.")
        output = modify_content(pipeline, current, instructions, f"{stage}:{subject}")
    if stage == "character_references":
        pipeline.edit_character(subject, output.model_dump_json(by_alias=True), expected=expected)
    else:
        edit_source(pipeline, stage, subject, output.model_dump_json(by_alias=True), expected=expected)
