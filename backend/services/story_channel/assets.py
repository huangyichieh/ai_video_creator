"""Asset production service: approval-gated, resumable, and provider-independent."""
import json
import io
import logging
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from threading import RLock
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import nullcontext
from collections import deque
from contextvars import copy_context

from .asset_models import ASSET_STAGES, Alignment, AssetRecord, AssetModification, Cue, Production, SceneAssets
from .costs import CostTracker
from .request_tracking import tracked_call, request_fields
from .files import atomic_write, input_hash, read_text
from .llm import ProviderError, TransientProviderError
from .locking import project_lock
from .media_providers import (DemoAlignmentProvider, DemoImageProvider, DemoTTSProvider,
                              OpenAIAlignmentProvider, OpenAIImageProvider, OpenAITTSProvider, ThreadLocalUsage)
from .media_utils import combine_wav, file_hash, subtitle_text, text_chunks, wav_duration, write_bytes
from .models import ApprovalRecord, GeneratedAsset, ProjectStatus, ProviderConfig, UsageRecord, utc_now
from .pipeline import ApprovalRequired, Pipeline

logger = logging.getLogger(__name__)


@dataclass
class Task:
    stage: str
    subject: str
    prompt: str
    scene_id: str | None = None
    refs: list[str] = field(default_factory=list)
    voice_id: str = ""
    speaker: str = ""
    line_index: int = -1
    chunk_index: int = 0
    chunk_count: int = 1
    operation: str = ""
    audio_tags: list[str] = field(default_factory=list)
    voice_line_source_hash: str = ""

    @property
    def key(self):
        return f"{self.stage}:{self.subject}"


class AssetPipeline:
    def __init__(self, manager, project_id, *, demo=None, image_provider=None, tts_provider=None,
                 alignment_provider=None, on_progress=None, stop_requested=None, max_tasks=5):
        if not 1 <= max_tasks <= 5:
            raise ValueError("Maximum parallel asset tasks must be between 1 and 5.")
        self.max_tasks = max_tasks
        self._state_lock = RLock()
        self._provider_lock = RLock()
        self._batch_open = False
        self._task_activity = {}
        self.manager = manager
        self.project_id = project_id
        self.creative = Pipeline(manager, project_id)
        self.path = manager.project_path(project_id)
        self.settings = manager.settings
        self.state_path = self.path / "production.json"
        prior = self.load()
        self.demo = (demo if demo is not None else prior.provider_mode == "demo" if prior.source_hash else
                     self.creative.demo or self.settings.asset_provider == "demo")
        from . import balanced_providers
        self.image_provider = image_provider or (DemoImageProvider() if self.demo else balanced_providers.image_provider(self.settings))
        self.panorama_image_provider = self.image_provider if self.demo or image_provider is not None else OpenAIImageProvider(timeout=self.settings.llm_timeout_sec)
        self.character_image_provider = self.panorama_image_provider
        self.scene_image_provider = self.panorama_image_provider
        self.tts_provider = tts_provider or (DemoTTSProvider() if self.demo else balanced_providers.tts_provider(self.settings))
        self.alignment_provider = alignment_provider or (DemoAlignmentProvider() if self.demo else OpenAIAlignmentProvider(timeout=self.settings.llm_timeout_sec))
        from .background_panorama import PanoramaViewProvider
        self.background_projection_provider = PanoramaViewProvider()
        self.on_progress = on_progress
        self.modification_image_provider = OpenAIImageProvider(timeout=self.settings.llm_timeout_sec)
        self.modification_tts_provider = OpenAITTSProvider(timeout=self.settings.llm_timeout_sec)
        self.stop_requested = stop_requested or (lambda: False)

    def parallel_limit(self, stage):
        if stage in ("character_references", "backgrounds"):
            return 1
        if self.demo:
            return self.max_tasks
        from .service_limits import LIMITS
        service = self.settings.tts_provider if stage == "voices" else "openai" if stage in ("subtitles","character_references","scene_images") else self.settings.image_provider
        return min(self.max_tasks, LIMITS.get(service, self.max_tasks))

    def load(self):
        from .file_cache import read_model
        state = read_model(self.state_path, Production) if self.state_path.exists() else Production()
        from .location_migration import unify
        unify(self,state,self.manager.load(self.project_id))
        from .character_visual_inputs import CharacterVisualInputs
        from .visual_generation import VisualGeneration
        from .style_rules import StyleRules
        return StyleRules(self).apply(VisualGeneration(self).apply(CharacterVisualInputs(self).apply(state)))

    def local_path(self, relative):
        from backend.paths import relocated_path
        relative=relocated_path(relative)
        path = (self.path / relative).resolve()
        if path == self.path or not path.is_relative_to(self.path):
            raise ValueError("Asset file path escapes the project")
        return path

    def _creative_ready(self, manifest):
        if manifest.world_project_id:
            from .studio import approved_content
            if not approved_content(self.creative, "prompts"):
                return False
        return ("story" in manifest.approvals and "storyboard" in manifest.approvals
                and all(manifest.steps.get(step) and manifest.steps[step].status == "succeeded"
                        for step in ("world", "characters", "story", "review", "script", "storyboard", "prompts")))

    def _source(self):
        manifest = self.manager.load(self.project_id)
        required = ("world",) if self.independent_backgrounds("backgrounds", manifest) else ("world", "characters")
        if any(not manifest.steps.get(step) or manifest.steps[step].status != "succeeded"
               for step in required):
            raise ApprovalRequired("Complete the Characters stage before generating character assets.")
        return input_hash({"artifacts": {step: manifest.steps[step].output_hash for step in manifest.steps if step in ("world", "characters", "story", "script", "storyboard", "prompts")},
                           "language": manifest.language, "mode": "demo" if self.demo else "openai",
                           "image": [self.settings.image_model, self.settings.image_size, self.settings.image_quality],
                           "tts": self.settings.tts_model, "align": self.settings.alignment_model,
                           "narrator": self.settings.narrator_voice, "version": "assets-v1"})

    def tasks(self, state, *, references_only=False):
        from .animation import load_animation
        from .animation_models import LocationBible
        from .identity import IdentityManager
        identity = IdentityManager(self.manager, self.project_id)
        library = identity.load() if (self.path / "characters.json").exists() else None
        if library:
            identity.validate(library)
        direction = load_animation(self.path)
        direction = direction if direction and direction.enabled else None
        characters = (self.creative.artifact("characters").characters if (self.path / "characters.json").exists() else [])
        owner = self.manager.load(self.project_id)
        if owner.world_project_id and (self.path/"scenes.json").exists():
            used = {cid for scene in self.creative.artifact("storyboard").scenes for cid in scene.character_ids}
            characters = [character for character in characters if character.character_id in used]
        shared_characters = owner.workflow_version == "world_episode"
        tasks = []
        for c in characters:
            prompt = (f"Character reference sheet, neutral background, full-body front and three-quarter views. "
                      f"Name: {c.name}; age: {c.age}; appearance: {c.appearance}; clothing: {c.clothing}; "
                      f"fixed traits: {', '.join(c.immutable_traits)}. Consistent identity across all views. No text.")
            if direction or library:
                prompt += (" Reference pack panels: front face, three-quarter face, side profile, full body, common expressions, "
                           "fixed costume and signature props. Keep every panel's identity, proportions and art style identical.")
            if library:
                # Alternate outfits have independent sheets; keep the base sheet
                # anchored to the character's canonical clothing.
                costumes = ([{"costume_id": f"{c.character_id[:119]}_default", "character_id": c.character_id, "description": c.clothing}]
                            if shared_characters else [item.model_dump(mode="json") for item in library.costumes.values() if item.character_id == c.character_id])
                prompt += " Registered identity: " + json.dumps({"characterId": c.character_id,
                    "costumes": costumes,
                    "props": [item.model_dump(mode="json") for item in library.props.values() if item.owner_id == c.character_id]}, ensure_ascii=False)
            if state.character_prompt_version >= 1:
                from .character_prompt_context import visual_prompt
                prompt = visual_prompt(c, reference_pack=bool(direction or library))
            tasks.append(Task("character_references", c.character_id, prompt))
        manifest = self.manager.load(self.project_id)
        library_mode = manifest.workflow_version == "world_episode" and not manifest.world_project_id
        if library_mode:
            from .studio import WorldStudio
            WorldStudio(self.manager, self.project_id).require_approved()
        if not library_mode:
            if references_only:
                # Reference validation is read-only preparation. Story approval
                # gates generation, not inspection of an episode's saved pack.
                if not (self.path/"scenes.json").exists():
                    return tasks
            elif not self._creative_ready(manifest):
                return tasks
        world = self.creative.artifact("world")
        board = self.creative.artifact("storyboard") if not library_mode else None
        prompts = {p.scene_id: p for p in self.creative.artifact("prompts").prompts} if not library_mode and not references_only else {}
        from .background_library import locations
        available_locations = locations(self.creative)
        used_locations = {scene.location_id for scene in board.scenes} if board else {loc.location_id for loc in available_locations}
        for location in available_locations:
            if location.location_id in used_locations:
                tasks.append(Task("backgrounds", location.location_id,
                                  f"Reusable cinematic environment reference, no people, no text. Era: {world.era}. "
                                  f"Location: {location.name}. {location.description}. World rules: {'; '.join(world.rules)}"
                                  + (" Preserve this location bible: " + library.locations[location.location_id].model_dump_json()
                                     if library else " Preserve this location bible: " + direction.plan.locations[location.location_id].model_dump_json()
                                     if direction and location.location_id in direction.plan.locations else
                                     " Preserve this location bible: " + LocationBible(location_id=location.location_id, description=location.description).model_dump_json()
                                     if manifest.workflow_version == "world_episode" else "")))
                if manifest.workflow_version == "world_episode":
                    base = tasks[-1]
                    if location.location_id in state.location_reference_sets:
                        from .location_references import location_tasks
                        tasks.pop()
                        location_jobs = location_tasks(Task, location.location_id, base.prompt, state.location_reference_sets[location.location_id])
                        for job in location_jobs:
                            if job.subject == getattr(self,"_individual_location_master",None):
                                # Individual repair must never enqueue another master.
                                job.refs = []
                        tasks.extend(location_jobs)
                        continue
                    from .background_panorama import panorama_mode, panorama_tasks
                    if panorama_mode(state, location.location_id):
                        tasks.pop()
                        tasks.extend(panorama_tasks(Task, location.location_id, base.prompt))
                        continue
                    base.prompt += " View from the center looking front. Keep the same floor plan and lighting across all four views."
                    for view in ("left", "right", "back"):
                        tasks.append(Task("backgrounds", location.location_id + "__" + view,
                            base.prompt.replace("looking front", "looking " + view)
                            + " Use the attached front view as the geometry and location identity anchor; rotate from the same center, preserving layout and lighting.",
                            refs=[base.key]))
        if library_mode or references_only:
            return tasks
        voices = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
        state.voice_map.setdefault("narrator", self.settings.narrator_voice or ("alloy" if self.demo else ""))
        for c in characters:
            state.voice_map.setdefault(c.voice_id, voices[int(input_hash(c.voice_id)[:8], 16) % len(voices)] if self.demo or self.settings.tts_provider == "openai" else "")
            if library:
                state.voice_map[c.voice_id] = library.voices[c.character_id].voice_id
            if direction and c.character_id in direction.plan.voices and direction.plan.voices[c.character_id].voice_id:
                state.voice_map[c.voice_id] = direction.plan.voices[c.character_id].voice_id
        cast = {c.character_id: c for c in characters}
        from .background_panorama import reference_keys
        from .location_references import LocationReferenceResolver
        for scene in board.scenes:
            prompt = prompts[scene.scene_id]
            tasks.append(Task("scene_images", scene.scene_id,
                              f"{prompt.image_prompt}\nAvoid: {prompt.negative_prompt}\n"
                              "Use the attached character and environment references. Preserve identity, clothing and location.",
                              scene.scene_id, [f"character_references:{cid}" for cid in scene.character_ids]
                              + (LocationReferenceResolver(self, state).resolve(
                                     scene.location_id, scene.camera_start_angle, scene.camera_end_angle, scene.target_area,
                                     pitch=scene.camera_pitch,fov=scene.camera_fov,
                                     character_position=scene.character_position,interaction_target=scene.interaction_target,
                                     prefer_details=True)
                                 if manifest.workflow_version == "world_episode" else [f"backgrounds:{scene.location_id}"])))
            from .animation_formats import saved_guidance
            framing = saved_guidance(self.path)
            if framing:
                tasks[-1].prompt += "\n" + framing
            if scene.location_id in state.location_reference_sets:
                spec = state.location_reference_sets[scene.location_id]
                from .location_references import scene_reference_guidance
                tasks[-1].prompt += "\n" + scene_reference_guidance(state,tasks[-1].refs)
                from .shared_room import enabled
                if enabled(spec):
                    tasks[-1].prompt += ("\nLocation references include an exact-angle raw projection (spatial truth) and nearest AI-enhanced canonical views (visual/style truth). "
                        "Preserve the raw projection's camera, room geometry and fixed object positions. Transfer materials, texture, lighting and object appearance from enhanced canonical views without copying their camera framing. "
                        "Do not treat blockout colors as the art style or relocate fixed anchors.")
                tasks[-1].prompt += (f"\nCamera yaw: {scene.camera_start_angle or 0} to {scene.camera_end_angle if scene.camera_end_angle is not None else scene.camera_start_angle or 0}; "
                    f"pitch: {scene.camera_pitch}; horizontal FOV: {scene.camera_fov}; "
                    f"target area: {scene.target_area}. Fixed layout: " + json.dumps({"floorPlan": spec.get("floor_plan", ""),
                        "objectLayout": spec.get("object_layout", {})}, ensure_ascii=False))
            lines = ([("narrator", "narrator", scene.narration)] if scene.narration.strip() else [])
            lines += [(line.character_id, cast[line.character_id].voice_id, line.text) for line in scene.dialogue]
            for line_index, (speaker, voice, text) in enumerate(lines):
                chunks = text_chunks(text)
                for chunk_index, chunk in enumerate(chunks):
                    subject = f"{scene.scene_id}_{line_index:04d}_{chunk_index:04d}"
                    task = Task("voices", subject, chunk, scene.scene_id, voice_id=voice, speaker=speaker,
                                line_index=line_index-int(bool(scene.narration.strip())), chunk_index=chunk_index, chunk_count=len(chunks))
                    # Direction stays out of spoken text, subtitles and alignment.
                    task.audio_tags = (scene.narration_audio_tags if speaker == "narrator" else
                                       scene.dialogue[task.line_index].audio_tags)
                    from .voice_lines import apply_override
                    apply_override(task,state,cast)
                    tasks.append(task)
                    tasks.append(Task("subtitles", subject, task.prompt, scene.scene_id, [task.key], voice_id=task.voice_id, speaker=task.speaker))
        return tasks

    def _config(self, task, state):
        if task.operation == "room_canonical_raw":
            from .room_canonical import MODEL
            return ProviderConfig(provider="local",model=MODEL,options={"size":"1536x1024"})
        if task.operation == "room_canonical_enhanced":
            from .dual_panorama import PANORAMA_MODEL
            return ProviderConfig(provider="demo" if self.demo else "openai",model=PANORAMA_MODEL,
                options={"size":"1536x1024", "quality":self.settings.image_quality,
                         "project_style_rules":state.project_style_rules,
                         "project_style_image":state.project_style_image,
                         "background_style_image":state.background_images.get(task.subject.rsplit("__",1)[0])})
        if task.operation == "room_panorama":
            from .room_geometry import MODEL
            return ProviderConfig(provider="local",model=MODEL,options={"size":"1536x768"})
        if task.operation == "location_projection":
            from .dual_panorama import MODEL
            return ProviderConfig(provider="local", model=MODEL, options={"size": self.settings.image_size,
                "yaw": int(task.subject.rsplit("view_", 1)[1]), "pitch": 0, "fov": 70, "cameraCenter": "location_center"})
        if task.operation == "panorama_view":
            from .background_panorama import MODEL, VIEWS, view_subject
            lid = task.refs[0].removeprefix("backgrounds:").removesuffix("__panorama")
            view = next(view for view in VIEWS if task.subject == view_subject(lid, view))
            return ProviderConfig(provider="local", model=MODEL, options={"view": view, "size": self.settings.image_size})
        if task.stage in ("character_references", "backgrounds", "scene_images"):
            model, options = self.settings.image_model, {"size": self.settings.image_size, "quality": self.settings.image_quality}
            if task.stage == "scene_images":
                from .scene_image_references import manifest as scene_manifest
                options["sceneAssetReferences"] = scene_manifest(self,state,task)
                scene = next(item for item in self.creative.artifact("storyboard").scenes if item.scene_id == task.subject)
                options["storyboardScene"] = scene.model_dump(mode="json",by_alias=True)
                options["storyboardSceneHash"] = input_hash(options["storyboardScene"])
                from .animation_formats import saved_options,delivery_formats,PORTRAIT
                animation_options = saved_options(self.path)
                if animation_options and animation_options.delivery_formats:
                    options["size"] = "1024x1536" if delivery_formats(animation_options) == [PORTRAIT] else "1536x1024"
            if task.operation == "panorama":
                options["size"] = "1536x1024"
                options["panorama"] = True
        elif task.stage == "voices":
            model, options = self.settings.tts_model, {"voice": state.voice_map[task.voice_id]}
        else:
            model, options = self.settings.alignment_model, {"language": self.manager.load(self.project_id).language}
        style = state.style_prompts.get(task.key, "").strip()
        if self.shared_style_enabled(state) and task.stage in ASSET_STAGES[:4] and state.generic_prompt.strip():
            options["generic_prompt"] = state.generic_prompt.strip()
        from .identity import IdentityManager
        library = IdentityManager(self.manager, self.project_id).load()
        if self.shared_style_enabled(state) and library and library.style.strip() and task.stage in ASSET_STAGES[:3]:
            options["generic_prompt"] = (options.get("generic_prompt", "") + "\n" + library.style).strip()
        if self.shared_style_enabled(state) and task.stage in ASSET_STAGES[:3] and state.style_reference:
            options["style_reference_hash"] = state.style_reference_hash
        if style:
            options["style_prompt"] = style
        if task.stage == "character_references" and task.subject in state.character_images:
            reference = state.character_images[task.subject]
            options["character_image_hash"] = reference["hash"]
            try:
                options["character_image_intact"] = file_hash(self.local_path(reference["file"])) == reference["hash"]
            except OSError:
                options["character_image_intact"] = False
        if task.stage == "backgrounds":
            from .background_library import BackgroundLibrary
            library = BackgroundLibrary(self)
            reference = state.background_images.get(library.subject_id(task.subject))
            if reference:
                options["background_image_hash"] = reference["hash"]
                try:
                    library.image(state, task.subject)
                    options["background_image_intact"] = True
                except (OSError, ValueError):
                    options["background_image_intact"] = False
        modification = state.asset_modifications.get(task.key)
        if modification:
            options["modification"] = modification.model_dump(mode="json")
        if task.stage == "voices":
            from .animation import performance_options
            options.update(performance_options(self.manager, self.project_id, task))
            options["audio_tags"] = list(task.audio_tags)
            if modification and modification.delivery:
                options["performance"] = {**options.get("performance", {}), **modification.delivery}
        provider = "openai" if modification else "demo" if self.demo else self.settings.image_provider if task.stage in ASSET_STAGES[:3] else self.settings.tts_provider if task.stage == "voices" else "openai"
        if modification and task.stage == "voices" and not self.demo and self.settings.tts_provider == "elevenlabs":
            provider, model = "elevenlabs", self.settings.tts_model
        elif modification:
            from .visual_generation import EDIT_MODEL
            model = "gpt-4o-mini-tts" if task.stage == "voices" else EDIT_MODEL
        if task.stage == "character_references" and not modification:
            from .visual_generation import DESIGN_MODEL
            provider, model = "demo" if self.demo else "openai", DESIGN_MODEL
        if task.stage == "scene_images" and not modification:
            from .scene_image_references import SCENE_IMAGE_MODEL
            provider,model = "demo" if self.demo else "openai",SCENE_IMAGE_MODEL
        if task.operation in ("location_panorama","simple_location"):
            from .dual_panorama import PANORAMA_MODEL
            provider, model = "demo" if self.demo else "openai", PANORAMA_MODEL
        if task.operation == "simple_location":
            options["size"] = "1536x864"
        if task.operation == "location_panorama":
            from .location_bridges import BRIDGES
            from .master_prefill import SIZES
            group = "bridges" if task.subject.rsplit("__",1)[-1] in BRIDGES else "masters"
            options.update(size="x".join(map(str,SIZES[group])),panorama=True,
                pair_prompt=state.location_pair_prompts.get(task.subject.rsplit("__",1)[0],{}).get(group,""),
                master_source="four-directional-masters-110-v1" if group == "masters" else "legacy_bridge")
        if task.stage in ("character_references", "backgrounds"):
            from .visual_generation import VisualGeneration, EDIT_MODEL
            inputs = VisualGeneration(self)
            spec = state.generation_inputs.get(inputs.key(task.stage, task.subject))
            if spec and task.operation not in ("location_panorama","simple_location"):
                for name in ("style_prompt", "generic_prompt", "style_reference_hash", "character_image_hash",
                             "character_image_intact", "background_image_hash", "background_image_intact"):
                    options.pop(name, None)
                options["generation_input"] = {"mode": spec["mode"]}
                if spec["mode"] == "text":
                    options["generation_input"]["prompt"] = spec["prompt"]
                else:
                    options["reference_role"] = "design"
                    try:
                        options["generation_input"]["image_hash"] = file_hash(inputs.selected_image(task.stage, task.subject, state))
                    except (KeyError, ValueError, OSError):
                        options["generation_input"]["image_hash"] = "missing"
                if modification:
                    model = EDIT_MODEL
        if state.project_style_rules and task.stage in ASSET_STAGES[:4]:
            from .style_rules import audio_rules
            rules = audio_rules(state.project_style_rules, kind="voice") if task.stage == "voices" else state.project_style_rules
            if rules:
                options["project_style_rules"] = rules
        if state.project_style_image and task.stage in ASSET_STAGES[:3]:
            from .style_rules import StyleRules
            options["project_style_image_hash"] = state.project_style_image["hash"]
            try:
                StyleRules(self).image(state)
                options["project_style_image_intact"] = True
            except (OSError, ValueError):
                options["project_style_image_intact"] = False
        return ProviderConfig(provider=provider, model=model, options=options)

    def _hash(self, task, state):
        return input_hash({"stage": task.stage, "subject": task.subject, "prompt": task.prompt,
                           "voice": task.voice_id, "speaker": task.speaker,
                           "config": self._config(task, state).model_dump(mode="json"),
                           "refs": [(ref, state.records[ref].file_hash, state.records[ref].asset.asset_id)
                                    if ref in state.records and state.records[ref].asset else (ref, None) for ref in task.refs]})

    def _intact(self, record):
        if not record or not record.asset:
            return False
        try:
            path = self.local_path(record.asset.file_path)
            return path.is_file() and file_hash(path) == record.file_hash
        except (OSError, ValueError):
            return False

    def _stage_done(self, stage, state, tasks):
        if stage == "consistency":
            return bool(state.consistency_report.get("passed") and state.consistency_report.get("signature") == self._signature(state))
        for task in tasks:
            if task.stage != stage:
                continue
            record = state.records.get(task.key)
            if not self.record_current(record, task, state):
                return False
        if stage == "subtitles":
            return self._scene_exports_intact(state)
        return True

    def _projection_outputs_current(self, task, record, state):
        from .dual_panorama import pair_ready, published_current
        return pair_ready(self, state, task.subject.rsplit("__", 1)[0]) and published_current(self, state, task, record)

    def record_current(self, record, task, state):
        if task.operation == "room_panorama":
            from .shared_room import valid_asset
            if not valid_asset(self,record,state.location_reference_sets[task.subject.rsplit("__",1)[0]]):
                return False
        if task.operation == "location_projection" and not self._projection_outputs_current(task, record, state):
            return False
        if task.operation in ("location_view", "location_panorama", "room_canonical_raw", "room_canonical_enhanced") and any(not state.records.get(ref) or state.records[ref].status != "succeeded"
                or not self._intact(state.records[ref]) for ref in task.refs):
            return False
        if task.operation == "panorama_view":
            source = state.records.get(task.refs[0])
            if not source or source.status != "succeeded" or not self._intact(source):
                return False
        return bool(record and record.status == "succeeded"
                    and (record.applied_input_hash or record.input_hash) == self._hash(task, state)
                    and self._intact(record))

    def character_requirements(self, subject, state, tasks=None):
        from .character_readiness import character_readiness
        return character_readiness(self, subject, state, tasks)[0]

    def _character_approval_hash(self, subject, state, *, completion=None):
        character = next(c for c in self.creative.artifact("characters").characters if c.character_id == subject)
        record = state.records["character_references:" + subject]
        content = {"character": character.model_dump(mode="json"),
                           "revision": record.revision, "asset": record.asset.asset_id if record.asset else None,
                           "input": record.applied_input_hash or record.input_hash, "file": record.file_hash}
        if self.manager.load(self.project_id).workflow_version == "world_episode":
            from .character_readiness import character_readiness
            content["completion"] = completion if completion is not None else character_readiness(self, subject, state)[1]
        return input_hash(content)

    def character_approved(self, subject, state, tasks=None):
        approval = state.character_approvals.get(subject)
        if not approval:
            return False
        from .character_readiness import character_readiness
        checks, completion = character_readiness(self, subject, state, tasks)
        if not all(checks.values()):
            return False
        task = next((t for t in (tasks if tasks is not None else self.tasks(state))
                     if t.stage == "character_references" and t.subject == subject), None)
        return bool(task and self.record_current(state.records.get(task.key), task, state)
                    and approval.content_hash == self._character_approval_hash(subject, state, completion=completion))

    def _sync_character_approvals(self, state, tasks, manifest):
        subjects = [t.subject for t in tasks if t.stage == "character_references"]
        old = manifest.asset_approvals.get("characters")
        # Existing group approvals become individual approvals on first load.
        if old and not state.character_approvals and self._stage_done("character_references", state, tasks) and old.content_hash == self._gate_hash("characters", state, tasks):
            state.character_approvals = {subject: old.model_copy(update={"content_hash": self._character_approval_hash(subject, state)})
                                         for subject in subjects}
        for subject in list(state.character_approvals):
            if subject not in subjects or not self.character_approved(subject, state, tasks):
                state.character_approvals.pop(subject)
        if subjects and all(self.character_approved(subject, state, tasks) for subject in subjects):
            digest = self._gate_hash("characters", state, tasks)
            if not old or old.content_hash != digest:
                manifest.asset_approvals["characters"] = ApprovalRecord(content_hash=digest, note="All current characters approved individually")
        else:
            manifest.asset_approvals.pop("characters", None)

    def revisions(self, stage, subject):
        from .asset_revisions import revisions
        return revisions(self, stage, subject)

    def apply_revision(self, stage, subject, asset_id):
        from .asset_revisions import apply_revision
        return apply_revision(self, stage, subject, asset_id)

    def delete_revision(self, stage, subject, asset_id):
        from .revision_deletion import delete_image
        return delete_image(self, stage, subject, asset_id)

    def _scene_exports_intact(self, state):
        expected = {s.scene_id for s in self.creative.artifact("storyboard").scenes}
        if set(state.scenes) != expected:
            return False
        if any(not state.records.get(f"scene_images:{scene.scene_id}") or
               state.records[f"scene_images:{scene.scene_id}"].asset is None or
               state.records[f"scene_images:{scene.scene_id}"].asset.file_path != scene.image
               for scene in state.scenes.values()):
            return False
        try:
            return all(file_hash(self.local_path(path)) == digest for scene in state.scenes.values()
                       for path, digest in scene.hashes.items()) and all(scene.hashes for scene in state.scenes.values())
        except (OSError, ValueError):
            return False

    def _signature(self, state):
        return input_hash({"source": state.source_hash,
                           "records": {key: [r.input_hash, r.file_hash, r.status] for key, r in state.records.items()},
                           "scenes": {key: scene.hashes for key, scene in state.scenes.items()}})

    def _gate_hash(self, gate, state, tasks):
        stages = {"characters": {"character_references"}, "backgrounds": {"backgrounds"}}.get(gate, set(ASSET_STAGES))
        return input_hash({"source": state.source_hash if gate == "assets" else "reference-approval-v2",
                           "records": {t.key: [state.records[t.key].input_hash, state.records[t.key].file_hash,
                                               state.records[t.key].revision, state.records[t.key].applied_input_hash,
                                               state.records[t.key].asset.asset_id if state.records[t.key].asset else None] for t in tasks if t.stage in stages and t.key in state.records},
                           "scenes": {key: scene.hashes for key, scene in state.scenes.items()} if gate == "assets" else {}})

    def _next(self, state, tasks, manifest):
        library_mode = manifest.workflow_version == "world_episode" and not manifest.world_project_id
        stages = ASSET_STAGES[:2] if library_mode else ASSET_STAGES[2:] if manifest.world_project_id else ASSET_STAGES
        for stage in stages:
            if stage != "character_references" and not library_mode and not self._creative_ready(manifest):
                return "waiting_for_creative"
            if stage == "backgrounds":
                from .dual_panorama import is_key, approved, digest, MASTERS, pair_ready
                from .location_pairs import current
                for lid,spec in state.location_reference_sets.items():
                    if not is_key(spec):
                        continue
                    for group,members in (("masters",MASTERS),):
                        if not current(self,state,lid,group):
                            return "backgrounds"
                        if not all(approved(self,state,lid,name) for name in members):
                            return "background_master_review"
            if not self._stage_done(stage, state, tasks):
                return stage
            gate = {"character_references": "characters", "backgrounds": "backgrounds", "consistency": "assets"}.get(stage)
            if gate and gate not in manifest.asset_approvals:
                return {"characters": "character_review", "backgrounds": "background_review", "assets": "asset_review"}[gate]
        return "complete"

    def _save(self, state):
        state.updated_at = utc_now()
        atomic_write(self.state_path, state.model_dump_json(by_alias=True, indent=2) + "\n")
        manifest = self.manager.load(self.project_id)
        manifest.asset_step = state.next_stage
        manifest.asset_state = "approved" if state.next_stage == "complete" else "review" if state.next_stage.endswith("review") else "generating"
        if manifest.asset_state == "approved":
            manifest.status = ProjectStatus.RENDERED if manifest.render_state in ("review", "approved") else ProjectStatus.ASSETS_READY
        elif manifest.status == ProjectStatus.ASSETS_READY:
            manifest.status = ProjectStatus.STORYBOARD_GENERATED
        if manifest.asset_state != "approved" and manifest.render_state != "not_started":
            manifest.render_state = "stale"
            manifest.final_approval = None
            if manifest.status == ProjectStatus.RENDERED:
                manifest.status = ProjectStatus.STORYBOARD_GENERATED
        self.manager.save(manifest)
        if self.on_progress:
            try:
                self.on_progress(state.model_copy(deep=True))
            except Exception:
                logger.warning("Could not update asset production display")

    def _refresh(self):
        source = self._source()
        state = self.load()
        manifest = self.manager.load(self.project_id)
        if not state.location_workflow_version and manifest.workflow_version == "world_episode" and not manifest.world_project_id:
            # Restore the requested camera-region workflow once; keep immutable media/history.
            for spec in state.location_reference_sets.values():
                if spec.get("generation_method") == "shared_3d":
                    spec["generation_method"] = "dual_panorama"
            state.location_workflow_version = 1
        if manifest.workflow_version == "world_episode" and not state.per_asset_references:
            from .reference_input_migration import retire_shared_style
            retire_shared_style(self, state, manifest)
        if state.character_prompt_version < 1:
            from .reference_input_migration import narrow_character_context
            narrow_character_context(self, state, manifest)
        self._style_reference(state)
        from .dual_panorama import restore_master_sources
        restore_master_sources(self,state)
        if state.source_hash != source:
            state.source_hash = source
            manifest.asset_approvals.pop("assets", None)
            state.consistency_report = {}
            state.scenes = {}
            for record in state.records.values():
                if record.stage not in ("character_references", "backgrounds"):
                    record.status = "stale"
        state.provider_mode = "demo" if self.demo else "openai"
        tasks = self.tasks(state)
        active_keys = {task.key for task in tasks}
        active_keys.update(ref for task in tasks for ref in task.refs if ref.startswith("backgrounds:") and "__shot_" in ref)
        active_keys.update(reference["key"] for spec in state.location_reference_sets.values()
                           for reference in spec.get("detail_references",{}).values())
        for key in list(state.records):
            if key not in active_keys and (self._creative_ready(manifest) or state.records[key].stage == "character_references"
                    or manifest.workflow_version == "world_episode" and not manifest.world_project_id and state.records[key].stage == "backgrounds"):
                if state.records[key].stage == "backgrounds" and state.records[key].asset:
                    retired = state.records[key]
                    archive = self.path / "history" / "assets" / "backgrounds" / retired.subject / (retired.asset.asset_id + ".json")
                    if not archive.exists():
                        atomic_write(archive, retired.model_dump_json(by_alias=True, indent=2))
                del state.records[key]
        for task in tasks:
            record = state.records.get(task.key)
            if record and record.status in ("succeeded", "stale"):
                if task.operation == "panorama_view" and (not state.records.get(task.refs[0]) or state.records[task.refs[0]].status != "succeeded"):
                    record.status, record.stale_reason = "stale", "The source panorama needs generation or approval of a saved revision; rebuild this view afterward."
                elif task.operation in ("location_view", "location_panorama", "location_projection", "room_canonical_raw", "room_canonical_enhanced") and any(not state.records.get(ref) or state.records[ref].status != "succeeded"
                        or not self._intact(state.records[ref]) for ref in task.refs):
                    record.status, record.stale_reason = "stale", "A source panorama needs a current image; rebuild dependent projections."
                elif task.operation == "room_panorama" and not self.record_current(record,task,state):
                    record.status,record.stale_reason = "stale","The shared 3D geometry, panorama or render metadata changed; render again."
                elif task.operation == "location_projection" and not self._projection_outputs_current(task, record, state):
                    record.status, record.stale_reason = "stale", "Panorama approvals/seam QA or projection inputs changed. Rebuild from approved masters."
                elif (record.applied_input_hash or record.input_hash) != self._hash(task, state):
                    record.status, record.stale_reason = "stale", "Generation inputs changed. Generate again or apply a saved revision, then approve."
                elif not self._intact(record):
                    record.status, record.stale_reason = "stale", "The saved asset file is missing or its contents changed."
                else:
                    record.status, record.stale_reason = "succeeded", ""
        self._sync_character_approvals(state, tasks, manifest)
        from .background_library import BackgroundLibrary
        BackgroundLibrary(self).sync(state, tasks, manifest)
        for gate, stages in (("characters", ("character_references",)), ("backgrounds", ("backgrounds",)), ("assets", ASSET_STAGES)):
            approval = manifest.asset_approvals.get(gate)
            if approval and (not all(self._stage_done(s, state, tasks) for s in stages)
                             or approval.content_hash != self._gate_hash(gate, state, tasks)):
                manifest.asset_approvals.pop(gate, None)
        if "characters" not in manifest.asset_approvals or "backgrounds" not in manifest.asset_approvals:
            manifest.asset_approvals.pop("assets", None)
        self.manager.save(manifest)
        state.next_stage = self._next(state, tasks, manifest)
        self._save(state)
        return state, tasks

    def inspect(self):
        self.creative.inspect()
        with project_lock(self.path):
            return self._refresh()[0]

    def _require_stage(self, stage, manifest):
        if stage not in ("character_references","backgrounds"):
            from .storyboard_asset_check import require
            require(self.manager,self.project_id)
        library_mode = manifest.workflow_version == "world_episode" and not manifest.world_project_id
        if library_mode:
            from .studio import WorldStudio
            WorldStudio(self.manager, self.project_id).require_approved()
            if stage not in ASSET_STAGES[:2]:
                raise ApprovalRequired("Generate scene inputs in an approved episode on Animations.")
        if stage != "character_references" and not library_mode and not self._creative_ready(manifest):
            raise ApprovalRequired("Complete the creative pipeline and approve the story and storyboard before generating other assets.")
        if stage != "character_references" and not manifest.world_project_id and not self.independent_backgrounds(stage, manifest) and "characters" not in manifest.asset_approvals:
            raise ApprovalRequired("Review and approve character references before continuing asset production.")
        if stage not in ("character_references", "backgrounds") and not manifest.world_project_id and "backgrounds" not in manifest.asset_approvals:
            raise ApprovalRequired("Review and approve backgrounds before generating scene assets.")
        if manifest.world_project_id and stage in ("voices", "subtitles", "consistency"):
            from .scene_production import SceneProduction
            from .rendering import RenderPipeline
            options = RenderPipeline(self.manager, self.project_id).load().options
            service = SceneProduction(self.manager, self.project_id)
            if not all(service.visual_current(s.scene_id, options) for s in self.creative.artifact("storyboard").scenes):
                raise ApprovalRequired("Generate the current animated scenes before Voices & input.")
        if manifest.world_project_id and stage == "scene_images":
            from .location_references import LocationReferenceResolver
            production = self.load()
            resolver = LocationReferenceResolver(self,production)
            for scene in self.creative.artifact("storyboard").scenes:
                if not resolver.resolve(scene.location_id,scene.camera_start_angle,scene.camera_end_angle,scene.target_area,
                    character_position=scene.character_position,interaction_target=scene.interaction_target):
                    raise ApprovalRequired(f"Scene {scene.scene_id} needs generated and approved location references for {scene.location_id}.")

    def _cost(self, task, usage, provider=None):
        if provider in ("demo", "local") or (provider is None and self.demo):
            return Decimal("0"), True
        if task.stage in ("character_references", "backgrounds", "scene_images"):
            rate = self.settings.runway_image_cost_per_image if provider == "runway" else self.settings.image_cost_per_image
            return (rate * usage.get("image_count", 0), True) if rate is not None and "image_count" in usage else (Decimal("0"), False)
        if task.stage == "voices":
            rate = self.settings.elevenlabs_tts_cost_per_million_characters if provider == "elevenlabs" else self.settings.tts_cost_per_million_characters
            return (rate * usage["audio_characters"] / Decimal(1000000), True) if rate is not None and "audio_characters" in usage else (Decimal("0"), False)
        rate = self.settings.alignment_cost_per_minute
        return (rate * Decimal(str(usage["audio_seconds"])) / Decimal(60), True) if rate is not None and "audio_seconds" in usage else (Decimal("0"), False)

    def _event(self, task, outcome, **details):
        with (self.path / "logs" / "assets.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": utc_now().isoformat(), "asset": task.key, "status": outcome, **details}, ensure_ascii=False) + "\n")

    def _run_task(self, task, state, *, force=False):
        spec = None
        if task.stage == "voices":
            from .animation import require_voice
            require_voice(self.manager, self.project_id, task.speaker, require_performance=True)
        with self._state_lock:
            record = state.records.setdefault(task.key, AssetRecord(stage=task.stage, subject=task.subject))
            fingerprint = self._hash(task, state)
            from .location_pairs import reusable
            if not force and reusable(self,task,record) and record.status not in ("failed", "running") and (record.applied_input_hash or record.input_hash) == fingerprint and self._intact(record) and (task.operation != "location_projection" or self._projection_outputs_current(task, record, state)):
                record.status = "succeeded"
                record.error = None
                self._save(state)
                return
            for ref in task.refs:
                if ref not in state.records or state.records[ref].status != "succeeded" or not self._intact(state.records[ref]):
                    raise ValueError(f"Generate the referenced asset first: {ref}")
            manifest = self.manager.load(self.project_id)
            affected = {"character_references": ("characters", "backgrounds", "assets"), "backgrounds": ("backgrounds", "assets")}.get(task.stage, ("assets",))
            for gate in affected:
                manifest.asset_approvals.pop(gate, None)
            if task.stage == "character_references":
                state.character_approvals.pop(task.subject, None)
            if task.operation == "location_panorama":
                from .location_bridges import BRIDGES
                from .dual_panorama import MASTERS
                lid,name = task.subject.rsplit("__",1)
                state.location_master_approvals.pop(lid+":"+name,None)
                if name in BRIDGES:
                    for master in MASTERS:
                        state.location_master_approvals.pop(lid+":"+master,None)
                state.background_approvals.pop(lid,None)
            self.manager.save(manifest)
            state.consistency_report = {}
            record.status = "running"
            record.error = None
            record.stale_reason = ""
            state.next_stage = task.stage
            self._save(state)
            self._event(task, "running")
            config = self._config(task, state)
            suffix = ".png" if task.stage in ("character_references", "backgrounds", "scene_images") else ".wav" if task.stage == "voices" else ".json"
            folder = "images" if suffix == ".png" else "audio" if suffix == ".wav" else "subtitles"
            destination = self.path / folder / task.stage / task.subject / f"{utc_now().strftime('%Y%m%d%H%M%S%f')}{suffix}"
            config.options["output_path"] = str(destination)
            if force:
                record.remote_job_id = None
            config.options["job_id"] = record.remote_job_id if record.remote_job_hash == fingerprint and record.remote_status not in ("failed", "cancelled") else None
            if config.options["job_id"] and record.remote_request_file:
                config.options["request_snapshot_path"] = str(self.local_path(record.remote_request_file))
            def update_job(job_id, status, progress):
                with self._state_lock:
                    record.remote_job_id, record.remote_job_hash, record.remote_status = job_id, fingerprint, status
                    snapshot = destination.with_suffix(destination.suffix + ".request.json")
                    if snapshot.is_file():
                        record.remote_request_file = snapshot.relative_to(self.path).as_posix()
                    config.options["job_id"] = job_id
                    self._save(state)
            config.options["job_update"] = update_job
            provider = self._provider_for(task, state)
        for attempt in range(self.settings.llm_max_retries + 1):
            if isinstance(provider, ThreadLocalUsage) and isinstance(getattr(type(provider), "last_usage", None), property):
                provider.last_usage = {}
            with self._state_lock:
                record.attempts += 1
                self._save(state)
            outcome = "failed"
            try:
                if task.stage == "subtitles":
                    source_audio = self.local_path(state.records[task.refs[0]].asset.file_path)
                    alignment = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.align, source_audio, task.prompt, config.options["language"], config, task_key=f"assets:{task.key}")
                    alignment = Alignment.model_validate(alignment.model_dump())
                    if abs(alignment.duration - wav_duration(source_audio)) > 0.05:
                        raise ValueError("Alignment duration does not match its source audio")
                    if not alignment.segments:
                        raise ProviderError("Alignment returned no subtitle segments.")
                    atomic_write(destination, alignment.model_dump_json(by_alias=True, indent=2) + "\n")
                    asset = GeneratedAsset(type="subtitle", provider=config.provider, model=config.model,
                                           file_path=str(destination), duration=alignment.duration, prompt_hash=input_hash(task.prompt))
                elif task.operation in ("room_panorama", "room_canonical_raw", "room_canonical_enhanced"):
                    from .shared_room import generate
                    from .background_library import BackgroundLibrary
                    prompt,spec,references = task.prompt,None,[]
                    if task.operation == "room_panorama":
                        asset = generate(self,state,task,config)
                    elif task.operation == "room_canonical_raw":
                        from .room_canonical import raw_generate
                        asset = raw_generate(self,state,task,config)
                    else:
                        from .room_canonical import enhancement_request
                        from PIL import Image
                        prompt,references = enhancement_request(self,state,task)
                        asset = tracked_call(self.manager,self.project_id,f"assets:{task.stage}",
                            provider.generate,prompt,references,config,task_key=f"assets:{task.key}")
                        with Image.open(asset.file_path) as image:
                            if config.provider != "demo" and abs(image.width/image.height-1.5) > .01:
                                raise ValueError("Enhanced canonical aspect ratio changed; regenerate without changing framing.")
                            image.convert("RGB").resize((1536,1024)).save(asset.file_path)
                        asset.generation_params["canonicalEnhancement"] = {
                            "yaw":int(task.subject.rsplit("_",1)[1]),"pitch":0,"fov":70,
                            "rawAssetId":state.records[task.refs[0]].asset.asset_id,
                            "cameraCenter":state.records[task.refs[0]].asset.generation_params["canonicalProjection"]["cameraCenter"],
                            "geometryCompliance":"requires_human_review", "spatialTruth":"shared_3d_room"}
                elif task.operation == "location_projection":
                    from .dual_panorama import generate_view
                    from .background_library import BackgroundLibrary
                    prompt, spec = task.prompt, None
                    references = [self.local_path(state.records[key].asset.file_path) for key in task.refs]
                    asset = generate_view(self, state, task, config)
                elif task.stage == "voices":
                    spoken_text = config.options.get("modification", {}).get("spoken_text") or task.prompt
                    asset = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.generate, spoken_text, config.options["voice"], config, task_key=f"assets:{task.key}")
                    if config.options.get("performance"):
                        from .media_utils import pad_performance
                        pad_performance(Path(asset.file_path), config.options["performance"])
                        asset.duration = wav_duration(Path(asset.file_path))
                    if wav_duration(Path(asset.file_path)) <= 0:
                        raise ValueError("Empty speech track")
                else:
                    from .background_library import BackgroundLibrary
                    from .visual_generation import VisualGeneration
                    inputs = VisualGeneration(self)
                    spec = (state.generation_inputs.get(inputs.key(task.stage, task.subject))
                            if task.stage in ("character_references", "backgrounds") else None)
                    if task.operation == "simple_location":
                        from .simple_locations import request
                        spec = None
                        modification = None
                        prompt,references = request(self,state,task)
                    elif task.stage == "scene_images":
                        from .scene_image_references import request as scene_request
                        modification = state.asset_modifications.get(task.key)
                        prompt,references = scene_request(self,state,task,config,runway=config.provider == "runway")
                    elif spec:
                        modification = state.asset_modifications.get(task.key)
                        if modification:
                            current = self.local_path(modification.source_path)
                            if not current.is_file() or file_hash(current) != modification.source_hash:
                                raise ValueError("The current image changed. Reopen Modify.")
                            references = [current]
                            prompt = "Modify the attached current image, preserving unrelated details. Request: " + modification.prompt
                            from .style_rules import apply_rules
                            prompt = apply_rules(prompt, state.project_style_rules)
                            from .style_rules import style_reference
                            prompt, references = style_reference(self, state, prompt, references)
                            if task.stage == "backgrounds":
                                from .visual_generation import BACKGROUND_EMPTY
                                prompt += "\n" + BACKGROUND_EMPTY
                        else:
                            prompt, references = inputs.request(task.stage, task.subject, state, spec=spec, shorten=True)
                    else:
                        prompt = task.prompt
                        if config.options.get("generic_prompt"):
                            prompt += "\nOverall genre, tone and style: " + config.options["generic_prompt"]
                            prompt += "\nMaintain this shared art direction consistently across characters, environments and scenes. Per-asset instructions add detail without changing the shared medium, rendering or palette."
                        if config.options.get("style_prompt"):
                            prompt += "\nAdditional visual style: " + config.options["style_prompt"]
                        references = [self.local_path(state.records[r].asset.file_path) for r in task.refs]
                        style_references = self._style_references(state)
                        if style_references:
                            references = style_references + references
                            prompt += (f"\nThe first {len(style_references)} attached image(s) are shared STYLE references. Follow their shared art medium, "
                                       "rendering, linework, color palette, lighting and texture consistently. "
                                       "Transfer style only, not its characters, objects, pose, composition or background. "
                                       "Any remaining images are approved identity and location references; preserve their "
                                       "faces, proportions, clothing and environment while applying the shared style. "
                                       "The shared style takes priority over conflicting per-asset style instructions.")
                        from .background_library import BackgroundLibrary
                        if task.stage == "backgrounds" and task.operation != "panorama_view":
                            library = BackgroundLibrary(self)
                            if library.subject_id(task.subject) in state.background_images:
                                references.append(library.image(state, task.subject))
                                prompt += (f"\nAttached image {len(references)} is the uploaded STYLE GUIDE ONLY. Transfer its art medium, rendering, "
                                    "linework, color palette, texture and lighting aesthetics. Do NOT copy its architecture, room layout, "
                                    "furniture, objects, people, viewpoint, crop, lens or composition. The style guide does not define panoramic coverage. Define the location's architecture, objects and spatial layout "
                                    "from the saved location description and World settings. Any other attached approved generated location views remain layout anchors.")
                        if task.stage == "character_references" and task.subject in state.character_images:
                            references.append(self.character_image(state, task.subject))
                            prompt += (f"\nAttached image {len(references)} is the uploaded STYLE GUIDE ONLY. Transfer its art medium, rendering, "
                                "linework, color palette, texture and lighting aesthetics. Do NOT copy the reference subject's identity, "
                                "face, hairstyle, clothing, body proportions, pose, objects or composition. Character identity and appearance "
                                "must follow the saved character description, clothing and fixed traits. Create a clean model sheet with "
                                "consistent front, side, back and three-quarter views. No text or watermarks.")
                        modification = state.asset_modifications.get(task.key)
                        if modification:
                            current_image = self.local_path(modification.source_path)
                            if not current_image.is_file() or file_hash(current_image) != modification.source_hash:
                                raise ValueError("The original image to modify is missing or changed. Reopen Modify with OpenAI.")
                            references.append(current_image)
                            prompt += ("\nThe LAST attached image is the CURRENT IMAGE TO EDIT. Modify that image according to "
                                       "the following request, preserving all unrelated details, character identity, clothing, "
                                       "composition and the shared visual style. Modification request: " + modification.prompt)
                        from .balanced_providers import RunwayImageProvider
                        from .style_rules import apply_rules
                        prompt = apply_rules(prompt, state.project_style_rules)
                        from .style_rules import style_reference
                        prompt, references = style_reference(self, state, prompt, references)
                        if isinstance(provider, RunwayImageProvider):
                            from .provider_prompts import compile_prompt
                            prompt = compile_prompt(self.creative, prompt, task_key=f"assets:{task.key}:prompt")
                    from .style_rules import reference_tags
                    tags = config.options.get("reference_tags") or reference_tags(self, state, references)
                    if tags:
                        config.options["reference_tags"] = tags
                    asset = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.generate, prompt, references, config, task_key=f"assets:{task.key}")
                    if task.operation == "location_panorama":
                        from .master_prefill import finish
                        finish(self,state,task,asset)
                    if task.operation == "panorama":
                        from .background_panorama import normalize_panorama, assess_panorama
                        normalize_panorama(asset.file_path)
                        asset.generation_params.update(projectionRequested="equirectangular-360x180", coverage360Verified=False,
                            panoramaAssessment=assess_panorama(asset.file_path), geometryReviewRequired=True, seamReviewRequired=True)
                    from PIL import Image
                    with Image.open(asset.file_path) as image:
                        image.verify()
                path = Path(asset.file_path).resolve()
                if not path.is_relative_to(self.path):
                    raise ValueError("Provider returned an asset outside the project")
                with self._state_lock:
                    cost, _ = self._cost(task, getattr(provider, "last_usage", {}) or {}, config.provider)
                    asset.cost = cost
                    asset.file_path = path.relative_to(self.path).as_posix()
                    asset.source_scene_id = task.scene_id
                    uploaded_role = ("current_image" if modification else "design_reference" if spec["mode"] == "image" else None) if spec else (
                        "style_only" if task.stage == "character_references" and task.subject in state.character_images or
                        task.stage == "backgrounds" and BackgroundLibrary(self).subject_id(task.subject) in state.background_images else None)
                    asset.generation_params = {**asset.generation_params, "prompt": prompt if suffix == ".png" else spoken_text if suffix == ".wav" else task.prompt,
                                               "inputMode": spec["mode"] if spec else None,
                                               "inputImages": [path.relative_to(self.path).as_posix() for path in references] if suffix == ".png" else [],
                                               "genericPrompt": state.generic_prompt if self.shared_style_enabled(state) else "",
                                               "styleReference": state.style_reference if suffix == ".png" and self.shared_style_enabled(state) else None,
                                               "styleReferenceHash": state.style_reference_hash if suffix == ".png" and self.shared_style_enabled(state) else "",
                                               "styleReferences": state.style_references if suffix == ".png" and self.shared_style_enabled(state) else [],
                                               "characterImage": state.character_images.get(task.subject) if task.stage == "character_references" else None,
                                               "backgroundImage": state.background_images.get(BackgroundLibrary(self).subject_id(task.subject)) if task.stage == "backgrounds" else None,
                                               "uploadedReferenceRole": uploaded_role,
                                               "stylePrompt": config.options.get("style_prompt", ""), "references": task.refs,
                                               "referenceHashes": {r: state.records[r].file_hash for r in task.refs},
                                               "logicalVoiceId": task.voice_id, "speaker": task.speaker,
                                               "config": {key: value for key, value in config.options.items() if key not in ("output_path", "job_update", "job_id")},
                                               "demo": config.provider == "demo"}
                    if task.stage == "backgrounds":
                        lid = BackgroundLibrary(self).subject_id(task.subject)
                        layout = state.location_reference_sets.get(lid, {})
                        from .background_library import locations
                        location = next((loc for loc in locations(self.creative) if loc.location_id == lid), None)
                        asset.generation_params["backgroundContext"] = {
                            "locationId": lid, "referenceType": layout.get("reference_type", "legacy"),
                            "title": location.name if location else None, "description": location.description if location else None,
                            "floorPlan": layout.get("floor_plan", ""), "objectLayout": layout.get("object_layout", {}),
                            "styleRules": state.project_style_rules,
                            "styleImage": state.project_style_image,
                            "inputMode": spec["mode"] if spec else None,
                            "sourcePrompt": spec.get("prompt", "") if spec and spec["mode"] == "text" else task.prompt if not spec else None,
                            "yawReferencePolicy": "canonical_panorama_projection" if task.operation == "location_projection" else None,
                            "generation_mode": "dual_panorama_projection" if task.operation == "location_projection" else "panorama_master" if task.operation == "location_panorama" else None,
                            "targetYaw": int(task.subject.rsplit("view_", 1)[1]) if "__view_" in task.subject else None}
                        if task.operation == "simple_location":
                            asset.generation_params.update(inputMode="simple_prompt",uploadedReferenceRole="environment_guidance" if lid in state.background_images else None)
                            asset.generation_params["backgroundContext"].update(
                                floorPlan="",objectLayout={},inputMode="simple_prompt",
                                sourcePrompt=layout.get("simple_prompt",""),generation_mode="simple_master")
                        if task.operation == "location_panorama":
                            from .location_view_context import view_context
                            from .location_bridges import BRIDGES,context as bridge_context
                            name = task.subject.rsplit("__",1)[1]
                            group = "bridges" if name in BRIDGES else "masters"
                            asset.generation_params["inputMode"] = "paired_"+group
                            asset.generation_params["sharedPairPrompt"] = state.location_pair_prompts.get(lid,{}).get(group,"")
                            asset.generation_params["backgroundContext"].update(
                                sourcePrompt=asset.generation_params["sharedPairPrompt"],
                                generation_mode="paired_bridge" if group == "bridges" else "camera_region_master")
                            scope = (bridge_context(layout,name,spec.get("prompt","") if spec else "") if name in BRIDGES else
                                     view_context(layout,name,notes=spec.get("prompt","") if spec else ""))
                            asset.generation_params["viewScope"] = scope
                            asset.generation_params["backgroundContext"].update(floorPlan=scope["floorPlan"],objectLayout=scope["visibleObjects"],viewScope=scope)
                            from .floor_plan_guides import require_guides
                            guides = require_guides(self,lid,layout)
                            asset.generation_params["backgroundContext"]["cameraGuide"] = {
                                **guides["views"][task.subject.rsplit("__",1)[1]],
                                "signature":guides["signature"],"geometrySource":guides["geometrySource"],
                                "camera":guides["camera"],"cameraHeightMeters":guides["cameraHeightMeters"]}
                        request = asset.generation_params.get("runwayRequest")
                        if request:
                            for item in request["referenceInputs"]:
                                ref_path = Path(item["file"])
                                item["file"] = ref_path.relative_to(self.path).as_posix() if ref_path.is_relative_to(self.path) else str(ref_path)
                        asset.generation_params["locationReferencePack"] = [
                            {"key": key, "assetId": record.asset.asset_id, "file": record.asset.file_path, "fileHash": record.file_hash, "revision": record.revision}
                            for path in references for key, record in state.records.items()
                            if record.asset and self.local_path(record.asset.file_path) == path]
                    record.asset = GeneratedAsset.model_validate(asset.model_dump(exclude={"voice_id"}))
                    if task.operation == "location_panorama":
                        pair_key = task.subject.rsplit("__",1)[0]+":"+("bridges" if task.subject.endswith(("__east_bridge","__west_bridge")) else "masters")
                        pair_id = getattr(self,"_location_pair_batches",{}).get(pair_key)
                        if pair_id:
                            record.asset.generation_params["locationPairId"] = pair_id
                    record.file_hash = file_hash(path)
                    record.input_hash = fingerprint
                    record.applied_input_hash = ""
                    if task.stage in ("character_references", "backgrounds", "scene_images"):
                        record.revision = max([record.revision, *(r.revision for r in self.revisions(task.stage, task.subject))]) + 1
                    else:
                        record.revision += 1
                    record.status = "succeeded"
                    record.error = None
                    record.updated_at = utc_now()
                    atomic_write(self.path / "history" / "assets" / task.stage / task.subject / f"{asset.asset_id}.json",
                                 record.model_dump_json(by_alias=True, indent=2) + "\n")
                    if task.operation == "location_panorama":
                        from .dual_panorama import invalidate_world, root
                        import shutil
                        lid, name = task.subject.rsplit("__", 1)
                        invalidate_world(self, lid)
                        from .location_bridges import BRIDGES
                        from .directional_masters import alias
                        published = root(self,lid)/(name+".png" if name in BRIDGES else alias(name)+".png")
                        published.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(path, published)
                    outcome = "succeeded"
                    self._save(state)
                    self._event(task, "succeeded", revision=record.revision)
                    return
            except Exception as exc:
                with self._state_lock:
                    record.status = "failed"
                    record.error = str(exc)
                    record.updated_at = utc_now()
                    self._save(state)
                    self._event(task, "failed", error=record.error)
                if isinstance(exc, TransientProviderError) and attempt < self.settings.llm_max_retries:
                    time.sleep(min(2 ** attempt, 8))
                    with self._state_lock:
                        record.status = "running"
                        record.error = None
                        self._save(state)
                    continue
                if isinstance(exc, (OSError, ValueError, RuntimeError)):
                    raise
                raise ProviderError(f"Asset generation failed ({type(exc).__name__}); inspect this asset and retry.") from exc
            finally:
                with self._state_lock:
                    usage = dict(getattr(provider, "last_usage", {}) or {})
                    cost, known = self._cost(task, usage, config.provider)
                    CostTracker(self.manager, self.project_id).record(UsageRecord(
                        provider=config.provider, model=config.model, step=f"assets:{task.stage}", scene_id=task.scene_id, **request_fields(usage),
                        provider_job_id=usage.get("job_id"),
                        image_count=usage.get("image_count", 0), input_tokens=usage.get("input_tokens", 0),
                        output_tokens=usage.get("output_tokens", 0), audio_characters=usage.get("audio_characters", 0),
                        audio_seconds=usage.get("audio_seconds", 0), cost=cost, estimated_cost=cost,
                        pricing_known=known, outcome=outcome, retry_count=attempt))

    def _export_scenes(self, state, tasks):
        from difflib import SequenceMatcher
        for scene in self.creative.artifact("storyboard").scenes:
            voice_tasks = [t for t in tasks if t.stage == "voices" and t.scene_id == scene.scene_id]
            tracks, segments, words, cursor, expected, spoken = [], [], [], 0.0, [], []
            for task in voice_tasks:
                record = state.records[task.key]
                path = self.local_path(record.asset.file_path)
                duration = wav_duration(path)
                aligned = state.records[f"subtitles:{task.subject}"]
                alignment = Alignment.model_validate_json(self.local_path(aligned.asset.file_path).read_text(encoding="utf-8"))
                for cues, target in ((alignment.segments, segments), (alignment.words, words)):
                    target.extend(Cue(start=cursor + cue.start, end=cursor + cue.end, text=cue.text, speaker=task.speaker) for cue in cues)
                tracks.append({"assetId": record.asset.asset_id, "filePath": record.asset.file_path, "speaker": task.speaker,
                               "voiceId": task.voice_id, "providerVoice": state.voice_map[task.voice_id], "start": cursor, "duration": duration})
                expected.append(task.prompt)
                spoken.extend(cue.text for cue in alignment.segments)
                cursor += duration
            audio = f"audio/scenes/{scene.scene_id}.wav"
            srt, vtt, timing = (f"subtitles/{scene.scene_id}{suffix}" for suffix in (".srt", ".vtt", ".timing.json"))
            write_bytes(self.local_path(audio), combine_wav([self.local_path(t["filePath"]) for t in tracks]))
            duration = wav_duration(self.local_path(audio))
            atomic_write(self.local_path(srt), subtitle_text(segments))
            atomic_write(self.local_path(vtt), subtitle_text(segments, vtt=True))
            normalize = lambda text: re.sub(r"[\W_]", "", text.casefold())
            similarity = SequenceMatcher(None, normalize("".join(expected)), normalize("".join(spoken))).ratio() if expected else 1.0
            atomic_write(self.local_path(timing), json.dumps({"sceneId": scene.scene_id, "duration": duration,
                         "plannedDuration": scene.duration_sec, "tracks": tracks,
                         "segments": [c.model_dump(mode="json") for c in segments], "words": [c.model_dump(mode="json") for c in words],
                         "transcriptSimilarity": similarity, "alignmentSource": "demo" if self.demo else "audio-transcription"}, ensure_ascii=False, indent=2))
            image = state.records[f"scene_images:{scene.scene_id}"].asset.file_path
            state.scenes[scene.scene_id] = SceneAssets(scene_id=scene.scene_id, image=image, audio=audio,
                srt=srt, vtt=vtt, timing=timing, duration=duration,
                hashes={path: file_hash(self.local_path(path)) for path in (audio, srt, vtt, timing, image)})

    def _check_consistency(self, state, tasks):
        issues = []
        if self.shared_style_enabled(state) and state.style_reference:
            for record in state.records.values():
                if record.stage in ASSET_STAGES[:3] and record.asset and record.asset.generation_params.get("styleReferenceHash") != state.style_reference_hash:
                    issues.append({"severity": "error", "message": f"{record.subject} was not generated with the current shared style reference."})
        if not self._scene_exports_intact(state):
            issues.append({"severity": "error", "message": "Scene image/audio/subtitle exports are missing or modified."})
        for scene in state.scenes.values():
            timing = json.loads(self.local_path(scene.timing).read_text(encoding="utf-8"))
            if timing["transcriptSimilarity"] < 0.6:
                issues.append({"severity": "warning", "sceneId": scene.scene_id, "message": "Transcription differs substantially from the source text. Review pronunciation and subtitles."})
            if abs(scene.duration - timing["plannedDuration"]) > max(1, timing["plannedDuration"] * 0.15):
                issues.append({"severity": "warning", "sceneId": scene.scene_id, "message": "Actual voice duration differs from the storyboard estimate. Use the timing export for composition."})
        state.consistency_report = {"passed": not any(i["severity"] == "error" for i in issues),
                                    "styleReferences": state.style_references if self.shared_style_enabled(state) else [],
                                    "styleReference": state.style_reference if self.shared_style_enabled(state) else None,
                                    "styleReferenceHash": state.style_reference_hash if self.shared_style_enabled(state) else "",
                                    "styleReview": "Approved character and background reference provenance is checked. Human review must confirm visual style, character identity and location consistency.",
                                    "issues": issues, "signature": self._signature(state),
                                    "visualReviewRequired": True, "demo": self.demo,
                                    "checks": ["asset integrity", "reference hashes", "character/background provenance", "fixed voice IDs", "audio-derived timestamps", "transcript similarity"]}
        atomic_write(self.path / "asset_review.json", json.dumps(state.consistency_report, ensure_ascii=False, indent=2))

    def generate(self, stage, *, subject=None, subjects=None, force=False, use_saved_modification=False):
        if stage not in ASSET_STAGES:
            raise ValueError(f"Unknown asset stage: {stage}")
        if self.manager.load(self.project_id).world_project_id and stage in ("character_references", "backgrounds"):
            raise ApprovalRequired("Generate shared character/background references on the parent World's Assets page, then explicitly refresh this episode's shared assets.")
        if stage == "scene_images" and self.manager.load(self.project_id).world_project_id:
            # Refresh selected shared revisions before entering this project's
            # non-reentrant generation lock. Preserve episode story approvals.
            from .check_asset_sync import synchronize
            synchronize(self.manager,self.project_id)
        self.creative.inspect()
        with project_lock(self.path):
            if stage == "backgrounds" and self.independent_backgrounds(stage):
                from .background_library import BackgroundLibrary
                prepared = self.load()
                if BackgroundLibrary(self).prepare_generation(prepared, subject=subject, subjects=subjects, force=force):
                    atomic_write(self.state_path, prepared.model_dump_json(by_alias=True, indent=2))
                # Legacy view IDs are selection aliases. New requests produce
                # only one panorama per selected background.
                requested = [subject] if subject is not None else subjects
                if requested is not None:
                    expanded = []
                    from .location_references import view_names
                    for item in requested:
                        lid = BackgroundLibrary(self).subject_id(item)
                        spec = prepared.location_reference_sets.get(lid)
                        if spec:
                            from .shared_room import enabled
                            if enabled(spec):
                                from .room_geometry import scene_from_spec
                                scene_from_spec(spec)
                                from .room_canonical import tasks as canonical_tasks
                                allowed = [t.subject for t in canonical_tasks(lid,spec,Task)]
                                if item != lid and item not in allowed:
                                    raise ValueError("Choose a shared panorama or canonical view task.")
                                # A location request advances the full graph; explicit subjects regenerate one output.
                                expanded.extend(allowed if item == lid else [item])
                                continue
                            if spec["reference_type"] in ("KEY", "KEY_360") and (not spec.get("floor_plan", "").strip() or not spec.get("object_layout")):
                                raise ValueError("Locations require a floor plan and object-layout metadata before generation.")
                            if spec["reference_type"] in ("KEY", "KEY_360"):
                                from .floor_plan_guides import require_guides
                                require_guides(self,lid,spec)
                                from .dual_panorama import next_subjects, pair_ready, approved
                                if item == lid:
                                    choices = next_subjects(self, prepared, lid)
                                    if not choices:
                                        raise ApprovalRequired("Review and approve all four directional masters.")
                                    expanded.extend(choices)
                                else:
                                    from .dual_panorama import MASTERS
                                    allowed = {lid+"__"+name for name in MASTERS} | {lid+"__master"}
                                    if item not in allowed:
                                        raise ValueError("This background workflow generates only four directional masters.")
                                    expanded.append(item.replace("__master", "__front_panorama") if item.endswith("__master") else item)
                            else:
                                expanded.extend([lid+"__"+view for view in view_names(spec)] if item == lid else [item])
                        else:
                            expanded.append(lid + "__panorama")
                    subject, subjects = None, list(dict.fromkeys(expanded))
                else:
                    for lid,spec in prepared.location_reference_sets.items():
                        from .shared_room import enabled
                        if enabled(spec):
                            from .room_geometry import scene_from_spec
                            scene_from_spec(spec)
                            continue
                        if spec["reference_type"] in ("KEY", "KEY_360") and (not spec.get("floor_plan", "").strip() or not spec.get("object_layout")):
                            raise ValueError("Locations require a floor plan and object-layout metadata before generation.")
                        if spec["reference_type"] in ("KEY","KEY_360"):
                            from .floor_plan_guides import require_guides
                            require_guides(self,lid,spec)
            state, tasks = self._refresh()
            if self.shared_style_enabled(state) and stage != "character_references" and not self.independent_backgrounds(stage) and not self.has_generic_prompt(state):
                raise ValueError("Add a generic type prompt using text, a reference image, or both before generating assets.")
            state.stopped = False
            manifest = self.manager.load(self.project_id)
            self._require_stage(stage, manifest)
            index = ASSET_STAGES.index(stage)
            prior_stages = ASSET_STAGES[2:index] if manifest.world_project_id else ASSET_STAGES[:index]
            if not self.independent_backgrounds(stage, manifest) and any(not self._stage_done(prior, state, tasks) for prior in prior_stages):
                raise ValueError("Complete earlier asset stages before continuing.")
            if subjects is not None and (not subjects or subject is not None):
                raise ValueError("Select one or more subjects, without a single subject filter")
            if stage == "backgrounds" and (subject is not None or subjects is not None):
                wanted = set(subjects or [subject])
                while True:
                    sources = {ref for t in tasks if t.subject in wanted and t.operation in ("panorama_view", "location_view", "location_panorama", "location_projection", "room_canonical_raw", "room_canonical_enhanced") for ref in t.refs}
                    needed = {t.subject for t in tasks if t.key in sources and t.subject not in wanted
                              and not self.record_current(state.records.get(t.key), t, state)}
                    if not needed:
                        break
                    wanted.update(needed)
                if wanted != set(subjects or [subject]):
                    subjects, subject = list(wanted), None
            selected = [t for t in tasks if t.stage == stage and (subject is None or t.subject == subject)
                        and (subjects is None or t.subject in subjects)]
            if subjects is not None and set(subjects) != {t.subject for t in selected}:
                raise ValueError("Unknown selected asset subject")
            if subject is not None and not selected:
                raise ValueError("No matching asset subject")
            if stage == "backgrounds" and self.independent_backgrounds(stage) and requested is None:
                from .dual_panorama import is_key, next_subjects
                allowed = {subject for lid, spec in state.location_reference_sets.items() if is_key(spec)
                           for subject in next_subjects(self, state, lid)}
                selected = [t for t in selected if not is_key(state.location_reference_sets.get(t.subject.rsplit("__", 1)[0])) or t.subject in allowed]
            if manifest.workflow_version == "world_episode" and stage in ("character_references", "backgrounds"):
                from .visual_generation import VisualGeneration
                inputs = VisualGeneration(self)
                for item in selected:
                    key = inputs.key(stage, item.subject)
                    state.generation_inputs.setdefault(key, inputs.get(stage, item.subject, state))
            selected_keys = {task.key for task in selected}
            for task in selected:
                explicit = state.generation_inputs.get(f"{task.stage}:{task.subject}")
                if task.stage == "backgrounds":
                    from .visual_generation import VisualGeneration
                    explicit = state.generation_inputs.get(VisualGeneration(self).key(task.stage, task.subject))
                if explicit and explicit["mode"] == "text":
                    # An unused saved image must not prevent text-only generation.
                    continue
                if task.stage == "character_references" and task.subject in state.character_images:
                    self.character_image(state, task.subject)
                if task.stage == "backgrounds" and task.operation not in ("panorama_view","location_panorama"):
                    from .background_library import BackgroundLibrary
                    library = BackgroundLibrary(self)
                    if library.subject_id(task.subject) in state.background_images:
                        library.image(state, task.subject)
                for ref in task.refs:
                    if ref not in selected_keys and (ref not in state.records or state.records[ref].status != "succeeded" or not state.records[ref].asset or not self._intact(state.records[ref])):
                        raise ValueError("Generate the required reference first: " + ref)
            # A new generation uses the configured provider. A prior edit is
            # retained as provenance for its result, not as a permanent routing
            # override for regeneration or recovery from a failed/stale edit.
            if stage in ASSET_STAGES[:3] and not use_saved_modification:
                for task in selected:
                    record = state.records.get(task.key)
                    if force or record and record.status in ("failed", "stale"):
                        previous_edit = state.asset_modifications.get(task.key)
                        if previous_edit:
                            archive = self.path / "history" / "asset_modifications" / task.stage / task.subject / f"{utc_now().strftime('%Y%m%d%H%M%S%f')}.json"
                            atomic_write(archive, previous_edit.model_dump_json(by_alias=True, indent=2))
                            state.asset_modifications.pop(task.key, None)
            self._batch_stage = stage
            self._batch_tasks = {t.key: t for t in tasks if t.stage == stage}
            from .location_pairs import assign_batches
            assign_batches(self,state,selected,force=force)
            failures = self._generate_parallel(selected, state, force=force)
            state.stopped = self.stop_requested()
            subtitle_tasks_ready = all(state.records.get(task.key) and state.records[task.key].status == "succeeded"
                                       for task in tasks if task.stage == "subtitles")
            if stage == "subtitles" and not failures and subtitle_tasks_ready:
                self._export_scenes(state, tasks)
            elif stage == "consistency" and not state.stopped:
                self._check_consistency(state, tasks)
            self._save(state)
            if failures:
                raise ProviderError(f"{len(failures)} asset(s) failed. Successful items were preserved. " + failures[0])
            return self._refresh()[0]

    def _provider_for(self, task, state):
        if task.operation == "room_canonical_raw":
            return self.background_projection_provider
        if task.operation == "room_canonical_enhanced":
            return self.panorama_image_provider
        if task.operation == "room_panorama":
            return self.background_projection_provider
        if task.operation in ("location_panorama","simple_location"):
            return self.panorama_image_provider
        if task.operation in ("panorama_view", "location_projection"):
            return self.background_projection_provider
        if task.key in state.asset_modifications:
            if task.stage == "voices" and not self.demo and self.settings.tts_provider == "elevenlabs":
                return self.tts_provider
            return self.modification_tts_provider if task.stage == "voices" else self.modification_image_provider
        if task.stage == "character_references":
            return self.character_image_provider
        if task.stage == "scene_images":
            return self.scene_image_provider
        return self.image_provider if task.stage in ASSET_STAGES[:3] else self.tts_provider if task.stage == "voices" else self.alignment_provider

    def modify_with_openai(self, stage, subject, instructions, *, expected_asset_id=None):
        if stage not in ASSET_STAGES[:4] or not instructions.strip():
            raise ValueError("Choose an image or voice asset and enter a modification prompt.")
        self.creative.inspect()
        with project_lock(self.path):
            state, tasks = self._refresh()
            self._require_stage(stage, self.manager.load(self.project_id))
            if not self.independent_backgrounds(stage) and any(not self._stage_done(prior, state, tasks) for prior in ASSET_STAGES[:ASSET_STAGES.index(stage)]):
                raise ValueError("Complete earlier asset stages before modifying this asset.")
            record = state.records.get(f"{stage}:{subject}")
            task = next((t for t in tasks if t.stage == stage and t.subject == subject), None)
            if task and task.operation in ("room_panorama", "room_canonical_raw", "room_canonical_enhanced"):
                raise ValueError("Shared 3D panoramas are deterministic. Edit the room geometry/materials and render again.")
            if task and task.operation in ("panorama_view", "location_projection"):
                raise ValueError("Yaw views are deterministic projections. Modify/repair and approve the panorama masters, then rebuild views.")
            if task and task.operation == "location_panorama":
                raise ValueError("Panorama repairs require a bounded mask and protected-pixel verification; unrestricted AI edits are disabled.")
            if not record or not self._intact(record):
                raise ValueError("Generate an intact asset before modifying it.")
            if expected_asset_id and record.asset.asset_id != expected_asset_id:
                raise ValueError("The asset changed. Reopen Modify with OpenAI.")
            state.asset_modifications[f"{stage}:{subject}"] = AssetModification(
                prompt=instructions.strip(), source_path=record.asset.file_path, source_hash=record.file_hash,
                source_asset_id=record.asset.asset_id, spoken_text=record.asset.generation_params.get("prompt", "") if stage == "voices" else "")
            if stage == "voices" and not self.demo and self.settings.tts_provider == "elevenlabs":
                from .asset_models import VoiceDelivery
                from .modification import modify_content
                delivery = modify_content(self.creative, VoiceDelivery(), instructions, "voice_delivery")
                state.asset_modifications[f"{stage}:{subject}"].delivery = delivery.model_dump(mode="json")
            self._save(state)
        return self.generate(stage, subject=subject, force=True, use_saved_modification=True)

    def _parallel_task(self, task, state, force):
        provider = self._provider_for(task, state)
        safe = isinstance(provider, ThreadLocalUsage) and isinstance(getattr(type(provider), "last_usage", None), property)
        # Third-party adapters without isolated usage reporting remain serialized.
        with nullcontext() if safe else self._provider_lock:
            with self._state_lock:
                if self.stop_requested():
                    return
                state.generation_active += 1
                state.generation_queued -= 1
                self._task_activity[task.key] = "running"
                self._save(state)
            try:
                self._run_task(task, state, force=force)
            finally:
                with self._state_lock:
                    state.generation_active -= 1
                    state.generation_finished += 1
                    self._task_activity[task.key] = "finished"
                    self._save(state)

    def _generate_parallel(self, selected, state, *, force):
        limit = self.parallel_limit(self._batch_stage)
        state.max_parallel_tasks = limit
        state.generation_total = len(selected)
        state.generation_finished = state.generation_active = 0
        state.generation_queued = len(selected)
        self._save(state)
        self._batch_state = state
        self._batch_queue = deque((task, force) for task in selected)
        self._scheduled = {task.key for task in selected}
        self._task_activity = {task.key: "queued" for task in selected}
        self._batch_open = True
        failures = []
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="asset") as pool:
            pending = {}

            def fill():
                with self._state_lock:
                    while len(pending) < limit and self._batch_queue and not self.stop_requested():
                        task, forced = self._batch_queue.popleft()
                        pending[pool.submit(copy_context().run, self._parallel_task, task, state, forced)] = task

            fill()
            while pending:
                completed, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in completed:
                    task = pending.pop(future)
                    try:
                        future.result()
                    except (OSError, ValueError, RuntimeError) as exc:
                        failures.append(f"{task.subject}: {exc}")
                fill()
                with self._state_lock:
                    if not pending:
                        if self._batch_queue and not self.stop_requested():
                            fill()
                        else:
                            self._batch_open = False
        self._batch_open = False
        return failures

    def activity_snapshot(self):
        with self._state_lock:
            return tuple(sorted(self._task_activity.items()))

    def task_activity(self, stage, subject):
        with self._state_lock:
            return self._task_activity.get(f"{stage}:{subject}")

    def can_enqueue(self, stage, subject):
        with self._state_lock:
            key = f"{stage}:{subject}"
            return (self._batch_open and stage == self._batch_stage and not self.stop_requested()
                    and key in self._batch_tasks and key not in self._scheduled)

    def enqueue(self, stage, subject, *, force=False):
        with self._state_lock:
            if not self.can_enqueue(stage, subject):
                return False
            key = f"{stage}:{subject}"
            if stage == "character_references":
                from .character_visual_inputs import CharacterVisualInputs
                CharacterVisualInputs(self).apply(self._batch_state, subjects={subject})
                from .visual_generation import VisualGeneration
                inputs = VisualGeneration(self)
                inputs.apply(self._batch_state, keys={key})
                self._batch_state.generation_inputs.setdefault(key, inputs.get(stage, subject, self._batch_state))
            self._scheduled.add(key)
            self._task_activity[key] = "queued"
            self._batch_queue.append((self._batch_tasks[key], force))
            self._batch_state.generation_total += 1
            self._batch_state.generation_queued += 1
            self._save(self._batch_state)
            return True

    def approve_character(self, subject, *, expected_asset_id=None):
        self.creative.inspect()
        with project_lock(self.path):
            state, tasks = self._refresh()
            task = next((t for t in tasks if t.stage == "character_references" and t.subject == subject), None)
            record = state.records.get("character_references:" + subject)
            if not task or not self.record_current(record, task, state):
                raise ValueError("Generate or apply a current character reference before approving this character.")
            if expected_asset_id and record.asset.asset_id != expected_asset_id:
                raise ValueError("The character reference changed. Review its current revision before approving.")
            missing = [part for part, ready in self.character_requirements(subject, state, tasks).items() if not ready]
            if missing:
                raise ValueError("Complete character assets before approval: " + ", ".join(missing) + ".")
            state.character_approvals[subject] = ApprovalRecord(content_hash=self._character_approval_hash(subject, state))
            manifest = self.manager.load(self.project_id)
            self._sync_character_approvals(state, tasks, manifest)
            from .background_library import BackgroundLibrary
            BackgroundLibrary(self).sync(state, tasks, manifest)
            self.manager.save(manifest)
            state.next_stage = self._next(state, tasks, manifest)
            self._save(state)
            return state

    def approve(self, gate, note=""):
        if gate == "backgrounds":
            from .background_library import BackgroundLibrary
            return BackgroundLibrary(self).approve()
        if gate not in ("characters", "backgrounds", "assets"):
            raise ValueError("Unknown asset approval gate")
        self.creative.inspect()
        with project_lock(self.path):
            state, tasks = self._refresh()
            manifest = self.manager.load(self.project_id)
            if gate != "characters":
                self._require_stage("backgrounds", manifest)
            required = {"characters": ("character_references",), "backgrounds": ("character_references", "backgrounds"), "assets": ASSET_STAGES}[gate]
            if any(not self._stage_done(stage, state, tasks) for stage in required):
                raise ValueError("Generate and review all required current assets before approval.")
            if gate == "characters":
                if not any(task.stage == "character_references" for task in tasks):
                    raise ValueError("Create at least one character before approval.")
                missing = []
                for task in tasks:
                    if task.stage != "character_references":
                        continue
                    parts = [part for part, ready in self.character_requirements(task.subject, state, tasks).items() if not ready]
                    if parts:
                        missing.append(task.subject + ": " + ", ".join(parts))
                if missing:
                    raise ValueError("Complete character assets before approval: " + "; ".join(missing) + ".")
            if gate != "characters" and "characters" not in manifest.asset_approvals:
                raise ApprovalRequired("Approve character references first.")
            if gate == "assets" and "backgrounds" not in manifest.asset_approvals:
                raise ApprovalRequired("Approve backgrounds first.")
            manifest.asset_approvals[gate] = ApprovalRecord(content_hash=self._gate_hash(gate, state, tasks), note=note)
            if gate == "characters":
                state.character_approvals = {t.subject: ApprovalRecord(content_hash=self._character_approval_hash(t.subject, state), note=note)
                    for t in tasks if t.stage == "character_references"}
                from .background_library import BackgroundLibrary
                BackgroundLibrary(self).sync(state, tasks, manifest)
            self.manager.save(manifest)
            state.next_stage = self._next(state, tasks, manifest)
            self._save(state)
            return state

    def run(self):
        """Continue up to the next asset approval, keeping expensive work explicit."""
        while True:
            state = self.inspect()
            if self.stop_requested() or state.next_stage not in ASSET_STAGES:
                return state
            state = self.generate(state.next_stage)
            if state.stopped:
                return state

    def set_generic_prompt(self, text):
        if not isinstance(text, str):
            raise ValueError("The generic type prompt must be text.")
        with project_lock(self.path):
            state = self.load()
            state.generic_prompt = text.strip()
            self._save(state)
            try:
                self._source()
            except ApprovalRequired:
                return state
            return self._refresh()[0]

    def has_generic_prompt(self, state):
        return bool(state.generic_prompt.strip() or state.style_reference)

    def shared_style_enabled(self, state):
        """Compatibility for the earlier standalone pipeline only."""
        return not state.per_asset_references

    def _style_references(self, state):
        if not self.shared_style_enabled(state):
            return []
        paths = []
        for reference in state.style_references:
            path = self.local_path(reference["file"])
            if not path.is_file() or file_hash(path) != reference["hash"]:
                raise ValueError("The shared style reference image is missing or modified. Upload it again or remove it before generating.")
            paths.append(path)
        return paths

    def _style_reference(self, state):
        paths = self._style_references(state)
        return paths[0] if paths else None

    def set_style_reference(self, data):
        """Compatibility helper for selecting one image or clearing all images."""
        return self.set_style_references([] if data is None else [data])

    def set_style_references(self, images):
        """Validate the complete selection before saving up to three immutable images."""
        from PIL import Image, UnidentifiedImageError
        import hashlib
        images = list(images)
        if len(images) > 3:
            raise ValueError("Select at most 3 shared style reference images.")
        validated = []
        for data in images:
            if not isinstance(data, bytes) or len(data) > 20 * 1024 * 1024:
                raise ValueError("Upload a PNG, JPEG or WebP image up to 20 MB.")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
                    image.verify()
                if extension is None:
                    raise ValueError("Upload a PNG, JPEG or WebP style reference.")
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("The style reference is not a valid PNG, JPEG or WebP image.") from exc
            digest = hashlib.sha256(data).hexdigest()
            validated.append(({"file": f"references/style/{digest}.{extension}", "hash": digest}, data))
        with project_lock(self.path):
            state = self.load()
            for reference, data in validated:
                write_bytes(self.local_path(reference["file"]), data)
            state.style_references = [reference for reference, _ in validated]
            state.style_reference = state.style_references[0]["file"] if validated else None
            hashes = [reference["hash"] for reference in state.style_references]
            state.style_reference_hash = hashes[0] if len(hashes) == 1 else input_hash(hashes) if hashes else ""
            self._save(state)
            try:
                self._source()
            except ApprovalRequired:
                return state
            return self._refresh()[0]

    def set_style_prompt(self, stage, subject, text):
        if stage not in ASSET_STAGES[:4] or not isinstance(text, str):
            raise ValueError("Style prompts are supported for images and voice tracks.")
        if stage == "backgrounds" and self.independent_backgrounds(stage):
            from .background_library import BackgroundLibrary
            return BackgroundLibrary(self).set_prompt(subject, text)
        if stage == "character_references":
            from .character_visual_inputs import CharacterVisualInputs
            return CharacterVisualInputs(self).update(subject, {"style_prompt": text.strip()})
        with project_lock(self.path):
            state = self.load()
            state.style_prompts[f"{stage}:{subject}"] = text.strip()
            self._save(state)
            try:
                self._source()
            except ApprovalRequired:
                return state
            return self._refresh()[0]

    def character_image(self, state, subject):
        reference = state.character_images[subject]
        path = self.local_path(reference["file"])
        if not path.is_file() or file_hash(path) != reference["hash"]:
            raise ValueError("The character image reference is missing or modified. Upload it again or remove it.")
        return path

    def set_character_image(self, subject, data):
        from PIL import Image, UnidentifiedImageError
        import hashlib
        if subject not in {c.character_id for c in self.creative.artifact("characters").characters}:
            raise ValueError("Unknown character")
        reference = None
        if data is not None:
            if not isinstance(data, bytes) or len(data) > 20 * 1024 * 1024:
                raise ValueError("Upload a PNG, JPEG or WebP image up to 20 MB.")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    extension = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}.get(image.format)
                    image.verify()
                if extension is None:
                    raise ValueError("Upload a PNG, JPEG or WebP image.")
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                raise ValueError("The character reference is not a valid image.") from exc
            digest = hashlib.sha256(data).hexdigest()
            reference = {"file": f"references/characters/{subject}/{digest}.{extension}", "hash": digest}
        from .character_visual_inputs import CharacterVisualInputs
        return CharacterVisualInputs(self).update(subject, {"reference": reference}, image_data=data)

    def independent_backgrounds(self, stage, manifest=None):
        manifest = manifest or self.manager.load(self.project_id)
        return stage == "backgrounds" and manifest.workflow_version == "world_episode" and not manifest.world_project_id

    def can_generate(self, stage, state):
        return self.generation_blocker(stage,state) is None

    def generation_blocker(self,stage,state):
        """Use the same generation gates for eligibility and visible explanations."""
        try:
            if self.shared_style_enabled(state) and stage != "character_references" and not self.independent_backgrounds(stage) and not self.has_generic_prompt(state):
                return "Add the required shared style guidance before generating."
            self._style_reference(state)
            self._source()
            self._require_stage(stage, self.manager.load(self.project_id))
            tasks = self.tasks(state)
            manifest = self.manager.load(self.project_id)
            prior = ASSET_STAGES[2:ASSET_STAGES.index(stage)] if manifest.world_project_id else ASSET_STAGES[:ASSET_STAGES.index(stage)]
            if not self.independent_backgrounds(stage):
                incomplete = [item for item in prior if not self._stage_done(item,state,tasks)]
                if incomplete:
                    return "Complete the earlier asset stages: "+", ".join(incomplete)
            return None
        except (OSError, ValueError, RuntimeError) as exc:
            return str(exc) or "Complete this stage's prerequisites before generating."

    def set_voice(self, logical_voice, provider_voice):
        provider_voice = provider_voice.strip()
        from .animation import load_animation
        direction = load_animation(self.path)
        if direction and direction.enabled and logical_voice != "narrator":
            raise ValueError("Directed voice casting is locked to the animation voice profiles; edit the profile and approve a new preview")
        if not provider_voice.strip():
            raise ValueError("Enter a provider Voice ID")
        if (self.demo or self.settings.tts_provider == "openai") and provider_voice not in ("alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "sage", "ash", "ballad", "marin", "cedar"):
            raise ValueError("Unsupported built-in voice")
        if self.load().voice_map.get(logical_voice) != provider_voice:
            from .voice_validation import validate_provider_voice
            validate_provider_voice(self.manager, self.project_id, provider_voice, speaker=logical_voice, demo=self.demo)
        self.creative.inspect()
        with project_lock(self.path):
            manifest = self.manager.load(self.project_id)
            if logical_voice == "narrator" and any(not manifest.steps.get(step) or manifest.steps[step].status != "succeeded"
                                                   for step in ("world", "characters")):
                # Narrator casting is a setting, so it can be saved before
                # creative artifacts exist without advancing asset generation.
                state = self.load()
                state.voice_map["narrator"] = provider_voice
                state.updated_at = utc_now()
                atomic_write(self.state_path, state.model_dump_json(by_alias=True, indent=2) + "\n")
                return state
            state, _ = self._refresh()
            if logical_voice == "narrator":
                state.voice_map.setdefault("narrator", "")
            if logical_voice not in state.voice_map:
                raise ValueError("Unknown logical voice ID")
            state.voice_map[logical_voice] = provider_voice
            self._save(state)
            return self._refresh()[0]
