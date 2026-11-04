"""Animation direction, explicit voice casting, reference packs and human gates."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from .animation_models import AnimationPlan, AnimationState, VoiceProfile, Performance, LocationBible, ShotPlan, VoicePreview
from .assets import AssetPipeline
from .costs import CostTracker
from .files import atomic_write, input_hash
from .locking import project_lock
from .media_providers import DemoTTSProvider, OpenAITTSProvider
from .balanced_providers import tts_provider as production_tts_provider
from .media_utils import file_hash, wav_duration
from .models import ApprovalRecord, ProviderConfig, UsageRecord
from .pipeline import ApprovalRequired
from .request_tracking import tracked_call, request_fields


def load_animation(path):
    file = path / "animation.json"
    from .file_cache import read_model
    return read_model(file, AnimationState) if file.exists() else None


def voice_signature(profile, settings):
    return input_hash({"profile": profile.model_dump(mode="json"), "model": settings.tts_model, "provider": settings.tts_provider})


def performance_options(manager, project_id, task):
    state = load_animation(manager.project_path(project_id))
    if not state or not state.enabled or task.stage != "voices" or task.speaker == "narrator":
        return {}
    profile = state.plan.voices.get(task.speaker)
    performance = next((line for line in state.plan.performances if line.scene_id == task.scene_id and line.line_index == task.line_index), None)
    if profile is None or performance is None:
        raise ValueError("Animation direction is missing a character or dialogue line; rebuild the plan")
    performance = performance.model_copy(update={
        "pause_before": performance.pause_before if task.chunk_index == 0 else 0,
        "pause_after": performance.pause_after if task.chunk_index == task.chunk_count-1 else 0})
    return {"animation_directed": True, "voice": profile.voice_id,
            "voice_profile": profile.model_dump(mode="json"), "performance": performance.model_dump(mode="json")}


def require_voice(manager, project_id, character_id, *, require_performance=False):
    state = load_animation(manager.project_path(project_id))
    from .identity import IdentityManager
    identity = IdentityManager(manager, project_id)
    library = identity.load()
    if library and character_id != "narrator":
        identity.validate(library)
        profile = library.voices.get(character_id)
        if not profile or not profile.voice_id.strip():
            raise ApprovalRequired(f"Choose an explicit Voice ID for {character_id} in the identity library")
        if not state or not state.enabled or state.plan.voices.get(character_id) != profile:
            raise ApprovalRequired("Build current animation direction, then preview and approve the library voice before dialogue tracks")
    if not state or not state.enabled or character_id == "narrator":
        return
    profile = state.plan.voices.get(character_id)
    if not profile or not profile.voice_id.strip():
        raise ApprovalRequired(f"Choose an explicit Voice ID for {character_id}; automatic voice guessing is disabled")
    preview = state.previews.get(character_id)
    if not preview or not preview.approval or preview.profile_hash != voice_signature(profile, manager.settings):
        raise ApprovalRequired(f"Generate, listen to and approve the voice preview for {character_id}")
    assets = AssetPipeline(manager, project_id)
    if preview.asset.provider != ("demo" if assets.demo else manager.settings.tts_provider):
        raise ApprovalRequired("The voice provider changed; generate and approve a preview with the current provider")
    path = assets.local_path(preview.asset.file_path)
    if not path.exists() or file_hash(path) != preview.file_hash:
        raise ApprovalRequired(f"Voice preview for {character_id} is missing or changed; preview and approve again")
    if require_performance and (not state.performance_approval or state.performance_approval.content_hash != input_hash(state.plan.model_dump(mode="json"))):
        raise ApprovalRequired("Review and approve dialogue/performance direction before generating character tracks")


class AnimationDirector:
    def __init__(self, manager, project_id):
        self.manager, self.project_id = manager, project_id
        self.assets = AssetPipeline(manager, project_id)
        self.path = self.assets.path

    def load(self, *, required=False):
        state = load_animation(self.path)
        if required and state is None:
            raise ValueError("Build an animation plan first")
        return state

    def source(self):
        data = {name: self.assets.creative.artifact(name).model_dump(mode="json")
                for name in ("characters", "world", "storyboard")}
        from .identity import IdentityManager
        library = IdentityManager(self.manager, self.project_id).load()
        if library:
            data["identity"] = library.model_dump(mode="json", exclude={"approval"})
        return input_hash(data)

    def save_state(self, state):
        atomic_write(self.path / "animation.json", state.model_dump_json(by_alias=True, indent=2))
        atomic_write(self.path / "animation_plan.json", state.plan.model_dump_json(by_alias=True, indent=2))
        location_file = "animation_locations.json" if (self.path / "identity_library.json").exists() else "location_bible.json"
        atomic_write(self.path / location_file, json.dumps({key: value.model_dump(mode="json", by_alias=True)
                     for key, value in state.plan.locations.items()}, ensure_ascii=False, indent=2))
        atomic_write(self.path / "animation_plan.md", "# Animation direction\n\n" + "\n".join(
            f"- {shot.shot_id}: {shot.action}; camera: {shot.camera}; chain previous: {shot.chain_previous}"
            for shot in state.plan.shots) + "\n\nVoice casting, emotion and visual continuity require human approval.\n")

    def prepare(self):
        from .storyboard_asset_check import require
        require(self.manager,self.project_id)
        self.assets.creative.inspect()
        with project_lock(self.path):
            manifest = self.manager.load(self.project_id)
            if ("storyboard" not in manifest.approvals or any(not manifest.steps.get(name)
                    or manifest.steps[name].status != "succeeded" for name in ("world", "characters", "script", "storyboard"))):
                raise ApprovalRequired("Complete and approve the current Storyboard before building animation direction")
            chars = self.assets.creative.artifact("characters").characters
            world = self.assets.creative.artifact("world")
            board = self.assets.creative.artifact("storyboard").scenes
            if manifest.world_project_id:
                production = self.assets.load()
                if any(not production.records.get("scene_images:" + s.scene_id) or not self.assets._intact(production.records["scene_images:" + s.scene_id]) for s in board):
                    raise ApprovalRequired("Generate all scene snapshots before building direction.")
            old = self.load()
            plan = AnimationPlan(source_hash=self.source(), voices={c.character_id: VoiceProfile(
                character_id=c.character_id, age_range=str(c.age), speaking_style=c.speaking_style) for c in chars},
                locations={location.location_id: LocationBible(location_id=location.location_id, description=location.description)
                           for location in world.locations},
                performances=[Performance(scene_id=s.scene_id, line_index=i, character_id=line.character_id, text=line.text, audio_tags=line.audio_tags,
                              gesture=s.action, facial_expression="Match the scene's emotional intent")
                              for s in board for i, line in enumerate(s.dialogue)],
                shots=[ShotPlan(shot_id=f"{s.scene_id}_shot_01", scene_id=s.scene_id, order=1,
                                action=s.action or s.visual_description, camera=s.camera_movement or s.shot_type,
                                camera_start_angle=s.camera_start_angle, camera_end_angle=s.camera_end_angle,
                                camera_pitch=s.camera_pitch,camera_fov=s.camera_fov,target_area=s.target_area,
                                character_position=s.character_position,interaction_target=s.interaction_target,prop_ids=s.prop_ids) for s in board])
            if old:
                for cid in plan.voices:
                    if cid in old.plan.voices:
                        plan.voices[cid] = old.plan.voices[cid]
            intent_file = self.path / "performance_intent.json"
            if intent_file.exists():
                intent = json.loads(intent_file.read_text(encoding="utf-8"))
                script = self.assets.creative.artifact("script")
                if intent.get("scriptHash") == input_hash(script.model_dump(mode="json", by_alias=True)):
                    initial = {(line.scene_id, line.line_index): line
                               for item in intent.get("performances", []) if (line := Performance.model_validate(item))}
                    plan.performances = [initial[(line.scene_id, line.line_index)].model_copy(deep=True)
                        if (line.scene_id, line.line_index) in initial
                        and initial[(line.scene_id, line.line_index)].character_id == line.character_id
                        and initial[(line.scene_id, line.line_index)].text == line.text else line
                        for line in plan.performances]
            from .identity import IdentityManager
            identity = IdentityManager(self.manager, self.project_id)
            library = identity.load()
            if library:
                identity.validate(library)
                plan.voices = {key: item.model_copy(deep=True) for key, item in library.voices.items()}
                plan.locations = {key: item.model_copy(deep=True) for key, item in library.locations.items()}
                for shot in plan.shots:
                    scene = next(s for s in board if s.scene_id == shot.scene_id)
                    shot.costume_ids = [next(key for key, item in library.costumes.items() if item.character_id == cid)
                                        for cid in scene.character_ids]
                    shot.prop_ids = list(dict.fromkeys([*scene.prop_ids,*[key for key,item in library.props.items()
                        if item.owner_id in scene.character_ids or item.location_id == scene.location_id]]))
                    shot.sfx_cues = list(scene.sfx)
            for lid, config in self.assets.load().location_reference_sets.items():
                if lid in plan.locations:
                    plan.locations[lid].layout = config.get("floor_plan", "")
                    plan.locations[lid].fixed_props = json.dumps(config.get("object_layout", {}), ensure_ascii=False)
            state = AnimationState(plan=plan, previews=old.previews if old else {},
                                   emotion_previews=old.emotion_previews if old else {})
            if manifest.world_project_id and (self.path / "library_media.json").exists():
                from .models import GeneratedAsset, AudioAsset
                shared = json.loads((self.path / "library_media.json").read_text(encoding="utf-8"))
                from .library_media import LibraryMedia
                media = LibraryMedia(self.manager, self.project_id)
                for cid, profile in plan.voices.items():
                    if not media.voice_approved(cid, profile, shared):
                        continue
                    for sample in media.selected_samples(cid, profile, shared):
                        if not sample.get("audioAsset"):
                            continue
                        if (sample["audioAsset"].get("provider") != ("demo" if self.assets.demo else self.manager.settings.tts_provider)
                                or sample["audioAsset"].get("model") != ("demo-voice" if self.assets.demo else self.manager.settings.tts_model)):
                            continue
                        path = self.assets.local_path(sample["audio"])
                        if not path.exists() or file_hash(path) != sample["fileHash"]:
                            continue
                        signature = voice_signature(profile, self.manager.settings)
                        asset_model = AudioAsset if "voiceId" in sample["audioAsset"] else GeneratedAsset
                        preview = VoicePreview(profile_hash=signature, file_hash=sample["fileHash"],
                            asset=asset_model.model_validate(sample["audioAsset"]), approval=ApprovalRecord(content_hash=signature))
                        if sample["emotion"] == "neutral":
                            state.previews[cid] = preview
                        else:
                            state.emotion_previews.setdefault(cid, {})[sample["emotion"]] = preview
            self.save_state(state)
            return state

    def validate_plan(self, plan):
        if plan.source_hash != self.source():
            raise ValueError("Storyboard/characters/world changed; rebuild the animation plan")
        chars = {c.character_id for c in self.assets.creative.artifact("characters").characters}
        board = self.assets.creative.artifact("storyboard").scenes
        if set(plan.voices) != chars or any(cid != profile.character_id for cid, profile in plan.voices.items()):
            raise ValueError("Keep the current character IDs in every voice profile")
        logical_voices = {}
        used_characters = {cid for scene in board for cid in scene.character_ids}
        for character in self.assets.creative.artifact("characters").characters:
            if self.manager.load(self.project_id).world_project_id and character.character_id not in used_characters:
                continue
            chosen = plan.voices[character.character_id].voice_id
            if character.voice_id in logical_voices and logical_voices[character.voice_id] != chosen:
                raise ValueError("Characters sharing a logical Voice ID must share its voice; give them distinct logical Voice IDs in Characters to cast different voices")
            logical_voices[character.voice_id] = chosen
        locations = {s.location_id for s in board}
        if not locations <= set(plan.locations) or any(k != v.location_id for k, v in plan.locations.items()):
            raise ValueError("Keep the storyboard location IDs in the location bible")
        expected = [(s.scene_id, i, line.character_id, line.text) for s in board for i, line in enumerate(s.dialogue)]
        if [(line.scene_id, line.line_index, line.character_id, line.text) for line in plan.performances] != expected:
            raise ValueError("Performance direction must preserve dialogue text, character IDs and line order; edit Storyboard to change words")
        ids = [shot.shot_id for shot in plan.shots]
        if len(ids) != len(set(ids)) or {shot.scene_id for shot in plan.shots} != {s.scene_id for s in board}:
            raise ValueError("Use unique shot IDs and cover exactly the storyboard scenes")
        for scene in board:
            shots = [shot for shot in plan.shots if shot.scene_id == scene.scene_id]
            if [shot.order for shot in shots] != list(range(1, len(shots)+1)):
                raise ValueError("Shot orders must be consecutive within each scene")
        from .identity import IdentityManager
        identity = IdentityManager(self.manager, self.project_id)
        library = identity.load()
        if library:
            identity.validate(library)
            expected_locations = {key:item.model_copy(deep=True) for key,item in library.locations.items()}
            for lid,config in self.assets.load().location_reference_sets.items():
                if lid in expected_locations:
                    expected_locations[lid].layout = config.get("floor_plan","")
                    expected_locations[lid].fixed_props = json.dumps(config.get("object_layout",{}),ensure_ascii=False)
            if plan.voices != library.voices or plan.locations != expected_locations:
                raise ValueError("Edit reusable voice/location profiles in the identity library, then rebuild direction")
            for shot in plan.shots:
                scene = next(s for s in board if s.scene_id == shot.scene_id)
                if not set(shot.costume_ids) <= library.costumes.keys() or not set(shot.prop_ids) <= library.props.keys():
                    raise ValueError("Shots must use registered costume and prop IDs")
                if any(library.costumes[key].character_id not in scene.character_ids for key in shot.costume_ids):
                    raise ValueError("Shot costume belongs to a character outside this scene")
                owners = [library.costumes[key].character_id for key in shot.costume_ids]
                if sorted(owners) != sorted(scene.character_ids):
                    raise ValueError("Choose exactly one registered costume for each character in the shot")

    def save_plan(self, plan):
        plan = AnimationPlan.model_validate(plan)
        from .voice_validation import validate_provider_voice
        previous = self.load()
        if previous:
            for cid, profile in plan.voices.items():
                profile.voice_id = profile.voice_id.strip()
                old_profile = previous.plan.voices.get(cid)
                if profile.voice_id and (not old_profile or old_profile.voice_id != profile.voice_id):
                    validate_provider_voice(self.manager, self.project_id, profile.voice_id, speaker=cid, demo=self.assets.demo)
        with project_lock(self.path):
            self.validate_plan(plan)
            state = self.load()
            if not state:
                raise ValueError("Build an animation plan first")
            atomic_write(self.path / "history" / "animation" / f"{uuid4().hex}.json", state.model_dump_json(by_alias=True, indent=2))
            if state.plan != plan:
                state.plan_approval = state.performance_approval = state.continuity_approval = None
            state.plan = plan
            self.save_state(state)
            return state

    def import_performance(self, shot_id, data, *, expected):
        """Bind a reviewed driving video to one explicitly selected close-up."""
        from .media_utils import write_bytes
        from .ffmpeg_renderer import FFmpeg
        import hashlib
        with project_lock(self.path):
            state = self.load(required=True)
            if state.plan.model_dump_json(by_alias=True) != expected:
                raise ValueError("Direction changed; reopen the performance upload")
            plan = state.plan.model_copy(deep=True)
            shot = next((s for s in plan.shots if s.shot_id == shot_id), None)
            if not shot:
                raise ValueError("Unknown shot")
            scene = next(s for s in self.assets.creative.artifact("storyboard").scenes if s.scene_id == shot.scene_id)
            if not scene.dialogue or len(scene.character_ids) != 1:
                raise ValueError("Select a single-character dialogue close-up; split multi-character scenes first")
            if data is not None and not any(word in (scene.shot_type + " " + shot.camera).lower() for word in ("close", "近景", "特寫")):
                raise ValueError("Act-Two requires an important dialogue close-up with a clearly visible face")
            if data is not None:
                if not data or len(data) > 100 * 1024 * 1024:
                    raise ValueError("Driving video must be MP4 up to 100 MB")
                path = self.path / "videos" / "performances" / (hashlib.sha256(data).hexdigest() + ".mp4")
                write_bytes(path, data)
                if not 3 <= FFmpeg().validate(path) <= 30:
                    raise ValueError("Driving video must be between 3 and 30 seconds")
                shot.performance_video = path.relative_to(self.path).as_posix()
                shot.performance_character_id = scene.character_ids[0]
            else:
                shot.performance_video = shot.performance_character_id = ""
            self.validate_plan(plan)
            atomic_write(self.path / "history" / "animation" / f"{uuid4().hex}.json", state.model_dump_json(by_alias=True, indent=2))
            state.plan = plan
            state.plan_approval = state.performance_approval = state.continuity_approval = None
            self.save_state(state)
            return state

    def references(self):
        production = self.assets.load()
        pack = {}
        for key, record in production.records.items():
            if record.stage in ("character_references", "backgrounds", "scene_images") and record.status == "succeeded" and self.assets._intact(record):
                pack[key] = {"file": record.asset.file_path, "sha256": record.file_hash,
                             "assetId": record.asset.asset_id, "referenceHashes": record.asset.generation_params.get("referenceHashes", {})}
        return pack

    def direction_hash(self, state):
        performances = {}
        for shot in state.plan.shots:
            if shot.last_frame:
                try:
                    performances[shot.shot_id + ":lastFrame"] = file_hash(self.assets.local_path(shot.last_frame))
                except (OSError, ValueError):
                    performances[shot.shot_id + ":lastFrame"] = None
            if shot.performance_video:
                try:
                    performances[shot.shot_id] = file_hash(self.assets.local_path(shot.performance_video))
                except (OSError, ValueError):
                    performances[shot.shot_id] = None
        return input_hash({"plan": state.plan.model_dump(mode="json"), "references": self.references(), "drivingPerformances": performances})

    def approve_plan(self):
        with project_lock(self.path):
            state = self.load(required=True)
            self.validate_plan(state.plan)
            manifest = self.manager.load(self.project_id)
            if manifest.world_project_id:
                from .scene_production import SceneProduction
                scenes = {shot.scene_id for shot in state.plan.shots}
                service = SceneProduction(self.manager, self.project_id)
                if not all(service.direction_approved(sid) for sid in scenes):
                    raise ApprovalRequired("Review and approve direction for every scene first.")
            if not all(gate in manifest.asset_approvals for gate in ("characters", "backgrounds")):
                raise ApprovalRequired("Approve character and background references before animation direction")
            state.plan_approval = ApprovalRecord(content_hash=self.direction_hash(state))
            atomic_write(self.path / "reference_pack.json", json.dumps({"assets": self.references(),
                "characters": {c.character_id: {"appearance": c.appearance, "costume": c.clothing,
                    "immutableTraits": c.immutable_traits, "identitySource": f"character_references:{c.character_id}"}
                    for c in self.assets.creative.artifact("characters").characters},
                "locations": {key: value.model_dump(mode="json", by_alias=True) for key, value in state.plan.locations.items()}}, ensure_ascii=False, indent=2))
            self.save_state(state)
            return state

    def preview_voice(self, cid, text, *, provider=None, emotion=None):
        from .ffmpeg_renderer import FFmpeg
        with project_lock(self.path):
            if "storyboard" not in self.manager.load(self.project_id).approvals:
                raise ApprovalRequired("Approve Storyboard before generating voice previews")
            state = self.load(required=True)
            self.validate_plan(state.plan)
            if cid not in state.plan.voices:
                raise ValueError("Unknown animation character ID")
            profile = state.plan.voices[cid]
            if emotion is not None and emotion not in ("happy", "sad", "angry", "afraid", "excited"):
                raise ValueError("Choose a supported preview emotion")
            delivery = profile.model_copy(update={"baseline_emotion": emotion}) if emotion else profile
            if not profile.voice_id.strip():
                raise ValueError("Choose an explicit Voice ID before generating a preview")
            if not text.strip() or len(text) > 300:
                raise ValueError("Enter a short preview line (1–300 characters)")
            provider = provider or (DemoTTSProvider() if self.assets.demo else production_tts_provider(self.manager.settings))
            work = self.path / "audio" / "voice_previews" / cid / uuid4().hex
            work.mkdir(parents=True)
            config = ProviderConfig(provider="demo" if self.assets.demo else self.manager.settings.tts_provider, model=self.manager.settings.tts_model,
                options={"output_path": str(work / "raw.wav"), "voice_profile": delivery.model_dump(mode="json")})
            outcome = "failed"
            try:
                asset = tracked_call(self.manager, self.project_id, "animation:voice_preview", provider.generate, text, profile.voice_id, config,
                                     task_key=f"animation:voice_preview:{cid}:{emotion or 'neutral'}")
                FFmpeg().run(["-i", asset.file_path, "-t", "5", "-c:a", "pcm_s16le", work / "preview.wav"])
                duration = wav_duration(work / "preview.wav")
                if duration < 3:
                    raise ValueError("Preview is shorter than 3 seconds; choose a longer sample line")
                asset.file_path = (work / "preview.wav").relative_to(self.path).as_posix()
                asset.duration = duration
                preview = VoicePreview(profile_hash=voice_signature(profile, self.manager.settings), asset=asset,
                                       file_hash=file_hash(work / "preview.wav"))
                if emotion:
                    state.emotion_previews.setdefault(cid, {})[emotion] = preview
                    if cid in state.previews:
                        state.previews[cid].approval = None
                else:
                    state.previews[cid] = preview
                state.performance_approval = state.continuity_approval = None
                self.save_state(state)
                outcome = "succeeded"
                return state
            finally:
                usage = dict(getattr(provider, "last_usage", {}) or {})
                rate = self.manager.settings.elevenlabs_tts_cost_per_million_characters if config.provider == "elevenlabs" else self.manager.settings.tts_cost_per_million_characters
                known = self.assets.demo or rate is not None and "audio_characters" in usage
                cost = rate * Decimal(usage.get("audio_characters", 0)) / 1000000 if not self.assets.demo and rate is not None else Decimal(0)
                CostTracker(self.manager, self.project_id).record(UsageRecord(provider=config.provider, model=config.model,
                    step="animation:voice_preview", outcome=outcome, audio_characters=usage.get("audio_characters", 0),
                    cost=cost, estimated_cost=cost, pricing_known=known, **request_fields(usage)))

    def approve_voice(self, cid):
        with project_lock(self.path):
            state = self.load(required=True)
            if cid not in state.plan.voices:
                raise ValueError("Unknown animation character ID")
            preview = state.previews.get(cid)
            if not preview or preview.profile_hash != voice_signature(state.plan.voices[cid], self.manager.settings):
                raise ValueError("Generate a preview for the current voice profile first")
            if preview.asset.provider != ("demo" if self.assets.demo else self.manager.settings.tts_provider):
                raise ValueError("Generate a preview with the current voice provider before approval")
            if file_hash(self.assets.local_path(preview.asset.file_path)) != preview.file_hash:
                raise ValueError("Preview changed; generate it again")
            for emotion, sample in state.emotion_previews.get(cid, {}).items():
                if sample.profile_hash != preview.profile_hash or sample.asset.provider != preview.asset.provider:
                    raise ValueError(f"Regenerate the {emotion} sample for the current voice profile before approval")
                if file_hash(self.assets.local_path(sample.asset.file_path)) != sample.file_hash:
                    raise ValueError(f"The {emotion} sample changed; regenerate it before approval")
                sample.approval = ApprovalRecord(content_hash=sample.profile_hash)
            preview.approval = ApprovalRecord(content_hash=preview.profile_hash)
            self.save_state(state)
            return state

    def approve_performance(self):
        with project_lock(self.path):
            state = self.load(required=True)
            self.validate_plan(state.plan)
            for cid in {line.character_id for line in state.plan.performances}:
                require_voice(self.manager, self.project_id, cid)
            state.performance_approval = ApprovalRecord(content_hash=input_hash(state.plan.model_dump(mode="json")))
            self.save_state(state)
            return state

    def require_ready(self):
        from .storyboard_asset_check import require
        require(self.manager,self.project_id)
        state = self.load()
        if not state or not state.enabled:
            raise ApprovalRequired("Build the animation direction plan on Animations first")
        self.validate_plan(state.plan)
        from .identity import IdentityManager
        identity = IdentityManager(self.manager, self.project_id)
        if identity.load():
            identity.require_approved()
        if not state.plan_approval or state.plan_approval.content_hash != self.direction_hash(state):
            raise ApprovalRequired("Review and approve the current animation/reference plan")
        if not state.performance_approval or state.performance_approval.content_hash != input_hash(state.plan.model_dump(mode="json")):
            raise ApprovalRequired("Review and approve dialogue/performance direction and voice previews")
        for cid in {line.character_id for line in state.plan.performances}:
            require_voice(self.manager, self.project_id, cid)
        return state

    def modify_with_openai(self, instructions, expected):
        from .storyboard_asset_check import require
        require(self.manager,self.project_id)
        from .modification import modify_content
        from .animation_models import DirectionEdit
        with project_lock(self.path):
            state = self.load()
            if state.plan.model_dump_json(by_alias=True) != expected:
                raise ValueError("Animation plan changed; reopen the modification dialog")
            current = DirectionEdit(source_hash=state.plan.source_hash, voices=list(state.plan.voices.values()),
                locations=list(state.plan.locations.values()), performances=state.plan.performances, shots=state.plan.shots)
            edited = modify_content(self.assets.creative, current, instructions, "animation_plan")
            plan = AnimationPlan(source_hash=edited.source_hash, voices={p.character_id: p for p in edited.voices},
                locations={p.location_id: p for p in edited.locations}, performances=edited.performances, shots=edited.shots)
            self.validate_plan(plan)
            state.plan = plan
            state.plan_approval = state.performance_approval = state.continuity_approval = None
            self.save_state(state)
            return state
