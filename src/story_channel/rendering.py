"""Resumable scene rendering, licensed sound planning, final assembly and approval."""
import json
from pathlib import Path
from decimal import Decimal
from uuid import uuid4

from .assets import AssetPipeline
from .costs import CostTracker
from .request_tracking import tracked_call, request_fields
from .ffmpeg_renderer import FFmpeg, combined_subtitles, synthesized_sound
from .files import atomic_copy, atomic_write, input_hash
from .locking import project_lock
from .media_utils import file_hash
from .models import ApprovalRecord, ProviderConfig, ProjectStatus, UsageRecord
from .pipeline import ApprovalRequired
from .render_models import RenderState, RenderScene, RenderOptions, MusicPlan, SceneSound, SoundCue
from .video_providers import LocalVideoProvider, OpenAIVideoProvider
from .llm import TransientProviderError
from .balanced_providers import video_provider as production_video_provider


class RenderPipeline:
    def __init__(self, manager, project_id, *, video_provider=None, ffmpeg=None, stop_requested=None,
                 lip_provider=None, sfx_provider=None, vfx_provider=None):
        self.manager, self.project_id = manager, project_id
        self.assets = AssetPipeline(manager, project_id)
        self.path = self.assets.path
        self.state_path = self.path / "render.json"
        self.ffmpeg = ffmpeg or FFmpeg(manager.settings.ffmpeg_path, manager.settings.render_timeout_sec)
        self.video_provider = video_provider
        self.stop_requested = stop_requested or (lambda: False)
        from .finishing import Finishing
        self.finishing = Finishing(self, lip_provider=lip_provider, sfx_provider=sfx_provider, vfx_provider=vfx_provider)

    def load(self):
        from .file_cache import read_model
        return read_model(self.state_path, RenderState) if self.state_path.exists() else RenderState() if self.assets.demo else RenderState(options=RenderOptions(provider="runway", video_model="klingO3_pro", continuity_animation=True))

    def _save(self, state):
        atomic_write(self.state_path, state.model_dump_json(by_alias=True, indent=2) + "\n")
        manifest = self.manager.load(self.project_id)
        ready = state.status in ("review", "approved") and self._final_intact(state)
        manifest.render_state = state.status
        manifest.final_approval = state.approval if ready else None
        if ready:
            manifest.status = ProjectStatus.RENDERED
        elif manifest.status == ProjectStatus.RENDERED:
            manifest.status = ProjectStatus.ASSETS_READY if manifest.asset_state == "approved" else ProjectStatus.STORYBOARD_GENERATED
        self.manager.save(manifest)

    def _input(self):
        from .storyboard_asset_check import require
        require(self.manager,self.project_id)
        production = self.assets.inspect()
        manifest = self.manager.load(self.project_id)
        if production.next_stage != "complete" or "assets" not in manifest.asset_approvals:
            raise ApprovalRequired("Generate and approve this episode's scene inputs before rendering video.")
        board = sorted(self.assets.creative.artifact("storyboard").scenes, key=lambda scene: scene.order)
        if not board or set(production.scenes) != {s.scene_id for s in board}:
            raise ValueError("Every storyboard scene needs current image, audio and subtitle exports.")
        return production, board

    def _refresh(self, production, board):
        # Revalidate under the same lock used for rendering, closing the gap
        # between the initial inspection and another session's edits.
        self.assets.creative._refresh()
        current, _ = self.assets._refresh()
        if current.next_stage != "complete" or "assets" not in self.manager.load(self.project_id).asset_approvals:
            raise ApprovalRequired("Generate and approve this episode's current scene inputs before rendering video.")
        if current.model_dump(exclude={"updated_at"}) != production.model_dump(exclude={"updated_at"}):
            raise ValueError("Assets changed before rendering started. Refresh and retry.")
        state = self.load()
        active = {scene.scene_id for scene in board}
        state.scenes = {key: record for key, record in state.scenes.items() if key in active}
        source = input_hash({"assets": {key: scene.hashes for key, scene in production.scenes.items()},
                             "board": [s.model_dump(mode="json") for s in board]})
        source_changed = state.source_hash != source
        state.source_hash = source
        state.music_plan.scenes = {sid: sound for sid, sound in state.music_plan.scenes.items() if sid in active}
        from .animation import load_animation
        direction = load_animation(self.path)
        for scene in board:
            if not state.music_plan_custom:
                state.music_plan.scenes.pop(scene.scene_id, None)
            cues, ambience = list(scene.sfx), []
            if direction and direction.enabled:
                for shot in direction.plan.shots:
                    if shot.scene_id == scene.scene_id:
                        cues.extend(shot.sfx_cues)
                        ambience.extend(shot.ambience_cues)
                cues.extend(line.sfx_cue for line in direction.plan.performances if line.scene_id == scene.scene_id and line.sfx_cue.strip())
            cues, ambience = list(dict.fromkeys(cues)), list(dict.fromkeys(ambience))
            from .bgm_library import scene_music
            reusable_music = scene_music(self.manager,self.project_id,scene)
            state.music_plan.scenes.setdefault(scene.scene_id, SceneSound(bgm_mood=scene.bgm_mood or "ambient", cues=[
                SoundCue(name=name, at=production.scenes[scene.scene_id].duration*(i+1)/(len(cues)+1),
                    duration=min(2., production.scenes[scene.scene_id].duration/(len(cues)+1)))
                for i, name in enumerate(cues)], ambience=[SoundCue(name=name, at=0,
                    duration=production.scenes[scene.scene_id].duration, loop=True) for name in ambience],
                bgm_file=reusable_music["file"] if reusable_music else None,
                bgm_source="Storyboard BGM: "+reusable_music["source"] if reusable_music else "Procedurally synthesized locally; no third-party recording"))
            sound = state.music_plan.scenes[scene.scene_id]
            if reusable_music and (not sound.bgm_file or sound.bgm_source.startswith("Storyboard BGM:")):
                sound.bgm_file,sound.bgm_source = reusable_music["file"],"Storyboard BGM: "+reusable_music["source"]
            from .storyboard_asset_check import sounds,sound_id,intact
            sound_assets = sounds(self.manager,self.project_id)
            for cue in state.music_plan.scenes[scene.scene_id].cues:
                item = sound_assets.get(sound_id(cue.name),{})
                if intact(self.path,item) and (not cue.file or cue.source.startswith("Storyboard SFX:")):
                    cue.file,cue.source = item["file"],"Storyboard SFX: "+item["source"]
            record = state.scenes.setdefault(scene.scene_id, RenderScene(scene_id=scene.scene_id))
            if record.status == "running":
                record.status, record.error = "failed", "Rendering was interrupted. Retry to resume the saved video job or clip."
                state.status = "failed"
            if record.status == "succeeded" and (record.input_hash != self._scene_key(scene, production, state) or not self._scene_intact(record)):
                record.status = "stale"
                record.approved_hash = ""
        if state.final_input_hash != self._final_key(state, board) or not self._final_intact(state) or any(r.status != "succeeded" for r in state.scenes.values()):
            state.approval = None
            if state.final_file:
                state.status = "stale"
        self._save(state)
        return state

    def inspect(self):
        production, board = self._input()
        with project_lock(self.path):
            return self._refresh(production, board)

    def _file_digest(self, relative):
        return file_hash(self.assets.local_path(relative))

    def _scene_intact(self, record):
        try:
            return bool(record.file and self._file_digest(record.file) == record.file_hash)
        except (OSError, ValueError):
            return False

    def _final_intact(self, state):
        try:
            return bool(state.final_file and self._file_digest(state.final_file) == state.final_hash
                        and state.final_exports and all(self._file_digest(path) == digest for path, digest in state.final_exports.items()))
        except (OSError, ValueError):
            return False

    def _sound_key(self, sound, *, strict=False):
        def digest(path):
            try:
                return self._file_digest(path)
            except (OSError, ValueError):
                if strict:
                    raise ValueError("A sound file is missing or outside the project; upload it and update the plan")
                return None
        if strict:
            sources = [(sound.bgm_file, sound.bgm_source), *((cue.file, cue.source) for cue in [*sound.cues, *sound.ambience])]
            if any(path and (not source.strip() or source.startswith("Procedurally synthesized locally")) for path, source in sources):
                raise ValueError("Record the actual license/source for every custom audio file")
        plan = sound.model_dump(mode="json")
        for cue in [*plan["cues"], *plan["ambience"]]:
            if not cue.get("prompt"):
                cue.pop("prompt", None)
        return {"plan": plan, "bgm": digest(sound.bgm_file) if sound.bgm_file else None,
                "sfx": [(cue.file, digest(cue.file)) for cue in [*sound.cues, *sound.ambience] if cue.file]}

    def _scene_key(self, scene, production, state):
        record = state.scenes[scene.scene_id]
        animation = None
        if state.options.continuity_animation:
            from .animation import AnimationDirector
            from .animation_rendering import ShotRenderer
            director = AnimationDirector(self.manager, self.project_id)
            if director.load():
                previous = ShotRenderer(director, self.ffmpeg, None).previous_frame(scene, self.assets.creative.artifact("storyboard").scenes)
                animation = {"direction": director.direction_hash(director.load()), "previousFrame": file_hash(previous) if previous else None}
        finishing = self.finishing.signature(scene.scene_id)
        visual = None
        if self.manager.load(self.project_id).world_project_id:
            from .scene_production import SceneProduction
            source = SceneProduction(self.manager, self.project_id).visuals().get(scene.scene_id, {})
            visual = {name: source.get(name) for name in ("sourceHash", "fileHash", "approval")}
        return input_hash({"scene": scene.model_dump(mode="json"), "assets": production.scenes[scene.scene_id].model_dump(mode="json"),
                           "options": state.options.model_dump(mode="json", exclude={"continuity_animation"} if not state.options.continuity_animation else set()), "sound": self._sound_key(state.music_plan.scenes[scene.scene_id]),
                           "modification": [record.modification_prompt, record.modification_source],
                             **({"animation": animation} if state.options.continuity_animation else {}),
                             **({"finishing": finishing} if finishing is not None else {}),
                             **({"approvedVisual": visual} if visual is not None else {}), "v": 1})

    def _final_key(self, state, board):
        return input_hash({"scenes": [(s.scene_id, state.scenes[s.scene_id].input_hash, state.scenes[s.scene_id].file_hash) for s in board],
                           "source": state.source_hash, "subtitles": state.options.subtitles})

    def configure(self, options=None, music_plan=None):
        if self.manager.load(self.project_id).world_project_id and options is not None and music_plan is None:
            # Visual generation precedes recorded voices in the episode flow.
            with project_lock(self.path):
                state = self.load()
                updated = RenderOptions.model_validate(options)
                if updated != state.options:
                    state.options = updated
                    state.approval = None
                    if state.scenes:
                        state.status = "stale"
                    for scene in state.scenes.values():
                        scene.status, scene.approved_hash = "stale", ""
                self._save(state)
                return state
        production, board = self._input()
        with project_lock(self.path):
            state = self._refresh(production, board)
            if options is not None:
                state.options = RenderOptions.model_validate(options)
            if music_plan is not None:
                plan = MusicPlan.model_validate(music_plan)
                if set(plan.scenes) != {s.scene_id for s in board}:
                    raise ValueError("The music plan must contain exactly the current scene IDs")
                for sid, sound in plan.scenes.items():
                    duration = production.scenes[sid].duration
                    if any(cue.at >= duration or cue.at + cue.duration > duration + .001 for cue in [*sound.cues, *sound.ambience]):
                        raise ValueError("Sound effects must fit within their scene's actual audio duration")
                    self._sound_key(sound, strict=True)
                state.music_plan = plan
                state.music_plan_custom = True
            self._save(state)
            atomic_write(self.path / "music_plan.json", state.music_plan.model_dump_json(by_alias=True, indent=2))
            return self._refresh(production, board)

    def import_audio(self, data, filename, source):
        if not source.strip():
            raise ValueError("Record the license/source of an uploaded BGM or sound effect.")
        suffix = Path(filename).suffix.lower()
        if suffix not in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
            raise ValueError("Upload WAV, MP3, M4A, OGG or FLAC audio")
        import hashlib
        relative = f"audio/library/{hashlib.sha256(data).hexdigest()}{suffix}"
        from .media_utils import write_bytes
        with project_lock(self.path):
            audio = self.assets.local_path(relative)
            write_bytes(audio, data)
            self.ffmpeg.run(["-i", audio, "-map", "0:a:0", "-vn", "-f", "null", "-"])
            atomic_write(self.assets.local_path(relative + ".source.json"), json.dumps({"file": relative, "source": source}, ensure_ascii=False))
        return relative

    def _clip(self, scene, production, state, work, force):
        record = state.scenes[scene.scene_id]
        options = state.options
        if self.manager.load(self.project_id).world_project_id:
            from .scene_production import SceneProduction
            clip = SceneProduction(self.manager, self.project_id).fitted_visual(scene.scene_id, options,
                production.scenes[scene.scene_id].duration, work / "approved_visual.mp4")
            record.clip, record.clip_hash = clip, file_hash(self.assets.local_path(clip.file_path))
            self._save(state)
            return self.assets.local_path(clip.file_path)
        if options.continuity_animation:
            from .animation import AnimationDirector
            from .animation_rendering import ShotRenderer
            director = AnimationDirector(self.manager, self.project_id)
            renderer = ShotRenderer(director, self.ffmpeg, self.video_provider or production_video_provider(options, self.manager.settings), self.stop_requested)
            clip = renderer.render(scene, production, options, work / "animated_scene.mp4", force=force)
            path = Path(clip.file_path)
            clip.file_path = path.relative_to(self.path).as_posix()
            record.clip, record.clip_hash = clip, file_hash(path)
            record.attempts = sum(r.attempts for r in director.load().shots.values() if r.scene_id == scene.scene_id)
            self._save(state)
            return path
        image = production.scenes[scene.scene_id].image
        prompt = next(p.video_prompt for p in self.assets.creative.artifact("prompts").prompts if p.scene_id == scene.scene_id)
        config_data = {"provider": options.provider, "mode": options.generation_mode, "model": options.video_model,
                       "seconds": options.video_seconds, "size": options.video_size,
                       "image": production.scenes[scene.scene_id].hashes[image], "prompt": prompt}
        if record.modification_prompt and options.provider == "openai":
            prompt = record.modification_prompt
            config_data.update(edit_source=record.modification_source, edit_prompt=prompt)
        if options.provider == "local":
            config_data.update(duration=production.scenes[scene.scene_id].duration, width=options.width, height=options.height, fps=options.fps, motion=options.motion)
        key = input_hash(config_data)
        if not force and record.clip and record.clip.generation_params.get("inputHash") == key:
            try:
                if self._file_digest(record.clip.file_path) == record.clip_hash:
                    return self.assets.local_path(record.clip.file_path)
            except (OSError, ValueError):
                pass
        provider = self.video_provider or (LocalVideoProvider(self.ffmpeg) if options.provider == "local" else production_video_provider(options, self.manager.settings))
        if force:
            record.remote_job_id = None
        for attempt in range(self.manager.settings.llm_max_retries + 1):
            record.attempts += 1
            outcome = "failed"

            def update(job_id, status, progress):
                record.remote_job_id, record.remote_job_key = job_id, key
                record.remote_status, record.remote_progress = status, progress
                self._save(state)

            reuse_job = record.remote_job_id if record.remote_job_key == key and record.remote_status not in ("failed", "cancelled") else None
            config = ProviderConfig(provider=options.provider, model=options.video_model if options.provider != "local" else "ffmpeg-image-motion", options={
                "output_path": str(work / "visual.mp4"), "duration": production.scenes[scene.scene_id].duration,
                "render_options": options.model_dump(mode="json"), "seconds": options.video_seconds, "size": options.video_size,
                "edit_source": record.modification_source if options.provider == "openai" else None,
                "job_id": reuse_job, "job_update": update, "poll_timeout": self.manager.settings.render_timeout_sec})
            try:
                refs = [self.assets.local_path(image)] if options.generation_mode == "image" or options.provider == "local" else []
                provider_prompt = prompt
                from .balanced_providers import KlingVideoProvider
                if isinstance(provider, KlingVideoProvider):
                    from .provider_prompts import compile_prompt
                    provider_prompt = compile_prompt(self.assets.creative, prompt, video=True, task_key=f"render:video:{scene.scene_id}:prompt")
                clip = tracked_call(self.manager, self.project_id, "render:video", provider.generate_clip, provider_prompt, refs, config, task_key=f"render:video:{scene.scene_id}")
                path = Path(clip.file_path).resolve()
                if not path.is_relative_to(self.path):
                    raise ValueError("Video provider returned a path outside the project")
                self.ffmpeg.validate(path)
                clip.file_path = path.relative_to(self.path).as_posix()
                clip.source_scene_id = scene.scene_id
                clip.generation_params.update(inputHash=key, prompt=prompt, reference=image if refs else None)
                usage = getattr(provider, "last_usage", {}) or {}
                rate = self.manager.settings.runway_video_cost_per_second if options.provider == "runway" else self.manager.settings.video_cost_per_second
                if options.provider != "local" and rate is not None:
                    clip.cost = rate * Decimal(str(usage.get("video_seconds", 0)))
                record.clip, record.clip_hash = clip, file_hash(path)
                outcome = "succeeded"
                self._save(state)
                return path
            except TransientProviderError:
                if attempt >= self.manager.settings.llm_max_retries:
                    raise
                import time
                time.sleep(min(2 ** attempt, 10))
            finally:
                usage = dict(getattr(provider, "last_usage", {}) or {})
                seconds = usage.get("video_seconds", 0)
                rate = self.manager.settings.runway_video_cost_per_second if options.provider == "runway" else self.manager.settings.video_cost_per_second
                known = options.provider == "local" or rate is not None and "video_seconds" in usage and (seconds > 0 or bool(reuse_job))
                cost = rate * Decimal(str(seconds)) if options.provider != "local" and rate is not None else Decimal(0)
                CostTracker(self.manager, self.project_id).record(UsageRecord(provider=options.provider, model=config.model,
                    provider_job_id=usage.get("job_id"),
                    step="render:video", scene_id=scene.scene_id, video_seconds=seconds, retry_count=attempt, **request_fields(usage),
                    cost=cost, estimated_cost=cost, pricing_known=known, outcome=outcome))

    def _render_scene(self, scene, production, state, *, force=False):
        record = state.scenes[scene.scene_id]
        key = self._scene_key(scene, production, state)
        if not force and record.status == "succeeded" and record.input_hash == key and self._scene_intact(record):
            return
        record.status, record.error = "running", None
        record.approved_hash = ""
        state.status, state.approval = "rendering", None
        self._save(state)
        work = self.path / "videos" / "renders" / scene.scene_id / uuid4().hex
        work.mkdir(parents=True)
        try:
            audio_scene = production.scenes[scene.scene_id]
            sound = state.music_plan.scenes[scene.scene_id]
            if any(cue.at + cue.duration > audio_scene.duration + .001 for cue in [*sound.cues, *sound.ambience]):
                raise ValueError("Update the sound plan: sound effects must fit the current scene duration")
            self._sound_key(sound, strict=True)
            self.finishing.preflight(scene, production, sound)
            clip = self._clip(scene, production, state, work, force)
            clip = self.finishing.apply_lip_sync(scene, production, clip, state.options, work, force=force)
            music, effects = None, []
            if state.options.music:
                if sound.bgm_file:
                    music = self.assets.local_path(sound.bgm_file)
                else:
                    music = work / "music.wav"
                    synthesized_sound(music, sound.bgm_mood, audio_scene.duration, music=True)
            if state.options.sfx:
                grouped = [(group, index, cue) for group in ("cues", "ambience") for index, cue in enumerate(getattr(sound, group))]
                for flat_index, (group, index, cue) in enumerate(grouped):
                    if self.stop_requested():
                        raise InterruptedError("Stopped after the current finishing task")
                    if cue.file:
                        file = self.assets.local_path(cue.file)
                    elif self.finishing.load().plan.sound_provider == "elevenlabs":
                        file = self.finishing.sound(scene.scene_id, group, index, cue, force=force)
                    else:
                        file = work / f"effect_{flat_index}.wav"
                        synthesized_sound(file, cue.name, cue.duration)
                    effects.append((file, cue))
            destination = work / "scene.mp4"
            overlays = []
            for layer in self.finishing.load().plan.effects:
                if layer.scene_id == scene.scene_id and layer.enabled:
                    if self.stop_requested():
                        raise InterruptedError("Stopped after the current finishing task")
                    overlays.append((self.finishing.effect(layer, state.options, force=force), layer))
            self.ffmpeg.compose_scene(clip, self.assets.local_path(audio_scene.audio), self.assets.local_path(audio_scene.srt),
                                      destination, audio_scene.duration, state.options, music, effects, overlays)
            record.file, record.file_hash = destination.relative_to(self.path).as_posix(), file_hash(destination)
            record.duration, record.input_hash = audio_scene.duration, self._scene_key(scene, production, state)
            record.revision += 1
            record.status = "succeeded"
            atomic_write(self.path / "history" / "renders" / scene.scene_id / f"{uuid4().hex}.json", record.model_dump_json(by_alias=True, indent=2))
        except Exception as exc:
            record.status, record.error = "failed", str(exc)
            state.status, state.error = "failed", str(exc)
            raise
        finally:
            self._save(state)

    def generate_scenes(self, **kwargs):
        """Generate scene media without assembling or publishing a final video."""
        return self.run(assemble=False, **kwargs)

    def approve_scenes(self, subjects):
        production, board = self._input()
        with project_lock(self.path):
            state = self._refresh(production, board)
            subjects = list(subjects)
            if not subjects or any(sid not in state.scenes for sid in subjects):
                raise ValueError("Choose existing scenes to approve")
            for sid in subjects:
                record = state.scenes[sid]
                if (record.status != "succeeded" or not record.file
                        or file_hash(self.assets.local_path(record.file)) != record.file_hash):
                    raise ApprovalRequired("Generate and review current scene files before approval")
            for sid in subjects:
                state.scenes[sid].approved_hash = state.scenes[sid].file_hash
            self._save(state)
            return state

    def assembly_progress(self, percent, stage, *, status="running", error=None):
        atomic_write(self.path / "assembly_progress.json", json.dumps(
            {"percent": percent, "stage": stage, "status": status, "error": error}))

    def assemble_final(self):
        """Assemble existing approved scenes only; never invoke a generation provider."""
        self.assembly_progress(0, "Checking approved scenes")
        try:
            if self.manager.load(self.project_id).world_project_id:
                from .scene_continuity_review import current
                if not current(self):
                    raise ApprovalRequired("Review current animated scenes with OpenAI on Animations > Scene Review & Continuity first")
            production, board = self._input()
            with project_lock(self.path):
                state = self._refresh(production, board)
                for scene in board:
                    record = state.scenes[scene.scene_id]
                    if (record.status != "succeeded" or not record.file or not record.approved_hash
                            or record.approved_hash != record.file_hash
                            or file_hash(self.assets.local_path(record.file)) != record.file_hash):
                        raise ApprovalRequired("Compose, review and approve every current scene on Final > Timeline & Compositing first")
                if state.options.continuity_animation and not self.manager.load(self.project_id).world_project_id:
                    from .animation import AnimationDirector
                    from .animation_rendering import continuity_signature
                    director = AnimationDirector(self.manager, self.project_id)
                    approval = director.load().continuity_approval
                    if not approval or approval.content_hash != continuity_signature(director):
                        raise ApprovalRequired("Review scene continuity on Final first")
                self._assemble(production, board, state)
                return state
        except Exception as exc:
            progress = json.loads((self.path / "assembly_progress.json").read_text(encoding="utf-8"))
            self.assembly_progress(progress["percent"], "Assembly failed", status="failed", error=str(exc))
            raise

    def run(self, *, subject=None, force=False, modification=None, expected=None, assemble=True):
        production, board = self._input()
        episode=bool(self.manager.load(self.project_id).world_project_id)
        with project_lock(self.path):
            state = self._refresh(production, board)
            state.error = None
            if state.options.continuity_animation and not episode:
                from .animation import AnimationDirector
                if self.video_provider is None and self.assets.demo:
                    raise ApprovalRequired("Production animation requires real approved references and voice previews; regenerate and approve the asset batch with OpenAI instead of Demo")
                direction_state = AnimationDirector(self.manager, self.project_id).require_ready()
            if subject is not None and subject not in state.scenes:
                raise ValueError("Unknown render scene ID")
            if modification is not None:
                if not subject or not modification.strip():
                    raise ValueError("Choose a scene and describe the requested modification")
                record = state.scenes[subject]
                if not record.clip or record.clip.provider != "openai" or state.options.provider != "openai":
                    raise ValueError("Generate an OpenAI video before modifying it")
                if expected != record.clip.asset_id:
                    raise ValueError("This video changed. Reopen the modification dialog.")
                source = record.clip.generation_params.get("jobId")
                if not source or record.clip.model != state.options.video_model:
                    raise ValueError("Select this clip's original OpenAI model before modifying it")
                record.modification_prompt, record.modification_source = modification.strip(), source
                state.approval = None
                self._save(state)
            failures = []
            for scene in board:
                if subject and scene.scene_id != subject:
                    continue
                if force:
                    state.scenes[scene.scene_id].approved_hash = ""
                if state.options.continuity_animation and not episode:
                    position = next(i for i, item in enumerate(board) if item.scene_id == scene.scene_id)
                    first_shot = next(shot for shot in direction_state.plan.shots if shot.scene_id == scene.scene_id)
                    previous_scene = board[position-1] if position else None
                    if (previous_scene and first_shot.chain_previous and previous_scene.location_id == scene.location_id
                            and set(previous_scene.character_ids) == set(scene.character_ids)
                            and state.scenes[previous_scene.scene_id].status != "succeeded"):
                        if subject:
                            raise ApprovalRequired(f"Render the preceding chained scene {previous_scene.scene_id} first")
                        continue
                if self.stop_requested():
                    state.status = "stopped"
                    self._save(state)
                    return state
                try:
                    self._render_scene(scene, production, state, force=force)
                except InterruptedError:
                    state.scenes[scene.scene_id].status = "stale"
                    state.status, state.error = "stopped", None
                    self._save(state)
                    return state
                except (OSError, ValueError, RuntimeError) as exc:
                    failures.append(str(exc))
            if self.stop_requested():
                state.status = "stopped"
                self._save(state)
                return state
            for scene in board:
                record = state.scenes[scene.scene_id]
                if record.status == "succeeded" and record.input_hash != self._scene_key(scene, production, state):
                    record.status = "stale"
            if failures:
                raise RuntimeError(f"{len(failures)} scene(s) failed. Completed scenes were retained. " + failures[0])
            if self.stop_requested():
                state.status = "stopped"
                self._save(state)
                return state
            if all(r.status == "succeeded" for r in state.scenes.values()):
                if not assemble:
                    state.status = "continuity_review"
                    self._save(state)
                    return state
                if self.manager.load(self.project_id).world_project_id:
                    from .scene_continuity_review import current
                    if not current(self):
                        state.status='continuity_review'
                        self._save(state)
                        return state
                elif state.options.continuity_animation:
                    from .animation_rendering import continuity_signature
                    director = AnimationDirector(self.manager, self.project_id)
                    approval = director.load().continuity_approval
                    if not approval or approval.content_hash != continuity_signature(director):
                        state.status = "continuity_review"
                        self._save(state)
                        return state
                self._assemble(production, board, state)
            else:
                state.status = "stopped"
                self._save(state)
            return state

    def modify_with_openai(self, subject, prompt, *, expected):
        return self.generate_scenes(subject=subject, force=True, modification=prompt, expected=expected)

    def retry_animation_shot(self, shot_id, *, modification=None, expected=None):
        from .animation import AnimationDirector
        director = AnimationDirector(self.manager, self.project_id)
        with project_lock(self.path):
            state = director.load(required=True)
            record = state.shots.get(shot_id)
            if record is None:
                raise ValueError("Unknown animated shot ID")
            if record.status == "succeeded" or modification is not None:
                record.remote_job_id, record.remote_job_key = None, ""
            if modification is not None:
                if not modification.strip() or not record.asset or record.asset.asset_id != expected:
                    raise ValueError("The shot changed or the prompt is empty; reopen the modification dialog")
                source = record.asset.generation_params.get("jobId")
                if not source:
                    raise ValueError("This shot has no editable OpenAI video ID")
                record.modification_source, record.modification_prompt = source, modification.strip()
            record.status = "failed"
            state.continuity_approval = None
            director.save_state(state)
            render = self.load()
            render.scenes[record.scene_id].status = "stale"
            render.approval = None
            render.status = "stale"
            self._save(render)
        return self.generate_scenes(subject=record.scene_id)

    def _assemble(self, production, board, state):
        state.error = None
        self.assembly_progress(5, "Preparing timeline and subtitles")
        last_percent = 5
        key = self._final_key(state, board)
        if state.final_input_hash == key and self._final_intact(state):
            state.status = "approved" if state.approval else "review"
            self._save(state)
            self.assembly_progress(100, "Final video ready", status="completed")
            return
        work = self.path / "output" / "versions" / uuid4().hex
        work.mkdir(parents=True)
        try:
            srt, vtt = combined_subtitles([production.scenes[s.scene_id] for s in board], self.assets)
            atomic_write(work / "final.srt", srt)
            atomic_write(work / "final.vtt", vtt)
            timeline, cursor = [], 0.0
            for scene in board:
                record = state.scenes[scene.scene_id]
                timeline.append({"sceneId": scene.scene_id, "start": cursor, "end": cursor+record.duration,
                                 "file": record.file, "voice": production.scenes[scene.scene_id].audio,
                                 "sound": self.finishing.resolved_sound(scene.scene_id, state.music_plan.scenes[scene.scene_id]),
                                 "finishing": self.finishing.signature(scene.scene_id)})
                cursor += record.duration
            atomic_write(work / "timeline.json", json.dumps({"duration": cursor, "options": state.options.model_dump(mode="json"), "scenes": timeline}, ensure_ascii=False, indent=2))
            self.assembly_progress(10, "Assembling video and audio")
            last_percent = 10

            def encoded(fraction):
                nonlocal last_percent
                percent = min(90, 10 + int(80 * fraction))
                if percent > last_percent:
                    self.assembly_progress(percent, "Validating final video" if fraction >= 1 else "Assembling video and audio")
                    last_percent = percent

            self.ffmpeg.assemble([self.assets.local_path(state.scenes[s.scene_id].file) for s in board], work / "final.mp4",
                                 work / "final.srt" if state.options.subtitles == "soft" else None,
                                 durations=[state.scenes[s.scene_id].duration for s in board], fps=state.options.fps, progress=encoded)
            last_percent = 92
            self.assembly_progress(last_percent, "Saving final video and downloads")
            state.final_file = (work / "final.mp4").relative_to(self.path).as_posix()
            state.final_hash, state.final_input_hash = file_hash(work / "final.mp4"), key
            state.final_exports = {(work / name).relative_to(self.path).as_posix(): file_hash(work / name)
                                   for name in ("final.srt", "final.vtt", "timeline.json")}
            state.final_revision += 1
            state.status, state.approval = "review", None
            # Stable download paths; previous version folders remain immutable.
            for name in ("final.mp4", "final.srt", "final.vtt", "timeline.json"):
                atomic_copy(work / name, self.path / "output" / name)
            atomic_write(self.path / "output" / "final_review.md", f"# Final review\n\nDuration: {cursor:.3f} seconds. Scenes: {len(board)}.\n\nReview picture, motion, voice, subtitles, music/SFX balance and transitions before approving.\n")
            self._save(state)
            self.assembly_progress(100, "Final video ready", status="completed")
        except Exception as exc:
            self.assembly_progress(last_percent, "Assembly failed", status="failed", error=str(exc))
            state.status, state.error = "failed", str(exc)
            self._save(state)
            raise

    def approve(self, note=""):
        episode=bool(self.manager.load(self.project_id).world_project_id)
        if episode:
            from .scene_continuity_review import current
            if not current(self):
                raise ApprovalRequired("Review current animated scenes with OpenAI before final approval")
        production, board = self._input()
        with project_lock(self.path):
            state = self._refresh(production, board)
            if state.options.continuity_animation and not episode:
                from .animation import AnimationDirector
                from .animation_rendering import continuity_signature
                director = AnimationDirector(self.manager, self.project_id)
                approval = director.load().continuity_approval
                if not approval or approval.content_hash != continuity_signature(director):
                    raise ApprovalRequired("Review and approve current animated shot continuity before final approval")
            if state.status not in ("review", "approved") or not self._final_intact(state):
                raise ValueError("Render and review a current final video before approval")
            state.approval = ApprovalRecord(content_hash=state.final_input_hash, note=note)
            state.status = "approved"
            self._save(state)
            return state
