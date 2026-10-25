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

from .asset_models import ASSET_STAGES, Alignment, AssetRecord, AssetModification, Cue, Production, SceneAssets
from .costs import CostTracker
from .request_tracking import tracked_call, request_fields
from .files import atomic_write, input_hash
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
        self.tts_provider = tts_provider or (DemoTTSProvider() if self.demo else balanced_providers.tts_provider(self.settings))
        self.alignment_provider = alignment_provider or (DemoAlignmentProvider() if self.demo else OpenAIAlignmentProvider(timeout=self.settings.llm_timeout_sec))
        self.on_progress = on_progress
        self.modification_image_provider = OpenAIImageProvider(timeout=self.settings.llm_timeout_sec)
        self.modification_tts_provider = OpenAITTSProvider(timeout=self.settings.llm_timeout_sec)
        self.stop_requested = stop_requested or (lambda: False)

    def parallel_limit(self, stage):
        return 1 if stage in ("character_references", "backgrounds") else self.max_tasks

    def load(self):
        return Production.model_validate_json(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else Production()

    def local_path(self, relative):
        path = (self.path / relative).resolve()
        if path == self.path or not path.is_relative_to(self.path):
            raise ValueError("Asset file path escapes the project")
        return path

    def _creative_ready(self, manifest):
        return ("story" in manifest.approvals and "storyboard" in manifest.approvals
                and all(manifest.steps.get(step) and manifest.steps[step].status == "succeeded"
                        for step in ("world", "characters", "story", "review", "script", "storyboard", "prompts")))

    def _source(self):
        manifest = self.manager.load(self.project_id)
        if any(not manifest.steps.get(step) or manifest.steps[step].status != "succeeded"
               for step in ("world", "characters")):
            raise ApprovalRequired("Complete the Characters stage before generating character assets.")
        return input_hash({"artifacts": {step: manifest.steps[step].output_hash for step in manifest.steps if step in ("world", "characters", "story", "script", "storyboard", "prompts")},
                           "language": manifest.language, "mode": "demo" if self.demo else "openai",
                           "image": [self.settings.image_model, self.settings.image_size, self.settings.image_quality],
                           "tts": self.settings.tts_model, "align": self.settings.alignment_model,
                           "narrator": self.settings.narrator_voice, "version": "assets-v1"})

    def tasks(self, state):
        from .animation import load_animation
        from .identity import IdentityManager
        identity = IdentityManager(self.manager, self.project_id)
        library = identity.load()
        if library:
            identity.validate(library)
        direction = load_animation(self.path)
        direction = direction if direction and direction.enabled else None
        characters = self.creative.artifact("characters").characters
        tasks = []
        for c in characters:
            prompt = (f"Character reference sheet, neutral background, full-body front and three-quarter views. "
                      f"Name: {c.name}; age: {c.age}; appearance: {c.appearance}; clothing: {c.clothing}; "
                      f"fixed traits: {', '.join(c.immutable_traits)}. Consistent identity across all views. No text.")
            if direction or library:
                prompt += (" Reference pack panels: front face, three-quarter face, side profile, full body, common expressions, "
                           "fixed costume and signature props. Keep every panel's identity, proportions and art style identical.")
            if library:
                prompt += " Registered identity: " + json.dumps({"characterId": c.character_id,
                    "costumes": [item.model_dump(mode="json") for item in library.costumes.values() if item.character_id == c.character_id],
                    "props": [item.model_dump(mode="json") for item in library.props.values() if item.owner_id == c.character_id]}, ensure_ascii=False)
            tasks.append(Task("character_references", c.character_id, prompt))
        if not self._creative_ready(self.manager.load(self.project_id)):
            return tasks
        world = self.creative.artifact("world")
        board = self.creative.artifact("storyboard")
        prompts = {p.scene_id: p for p in self.creative.artifact("prompts").prompts}
        used_locations = {scene.location_id for scene in board.scenes}
        for location in world.locations:
            if location.location_id in used_locations:
                tasks.append(Task("backgrounds", location.location_id,
                                  f"Reusable cinematic environment reference, no people, no text. Era: {world.era}. "
                                  f"Location: {location.name}. {location.description}. World rules: {'; '.join(world.rules)}"
                                  + (" Preserve this location bible: " + library.locations[location.location_id].model_dump_json()
                                     if library else " Preserve this location bible: " + direction.plan.locations[location.location_id].model_dump_json()
                                     if direction and location.location_id in direction.plan.locations else "")))
        voices = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
        state.voice_map.setdefault("narrator", self.settings.narrator_voice or ("alloy" if self.demo else ""))
        for c in characters:
            state.voice_map.setdefault(c.voice_id, voices[int(input_hash(c.voice_id)[:8], 16) % len(voices)] if self.demo or self.settings.tts_provider == "openai" else "")
            if library:
                state.voice_map[c.voice_id] = library.voices[c.character_id].voice_id
            if direction and c.character_id in direction.plan.voices and direction.plan.voices[c.character_id].voice_id:
                state.voice_map[c.voice_id] = direction.plan.voices[c.character_id].voice_id
        cast = {c.character_id: c for c in characters}
        for scene in board.scenes:
            prompt = prompts[scene.scene_id]
            tasks.append(Task("scene_images", scene.scene_id,
                              f"{prompt.image_prompt}\nAvoid: {prompt.negative_prompt}\n"
                              "Use the attached character and environment references. Preserve identity, clothing and location.",
                              scene.scene_id, [f"character_references:{cid}" for cid in scene.character_ids]
                              + [f"backgrounds:{scene.location_id}"]))
            lines = ([("narrator", "narrator", scene.narration)] if scene.narration.strip() else [])
            lines += [(line.character_id, cast[line.character_id].voice_id, line.text) for line in scene.dialogue]
            for line_index, (speaker, voice, text) in enumerate(lines):
                chunks = text_chunks(text)
                for chunk_index, chunk in enumerate(chunks):
                    subject = f"{scene.scene_id}_{line_index:04d}_{chunk_index:04d}"
                    task = Task("voices", subject, chunk, scene.scene_id, voice_id=voice, speaker=speaker,
                                line_index=line_index-int(bool(scene.narration.strip())), chunk_index=chunk_index, chunk_count=len(chunks))
                    tasks.append(task)
                    tasks.append(Task("subtitles", subject, chunk, scene.scene_id, [task.key], voice_id=voice, speaker=speaker))
        return tasks

    def _config(self, task, state):
        if task.stage in ("character_references", "backgrounds", "scene_images"):
            model, options = self.settings.image_model, {"size": self.settings.image_size, "quality": self.settings.image_quality}
        elif task.stage == "voices":
            model, options = self.settings.tts_model, {"voice": state.voice_map[task.voice_id]}
        else:
            model, options = self.settings.alignment_model, {"language": self.manager.load(self.project_id).language}
        style = state.style_prompts.get(task.key, "").strip()
        if task.stage in ASSET_STAGES[:4] and state.generic_prompt.strip():
            options["generic_prompt"] = state.generic_prompt.strip()
        from .identity import IdentityManager
        library = IdentityManager(self.manager, self.project_id).load()
        if library and library.style.strip() and task.stage in ASSET_STAGES[:3]:
            options["generic_prompt"] = (options.get("generic_prompt", "") + "\n" + library.style).strip()
        if task.stage in ASSET_STAGES[:3] and state.style_reference:
            options["style_reference_hash"] = state.style_reference_hash
        if style:
            options["style_prompt"] = style
        modification = state.asset_modifications.get(task.key)
        if modification:
            options["modification"] = modification.model_dump(mode="json")
        if task.stage == "voices":
            from .animation import performance_options
            options.update(performance_options(self.manager, self.project_id, task))
            if modification and modification.delivery:
                options["performance"] = {**options.get("performance", {}), **modification.delivery}
        provider = "openai" if modification else "demo" if self.demo else self.settings.image_provider if task.stage in ASSET_STAGES[:3] else self.settings.tts_provider if task.stage == "voices" else "openai"
        if modification and task.stage == "voices" and not self.demo and self.settings.tts_provider == "elevenlabs":
            provider, model = "elevenlabs", self.settings.tts_model
        elif modification:
            model = "gpt-4o-mini-tts" if task.stage == "voices" else "gpt-image-1"
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

    def record_current(self, record, task, state):
        return bool(record and record.status == "succeeded"
                    and (record.applied_input_hash or record.input_hash) == self._hash(task, state)
                    and self._intact(record))

    def revisions(self, stage, subject):
        from .asset_revisions import revisions
        return revisions(self, stage, subject)

    def apply_revision(self, stage, subject, asset_id):
        from .asset_revisions import apply_revision
        return apply_revision(self, stage, subject, asset_id)

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
        for stage in ASSET_STAGES:
            if stage != "character_references" and not self._creative_ready(manifest):
                return "waiting_for_creative"
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
        self._style_reference(state)
        manifest = self.manager.load(self.project_id)
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
        for key in list(state.records):
            if key not in active_keys and (self._creative_ready(manifest) or state.records[key].stage == "character_references"):
                del state.records[key]
        for task in tasks:
            record = state.records.get(task.key)
            if record and record.status in ("succeeded", "stale"):
                if (record.applied_input_hash or record.input_hash) != self._hash(task, state):
                    record.status, record.stale_reason = "stale", "Generation inputs changed. Generate again or apply a saved revision, then approve."
                elif not self._intact(record):
                    record.status, record.stale_reason = "stale", "The saved asset file is missing or its contents changed."
                else:
                    record.status, record.stale_reason = "succeeded", ""
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
        if stage != "character_references" and not self._creative_ready(manifest):
            raise ApprovalRequired("Complete the creative pipeline and approve the story and storyboard before generating other assets.")
        if stage != "character_references" and "characters" not in manifest.asset_approvals:
            raise ApprovalRequired("Review and approve character references before continuing asset production.")
        if stage not in ("character_references", "backgrounds") and "backgrounds" not in manifest.asset_approvals:
            raise ApprovalRequired("Review and approve backgrounds before generating scene assets.")

    def _cost(self, task, usage, provider=None):
        if provider == "demo" or (provider is None and self.demo):
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
        if task.stage == "voices":
            from .animation import require_voice
            require_voice(self.manager, self.project_id, task.speaker, require_performance=True)
        with self._state_lock:
            record = state.records.setdefault(task.key, AssetRecord(stage=task.stage, subject=task.subject))
            fingerprint = self._hash(task, state)
            if not force and record.status not in ("failed", "running") and (record.applied_input_hash or record.input_hash) == fingerprint and self._intact(record):
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
            def update_job(job_id, status, progress):
                with self._state_lock:
                    record.remote_job_id, record.remote_job_hash, record.remote_status = job_id, fingerprint, status
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
                    alignment = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.align, source_audio, task.prompt, config.options["language"], config)
                    alignment = Alignment.model_validate(alignment.model_dump())
                    if abs(alignment.duration - wav_duration(source_audio)) > 0.05:
                        raise ValueError("Alignment duration does not match its source audio")
                    if not alignment.segments:
                        raise ProviderError("Alignment returned no subtitle segments.")
                    atomic_write(destination, alignment.model_dump_json(by_alias=True, indent=2) + "\n")
                    asset = GeneratedAsset(type="subtitle", provider=config.provider, model=config.model,
                                           file_path=str(destination), duration=alignment.duration, prompt_hash=input_hash(task.prompt))
                elif task.stage == "voices":
                    spoken_text = config.options.get("modification", {}).get("spoken_text") or task.prompt
                    asset = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.generate, spoken_text, config.options["voice"], config)
                    if config.options.get("performance"):
                        from .media_utils import pad_performance
                        pad_performance(Path(asset.file_path), config.options["performance"])
                        asset.duration = wav_duration(Path(asset.file_path))
                    if wav_duration(Path(asset.file_path)) <= 0:
                        raise ValueError("Empty speech track")
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
                    if isinstance(provider, RunwayImageProvider):
                        from .provider_prompts import compile_prompt
                        prompt = compile_prompt(self.creative, prompt)
                    asset = tracked_call(self.manager, self.project_id, f"assets:{task.stage}", provider.generate, prompt, references, config)
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
                    asset.generation_params = {**asset.generation_params, "prompt": prompt if suffix == ".png" else spoken_text if suffix == ".wav" else task.prompt,
                                               "genericPrompt": state.generic_prompt,
                                               "styleReference": state.style_reference if suffix == ".png" else None,
                                               "styleReferenceHash": state.style_reference_hash if suffix == ".png" else "",
                                               "styleReferences": state.style_references if suffix == ".png" else [],
                                               "stylePrompt": config.options.get("style_prompt", ""), "references": task.refs,
                                               "referenceHashes": {r: state.records[r].file_hash for r in task.refs},
                                               "logicalVoiceId": task.voice_id, "speaker": task.speaker,
                                               "config": {key: value for key, value in config.options.items() if key not in ("output_path", "job_update", "job_id")},
                                               "demo": config.provider == "demo"}
                    record.asset = GeneratedAsset.model_validate(asset.model_dump(exclude={"voice_id"}))
                    record.file_hash = file_hash(path)
                    record.input_hash = fingerprint
                    record.applied_input_hash = ""
                    if task.stage in ("character_references", "backgrounds"):
                        record.revision = max([record.revision, *(r.revision for r in self.revisions(task.stage, task.subject))]) + 1
                    else:
                        record.revision += 1
                    record.status = "succeeded"
                    record.error = None
                    record.updated_at = utc_now()
                    atomic_write(self.path / "history" / "assets" / task.stage / task.subject / f"{asset.asset_id}.json",
                                 record.model_dump_json(by_alias=True, indent=2) + "\n")
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
        if state.style_reference:
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
                                    "styleReferences": state.style_references,
                                    "styleReference": state.style_reference,
                                    "styleReferenceHash": state.style_reference_hash,
                                    "styleReview": "Shared reference provenance is checked. Human review must confirm visual style, character identity and location consistency.",
                                    "issues": issues, "signature": self._signature(state),
                                    "visualReviewRequired": True, "demo": self.demo,
                                    "checks": ["asset integrity", "reference hashes", "shared style provenance", "fixed voice IDs", "audio-derived timestamps", "transcript similarity"]}
        atomic_write(self.path / "asset_review.json", json.dumps(state.consistency_report, ensure_ascii=False, indent=2))
        atomic_write(self.path / "asset_review.md", "# Asset review\n\nVisual identity and clothing require human review.\n\n"
                     + "Shared style reference provenance is checked; matching the visual style and locations still requires human review.\n\n"
                     + "\n".join(f"- {i['severity']}: {i.get('sceneId', '')} {i['message']}" for i in issues) + "\n")

    def generate(self, stage, *, subject=None, subjects=None, force=False, use_saved_modification=False):
        if stage not in ASSET_STAGES:
            raise ValueError(f"Unknown asset stage: {stage}")
        self.creative.inspect()
        with project_lock(self.path):
            state, tasks = self._refresh()
            if not self.has_generic_prompt(state):
                raise ValueError("Add a generic type prompt using text, a reference image, or both before generating assets.")
            state.stopped = False
            manifest = self.manager.load(self.project_id)
            self._require_stage(stage, manifest)
            index = ASSET_STAGES.index(stage)
            if any(not self._stage_done(prior, state, tasks) for prior in ASSET_STAGES[:index]):
                raise ValueError("Complete earlier asset stages before continuing.")
            if subjects is not None and (not subjects or subject is not None):
                raise ValueError("Select one or more subjects, without a single subject filter")
            selected = [t for t in tasks if t.stage == stage and (subject is None or t.subject == subject)
                        and (subjects is None or t.subject in subjects)]
            if subjects is not None and set(subjects) != {t.subject for t in selected}:
                raise ValueError("Unknown selected asset subject")
            if subject is not None and not selected:
                raise ValueError("No matching asset subject")
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
        if task.key in state.asset_modifications:
            if task.stage == "voices" and not self.demo and self.settings.tts_provider == "elevenlabs":
                return self.tts_provider
            return self.modification_tts_provider if task.stage == "voices" else self.modification_image_provider
        return self.image_provider if task.stage in ASSET_STAGES[:3] else self.tts_provider if task.stage == "voices" else self.alignment_provider

    def modify_with_openai(self, stage, subject, instructions, *, expected_asset_id=None):
        if stage not in ASSET_STAGES[:4] or not instructions.strip():
            raise ValueError("Choose an image or voice asset and enter a modification prompt.")
        self.creative.inspect()
        with project_lock(self.path):
            state, tasks = self._refresh()
            self._require_stage(stage, self.manager.load(self.project_id))
            if any(not self._stage_done(prior, state, tasks) for prior in ASSET_STAGES[:ASSET_STAGES.index(stage)]):
                raise ValueError("Complete earlier asset stages before modifying this asset.")
            record = state.records.get(f"{stage}:{subject}")
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
                self._save(state)
            try:
                self._run_task(task, state, force=force)
            finally:
                with self._state_lock:
                    state.generation_active -= 1
                    state.generation_finished += 1
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
        self._batch_open = True
        failures = []
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="asset") as pool:
            pending = {}

            def fill():
                with self._state_lock:
                    while len(pending) < limit and self._batch_queue and not self.stop_requested():
                        task, forced = self._batch_queue.popleft()
                        pending[pool.submit(self._parallel_task, task, state, forced)] = task

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
            self._scheduled.add(key)
            self._batch_queue.append((self._batch_tasks[key], force))
            self._batch_state.generation_total += 1
            self._batch_state.generation_queued += 1
            self._save(self._batch_state)
            return True

    def approve(self, gate, note=""):
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
            if gate != "characters" and "characters" not in manifest.asset_approvals:
                raise ApprovalRequired("Approve character references first.")
            if gate == "assets" and "backgrounds" not in manifest.asset_approvals:
                raise ApprovalRequired("Approve backgrounds first.")
            manifest.asset_approvals[gate] = ApprovalRecord(content_hash=self._gate_hash(gate, state, tasks), note=note)
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

    def _style_references(self, state):
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
        with project_lock(self.path):
            state = self.load()
            state.style_prompts[f"{stage}:{subject}"] = text.strip()
            self._save(state)
            try:
                self._source()
            except ApprovalRequired:
                return state
            return self._refresh()[0]

    def can_generate(self, stage, state):
        try:
            if not self.has_generic_prompt(state):
                return False
            self._style_reference(state)
            self._source()
            self._require_stage(stage, self.manager.load(self.project_id))
            tasks = self.tasks(state)
            return all(self._stage_done(prior, state, tasks) for prior in ASSET_STAGES[:ASSET_STAGES.index(stage)])
        except (OSError, ValueError, RuntimeError):
            return False

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
