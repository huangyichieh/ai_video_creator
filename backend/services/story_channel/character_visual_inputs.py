"""Persist next-request inputs without touching a running production snapshot."""
import json

from .files import atomic_write, read_text
from .locking import ProjectBusyError, project_lock
from .media_utils import write_bytes


class CharacterVisualInputs:
    def __init__(self, engine):
        self.engine = engine
        self.path = engine.path / "references" / "character_inputs"
        self.file = self.path / "inputs.json"

    def read(self):
        from .file_cache import read_json
        return read_json(self.file) if self.file.exists() else {}

    def apply(self, state, *, subjects=None):
        if not self.file.exists() or not (self.engine.path / "characters.json").exists():
            return state
        active = {c.character_id for c in self.engine.creative.artifact("characters").characters}
        for cid, fields in self.read().items():
            if cid not in active or subjects is not None and cid not in subjects:
                continue
            if "style_prompt" in fields:
                state.style_prompts["character_references:" + cid] = fields["style_prompt"]
            if "reference" in fields:
                if fields["reference"] is None:
                    state.character_images.pop(cid, None)
                else:
                    state.character_images[cid] = dict(fields["reference"])
        return state

    def update(self, cid, fields, *, image_data=None):
        self.path.mkdir(parents=True, exist_ok=True)
        with project_lock(self.path, timeout=1):
            if cid not in {c.character_id for c in self.engine.creative.artifact("characters").characters}:
                raise ValueError("Unknown character")
            values = self.read()
            if image_data is not None:
                write_bytes(self.engine.local_path(fields["reference"]["file"]), image_data)
            values.setdefault(cid, {}).update(fields)
            atomic_write(self.file, json.dumps(values, ensure_ascii=False, indent=2))
        try:
            with project_lock(self.engine.path):
                return self.engine._refresh()[0]
        except ProjectBusyError:
            # The worker keeps the inputs captured when its request started.
            # load() overlays the saved edits for the UI and the next request.
            return self.engine.load()

    def remove(self, cid):
        if not self.file.exists():
            return
        with project_lock(self.path, timeout=1):
            values = self.read()
            values.pop(cid, None)
            atomic_write(self.file, json.dumps(values, ensure_ascii=False, indent=2))
