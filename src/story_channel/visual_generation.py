"""Explicit text OR design-image inputs, shared by individual and batch generation."""
import json
import re
from uuid import uuid4

from .files import atomic_write, read_text, input_hash
from .locking import project_lock, ProjectBusyError

DESIGN_MODEL = "gpt-image-2.5-flare"
EDIT_MODEL = "gpt-image-2.5-sunburst"
BACKGROUND_EMPTY = "Empty environment only. No people, characters or human reflections; remove any from reference images."
CHARACTER_INSTRUCTION = (
    "Create one clean character model sheet from @design, the reference image. Preserve the reference character's "
    "face, hair, anatomy, proportions, clothing, colors and visual style. Show consistent full-body front, "
    "side, back and three-quarter views on a neutral background. No text, labels or watermarks.")
BACKGROUND_INSTRUCTION = (
    "Create ONE full spherical equirectangular 360-degree panorama of the environment in @design, the reference image. "
    "Preserve its architecture, objects, materials and visual style. Complete the surroundings behind the camera, "
    "not just a 180-degree frontal POV. One fixed center; front at image center, back at both edges. "
    "The left and right edges must join continuously: matching walls, shelves, floor, ceiling and lighting. "
    "No panels, text, labels or watermarks.")


class VisualGeneration:
    def __init__(self, engine):
        self.engine = engine
        self.path = engine.path / "references" / "generation_inputs"
        self.file = self.path / "inputs.json"

    def key(self, stage, subject):
        if stage == "backgrounds":
            from .background_library import BackgroundLibrary
            subject = BackgroundLibrary(self.engine).subject_id(subject)
        elif stage != "character_references":
            raise ValueError("Generation modes support characters and backgrounds only.")
        return stage + ":" + subject

    def apply(self, state, *, keys=None):
        if self.file.exists():
            from .file_cache import read_json
            state.generation_inputs.update({key: value for key, value in read_json(self.file).items()
                                            if keys is None or key in keys})
        return state

    def default_prompt(self, stage, subject, state):
        if stage == "character_references":
            from .character_prompt_context import visual_context
            character = next(c for c in self.engine.creative.artifact("characters").characters if c.character_id == subject)
            prompt = ("Create one clean character model sheet: consistent full-body front, side, back and three-quarter "
                      "views on a neutral background. No text, labels or watermarks. Personality guides expression and posture only.\n"
                      + json.dumps(visual_context(character), ensure_ascii=False))
        else:
            from .background_library import locations
            lid = self.key(stage, subject).split(":", 1)[1]
            location = next(loc for loc in locations(self.engine.creative) if loc.location_id == lid)
            if lid in state.location_reference_sets:
                return "Reusable location, consistent geometry, materials and lighting. No people or text. " + location.name + ": " + location.description
            prompt = BACKGROUND_INSTRUCTION.replace("of the environment in @design, the reference image", "of this environment").replace(
                "Preserve its architecture, objects, materials and visual style. ", "") + "\n" + location.name + ": " + location.description
        style = state.style_prompts.get(self.key(stage, subject), "")
        return prompt + ("\n" + style if style else "")

    def get(self, stage, subject, state):
        key = self.key(stage, subject)
        if key in state.generation_inputs:
            return dict(state.generation_inputs[key])
        images = state.character_images if stage == "character_references" else state.background_images
        return {"mode": "image" if key.split(":", 1)[1] in images else "text",
                "prompt": self.default_prompt(stage, subject, state)}

    def update(self, stage, subject, **fields):
        key = self.key(stage, subject)
        if set(fields) - {"mode", "prompt"} or fields.get("mode", "text") not in ("text", "image"):
            raise ValueError("Choose text or image prompt mode.")
        if "prompt" in fields and not isinstance(fields["prompt"], str):
            raise ValueError("Prompt must be text.")
        self.path.mkdir(parents=True, exist_ok=True)
        with project_lock(self.path, timeout=1):
            state = self.engine.load()
            values = json.loads(read_text(self.file)) if self.file.exists() else {}
            previous = self.get(stage, subject, state)
            if "prompt" in fields and not fields["prompt"].strip() and previous.get("prompt", "").strip():
                # Keep clearing/retyping local to the editor; stale clients or
                # queued callbacks must not destroy the last usable prompt.
                fields = {name: value for name, value in fields.items() if name != "prompt"}
            values[key] = {**previous, **fields}
            history = self.path / "history"
            for spec in (previous, values[key]):
                if spec.get("prompt", "").strip():
                    snapshot = {"key": key, **spec}
                    archive = history / (input_hash(snapshot) + ".json")
                    if not archive.exists():
                        atomic_write(archive, json.dumps(snapshot, ensure_ascii=False, indent=2))
            atomic_write(self.file, json.dumps(values, ensure_ascii=False, indent=2))
        try:
            with project_lock(self.engine.path, timeout=1):
                return self.engine._refresh()[0]
        except ProjectBusyError:
            return self.engine.load()

    def selected_image(self, stage, subject, state):
        if stage == "character_references":
            return self.engine.character_image(state, subject)
        from .background_library import BackgroundLibrary
        return BackgroundLibrary(self.engine).image(state, subject)

    def request(self, stage, subject, state, *, spec=None, shorten=False):
        from .style_rules import apply_rules
        spec = spec or self.get(stage, subject, state)
        location_task = None
        if stage == "backgrounds":
            lid = self.key(stage, subject).split(":", 1)[1]
            if lid in state.location_reference_sets:
                location_task = next((task for task in self.engine.tasks(state) if task.stage == stage and task.subject == subject), None)
        if location_task and location_task.operation == "location_projection":
            raise ValueError("Yaw images use local panorama projection, never an AI image request.")
        if location_task and location_task.operation == "location_panorama":
            return self._panorama_request(state, lid, subject, spec, location_task, shorten=shorten)
        if spec["mode"] == "text":
            if not spec["prompt"].strip():
                raise ValueError("Enter a text prompt before generating.")
            prompt, images = spec["prompt"], []
        else:
            try:
                image = self.selected_image(stage, subject, state)
            except KeyError:
                raise ValueError("Upload or generate a design reference image before generating.") from None
            prompt, images = CHARACTER_INSTRUCTION if stage == "character_references" else BACKGROUND_INSTRUCTION, [image]
        if location_task:
            prompt = location_task.prompt + ("\nAdditional location direction: " + spec["prompt"] if spec["mode"] == "text" else
                "\nUse the uploaded environment design as a guide; preserve the fixed layout and required camera direction.")
            for key in location_task.refs:
                record = state.records.get(key)
                if not record or record.status != "succeeded" or not self.engine._intact(record):
                    raise ValueError("Generate the master location reference before its directional views.")
            keys = location_task.refs
            images = [self.engine.local_path(state.records[key].asset.file_path) for key in keys] + images
        prompt = apply_rules(prompt, state.project_style_rules)
        from .style_rules import style_reference
        prompt, images = style_reference(self.engine, state, prompt, images)
        if stage == "backgrounds":
            prompt += "\n" + BACKGROUND_EMPTY
        if stage == "backgrounds":
            from .background_prompt_notes import clean_text
            prompt = clean_text(prompt)
        if len(prompt.encode("utf-16-le")) // 2 > 999:
            if stage == "character_references":
                from .character_visual_compaction import cached_prompt, shorten_character_prompt
                prepared = cached_prompt(self.engine, prompt)
                if prepared is not None:
                    return prepared, images
                if shorten:
                    return shorten_character_prompt(self.engine, subject, prompt), images
            if location_task and shorten and not self.engine.demo:
                from .provider_prompts import compile_prompt
                compact = compile_prompt(self.engine.creative, prompt, task_key=f"assets:{location_task.key}:prompt", background=True)
                result = clean_text(compact + "\n" + BACKGROUND_EMPTY)
                if len(result.encode("utf-16-le")) // 2 > 999:
                    raise ValueError("Compiled yaw prompt exceeds the Runway limit. Shorten the layout or appearance notes.")
                return result, images
            if not location_task:
                raise ValueError("Runway accepts at most 999 UTF-16 characters, including shared Style rules. Shorten the displayed prompt or Style rules.")
        return prompt, images

    def _panorama_request(self, state, lid, subject, spec, task, *, shorten):
        from .dual_panorama import panorama_prompt
        from .background_prompt_notes import content_notes
        from .style_rules import style_reference
        from .location_view_context import view_context
        from .floor_plan_guides import require_guides
        from .location_bridges import BRIDGES,request
        name = subject.rsplit("__",1)[1]
        if name in BRIDGES:
            return request(self.engine,state,lid,name,spec)
        guides = require_guides(self.engine,lid,state.location_reference_sets[lid])
        guide = guides["views"][name]
        images = [self.engine.local_path(guide["cameraGuide"]),self.engine.local_path(guide["visibleFloorPlan"])]
        visible_names = {key for key,identity in guides["objectIds"].items() if identity in guide["visibleObjectIds"]}
        from .background_library import locations
        brief = next((location.description for location in locations(self.engine.creative) if location.location_id == lid),"")
        scope = view_context(state.location_reference_sets[lid],name,
                             notes=content_notes(brief),visible_objects=visible_names)
        text = panorama_prompt(name)+(
            " Image 1 is the authoritative camera-region guide; image 2 is the view-only floor plan. "
            "Generate this master directly from these references and the visible object metadata. "
            "Show only objects intersecting this camera region. Stable IDs describe unique physical objects, "
            "never extra copies. Preserve their layout, dimensions, materials, colors and lighting. "
            "Objects shared between adjacent directional masters belong to the same physical room. Continue cut-off "
            "desks, shelves and wall surfaces logically across each 20-degree overlapping boundary. "
            "Keep the fixed camera center, height and clockwise orientation. Never mirror the layout. "
            "Do not render diagram arrows, labels, people, reflections or visible text. ")
        text += "\nVisible view context: "+json.dumps(scope,ensure_ascii=False)
        if any(item.get("orientation") for item in scope["viewObjectLayout"]["visibleObjects"]):
            from .detail_object_guidance import instructions
            text += "\n"+instructions(scope["viewObjectLayout"])
        text += " Camera guide metadata: "+json.dumps({"geometrySource":guides["geometrySource"],
            "camera":guides["camera"],"cameraHeightMeters":guides["cameraHeightMeters"],
            "yaw":guide["yaw"],"horizontalFov":guide["horizontalFov"],"visibleObjectIds":guide["visibleObjectIds"],
            "objectIds":{key:value for key,value in guides["objectIds"].items() if value in guide["visibleObjectIds"]}},ensure_ascii=False)
        text += " Style rules: "+view_context(state.location_reference_sets[lid],name,
            notes=state.project_style_rules,visible_objects=visible_names)["appearanceNotes"]
        text += " Shared pair prompt: "+view_context(state.location_reference_sets[lid],name,
            notes=state.location_pair_prompts.get(lid,{}).get("masters",""),visible_objects=visible_names)["appearanceNotes"]
        from .directional_masters import anchors
        for path,metadata in anchors(self.engine,state,lid,name):
            images.append(path)
            text += (f"\nImage {len(images)} is an overlap crop from a completed neighbor: "+json.dumps(metadata)+
                ". Match shared object identity, materials and lighting only where consistent with the authoritative layout and fixed front directions. "
                "If the crop rotates, moves or duplicates furniture incorrectly, correct that error instead of copying it. "
                "Extend cut-off objects naturally into the new region; do not copy this crop into the center or duplicate its objects.")
        text,images = style_reference(self.engine,state,text,images)
        from .background_constraints import EMPTY_BACKGROUND
        text += "\n"+EMPTY_BACKGROUND
        from .directional_masters import orientation_contract
        text += "\n"+orientation_contract(scope["viewObjectLayout"])
        if len(text) > 32000:
            raise ValueError("OpenAI master prompt exceeds 32,000 characters. Shorten layout/style notes.")
        return text,images

    def design_reference(self, stage, subject, additional_prompt="", *, provider=None):
        """OpenAI creates a design reference; Runway is a separate explicit action."""
        from .media_providers import OpenAIImageProvider, DemoImageProvider
        from .models import ProviderConfig
        from .library_media import LibraryMedia
        state = self.engine.load()
        key = self.key(stage, subject)
        source = self.get(stage, subject, state)["prompt"]
        if not source.strip():
            raise ValueError("Enter a design description before generating a reference.")
        from .style_rules import apply_rules
        if stage == "character_references":
            if source.startswith("Create one clean character model sheet:"):
                source = source.split("\n", 1)[-1]
            prompt = ("Create ONE small character design reference: exactly one full-body character facing straight FRONT, "
                      "centered on a neutral background. One view only, no side, back or three-quarter views, no model sheet, "
                      "turnaround, panels, collage, labels or watermarks. Use the following subject data and additional request "
                      "for appearance only; ignore any request for multiple views.\nSubject data:\n" + source
                      + ("\nAdditional design request: " + additional_prompt if additional_prompt.strip() else "")
                      + "\nRequired output: ONE front-view character only.")
        else:
            prompt = ("Create a visual design reference image, not a prompt or written instructions. No labels or watermarks.\n"
                      + source + ("\nAdditional design request: " + additional_prompt if additional_prompt.strip() else ""))
            location_spec = state.location_reference_sets.get(key.split(":", 1)[1])
            if location_spec:
                prompt += "\nSaved location layout: " + json.dumps({"floorPlan": location_spec.get("floor_plan", ""),
                    "objectLayout": location_spec.get("object_layout", {})}, ensure_ascii=False)
        prompt = apply_rules(prompt, state.project_style_rules)
        path = self.engine.path / "references" / "designs" / uuid4().hex / "reference.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        config = ProviderConfig(provider="demo" if self.engine.demo else "openai", model="demo-image" if self.engine.demo else DESIGN_MODEL,
            options={"output_path": str(path), "size": "1024x1024" if stage == "character_references" else "1536x1024",
                     "quality": "low" if stage == "character_references" else "medium"})
        provider = provider or (DemoImageProvider() if self.engine.demo else OpenAIImageProvider(timeout=self.engine.settings.llm_timeout_sec))
        from .style_rules import style_reference
        prompt, refs = style_reference(self.engine, state, prompt, [])
        if stage == "backgrounds":
            prompt += "\n" + BACKGROUND_EMPTY
            from .background_prompt_notes import clean_text
            prompt = clean_text(prompt)
        media = LibraryMedia(self.engine.manager, self.engine.project_id)
        asset = media._call(provider, provider.generate, (prompt, refs), config, "design_reference:" + key)
        if stage == "character_references":
            from PIL import Image, ImageOps
            with Image.open(path) as source_image:
                small = ImageOps.contain(source_image.convert("RGB"), (512, 512))
                small.save(path, "PNG")
        data = path.read_bytes()
        asset.file_path = path.relative_to(self.engine.path).as_posix()
        asset.generation_params.update(prompt=prompt, additionalPrompt=additional_prompt, role="design_reference")
        atomic_write(path.with_suffix(".json"), asset.model_dump_json(by_alias=True, indent=2))
        if stage == "character_references":
            self.engine.set_character_image(subject, data)
        else:
            from .background_library import BackgroundLibrary
            BackgroundLibrary(self.engine).set_image(subject, data, timeout=max(300, self.engine.settings.render_timeout_sec))
        self.update(stage, subject, mode="image")
        return asset
