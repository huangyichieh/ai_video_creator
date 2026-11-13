"""Shared art direction, kept separate from immutable World canon."""
import json
import re

from .files import atomic_write, read_text
from .locking import project_lock


def apply_rules(prompt, rules):
    return prompt + ("\nProject style rules:\n" + rules.strip() if rules.strip() else "")


def audio_rules(rules, *, kind=None):
    """Explicit audio sections avoid sending visual instructions as sound content."""
    labels = "audio|聲音|声音"
    if kind != "sound":
        labels += "|voice|speech|語音|配音"
    if kind != "voice":
        labels += "|sound|sfx|音效"
    return "\n".join(line for line in rules.splitlines() if re.match(r"\s*(?:" + labels + r")\s*[:：]", line, re.I))


def pipeline_rules(pipeline):
    file = pipeline.path / "style_rules.json"
    if file.exists():
        return json.loads(read_text(file))["rules"]
    production = pipeline.path / "production.json"
    if production.exists():
        value = json.loads(read_text(production))
        return value.get("projectStyleRules", value.get("project_style_rules", ""))
    return ""


class StyleRules:
    def __init__(self, engine):
        self.engine = engine

    def apply(self, state):
        file = self.engine.path / "style_rules.json"
        if file.exists():
            from .file_cache import read_json
            data = read_json(file)
            state.project_style_rules = data["rules"]
            state.project_style_image = data.get("image")
        return state

    def save(self, rules):
        if not isinstance(rules, str):
            raise ValueError("Style rules must be text.")
        with project_lock(self.engine.path):
            state = self.engine.load()
            atomic_write(self.engine.path / "style_rules.json", json.dumps({"rules": rules.strip(), "image": state.project_style_image}, ensure_ascii=False, indent=2))
            return self.engine._refresh()[0]

    def image(self, state):
        from .media_utils import file_hash
        reference = state.project_style_image
        if not reference:
            return None
        path = self.engine.local_path(reference["file"])
        try:
            info = path.stat()
        except OSError:
            raise ValueError("The shared style reference is missing or modified. Upload it again or remove it.")
        stamp = (str(path), reference["hash"], info.st_mtime_ns, info.st_size)
        if getattr(self.engine, "_project_style_image_checked", None) != stamp:
            if not path.is_file() or file_hash(path) != reference["hash"]:
                raise ValueError("The shared style reference is missing or modified. Upload it again or remove it.")
            self.engine._project_style_image_checked = stamp
        return path

    def set_image(self, data):
        from io import BytesIO
        from PIL import Image, UnidentifiedImageError
        from .media_utils import file_hash, write_bytes
        import hashlib
        reference = None
        if data is not None:
            if not isinstance(data, bytes) or len(data) > 20 * 1024 * 1024:
                raise ValueError("Upload a PNG, JPEG or WebP style image up to 20 MB.")
            try:
                with Image.open(BytesIO(data)) as image:
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
                    image.verify()
                if extension is None:
                    raise ValueError("Upload a PNG, JPEG or WebP style image.")
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise ValueError("The style reference is not a valid image.") from exc
            digest = hashlib.sha256(data).hexdigest()
            reference = {"file": f"references/project_style/{digest}.{extension}", "hash": digest}
        with project_lock(self.engine.path):
            state = self.engine.load()
            if reference:
                write_bytes(self.engine.local_path(reference["file"]), data)
            atomic_write(self.engine.path / "style_rules.json", json.dumps({"rules": state.project_style_rules, "image": reference}, ensure_ascii=False, indent=2))
            return self.engine._refresh()[0]


def style_reference(engine, state, prompt, references):
    """Append a style-only guide after all design/identity inputs."""
    path = StyleRules(engine).image(state)
    if path is None:
        return prompt, list(references)
    references = list(references) + [path]
    prompt += (f"\nImage {len(references)} (@style) is STYLE ONLY: match its medium, linework, shading, palette and texture. "
               "Do not copy its subjects, faces, clothing, objects, layout or composition. Other images define the design.")
    return prompt, references


def reference_tags(engine, state, references):
    if not state.project_style_image:
        return []
    style = engine.local_path(state.project_style_image["file"])
    return ["style" if path == style else "design" if len(references) == 2 else f"content{i+1}"
            for i, path in enumerate(references)]
