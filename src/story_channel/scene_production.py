"""Per-scene direction review and silent visual generation before voice assembly."""
import json
from uuid import uuid4
from pathlib import Path

from .animation import AnimationDirector
from .animation_models import ShotPlan, Performance
from .models import Model, Identifier, ApprovalRecord, ProviderConfig, GeneratedAsset
from .files import atomic_write, read_text, input_hash
from .locking import project_lock
from .media_utils import file_hash
from .pipeline import ApprovalRequired


class SceneDirection(Model):
    scene_id: Identifier
    shots: list[ShotPlan]
    performances: list[Performance]


class SceneProduction:
    def __init__(self, manager, project_id):
        self.manager, self.project_id = manager, project_id
        self.director = AnimationDirector(manager, project_id)
        self.assets, self.path = self.director.assets, self.director.path
        self.stop_requested = lambda: False

    def direction(self, sid):
        state = self.director.load(required=True)
        self.director.validate_plan(state.plan)
        return SceneDirection(scene_id=sid, shots=[s for s in state.plan.shots if s.scene_id == sid],
                              performances=[p for p in state.plan.performances if p.scene_id == sid])

    def direction_hash(self, sid):
        record = self.assets.load().records.get("scene_images:" + sid)
        if not record or not self.assets._intact(record):
            raise ValueError("Generate the current scene snapshot before reviewing direction.")
        return input_hash({"direction": self.direction(sid).model_dump(mode="json"),
                           "source": self.director.source(), "image": record.file_hash if record else None})

    def direction_approved(self, sid):
        state = self.director.load()
        approval = state.scene_approvals.get(sid) if state else None
        try:
            return bool(approval and approval.content_hash == self.direction_hash(sid))
        except (OSError, ValueError):
            return False

    def proposals(self):
        file = self.path / "scene_direction_reviews.json"
        return json.loads(read_text(file)) if file.exists() else {}

    def propose(self, sid, instructions=""):
        from .storyboard_asset_check import require
        require(self.manager,self.project_id)
        with project_lock(self.path):
            current = self.direction(sid)
            if not current.shots:
                raise ValueError("Build the current scene/shot plan first.")
            output = current.model_copy(deep=True)
            if not self.assets.demo:
                from .modification import modify_content
                scene = next(s for s in self.assets.creative.artifact("storyboard").scenes if s.scene_id == sid)
                from .studio import WorldStudio
                from .animation_formats import saved_guidance
                manifest = self.manager.load(self.project_id)
                output = modify_content(self.assets.creative, current,
                    "Generate/refine cinematic direction for this scene. Check typos, action, interaction, emotion, pacing, "
                    "camera and continuity. Preserve cameraStartAngle/cameraEndAngle and targetArea; unwrapped yaw differences specify orbit direction and sweep. "
                    "Preserve dialogue text, all IDs, line order, shot count and approved identity. "
                    "Make targeted improvements following: " + instructions + "\n" + saved_guidance(self.path), "scene_direction:" + sid,
                    context={"scene": scene.model_dump(mode="json", by_alias=True),
                             "canon": manifest.canon_snapshot or WorldStudio(self.manager, manifest.world_project_id or self.project_id).canon()})
            reviews = self.proposals()
            review = {"reviewId": uuid4().hex, "sourceHash": self.direction_hash(sid), "original": current.model_dump(mode="json", by_alias=True),
                      "suggested": output.model_dump(mode="json", by_alias=True), "instructions": instructions}
            reviews[sid] = review
            atomic_write(self.path / "scene_direction_reviews.json", json.dumps(reviews, ensure_ascii=False, indent=2))
            return review

    def approve_direction(self, sid, *, expected, text=None, choice="revert", review_id=None):
        with project_lock(self.path):
            if self.direction_hash(sid) != expected:
                raise ValueError("Scene direction or image changed. Review the current scene.")
            if review_id is not None and self.proposals().get(sid, {}).get("reviewId") != review_id:
                raise ValueError("Direction suggestions changed. Reload the current review.")
            state = self.director.load(required=True)
            if choice == "apply":
                edited = SceneDirection.model_validate_json(text)
                from .modification import identities
                if identities(edited.model_dump()) != identities(self.direction(sid).model_dump()):
                    raise ValueError("Keep scene/shot/speaker IDs, line order and structure unchanged.")
                shot_map = {s.shot_id: s for s in edited.shots}
                performance_map = {(p.scene_id, p.line_index): p for p in edited.performances}
                state.plan.shots = [shot_map[s.shot_id] if s.scene_id == sid else s for s in state.plan.shots]
                state.plan.performances = [performance_map[(p.scene_id, p.line_index)] if p.scene_id == sid else p for p in state.plan.performances]
                self.director.validate_plan(state.plan)
                state.plan_approval = state.performance_approval = state.continuity_approval = None
                self.director.save_state(state)
            elif choice != "revert":
                raise ValueError("Apply or revert the scene direction changes before saving.")
            state.scene_approvals[sid] = ApprovalRecord(content_hash=self.direction_hash(sid))
            self.director.save_state(state)
            reviews = self.proposals()
            if sid in reviews:
                reviews[sid].update(choice=choice, appliedText=text if choice == "apply" else None, savedHash=self.direction_hash(sid))
                atomic_write(self.path / "scene_direction_reviews.json", json.dumps(reviews, ensure_ascii=False, indent=2))
            return state

    def visuals(self):
        file = self.path / "visual_scenes.json"
        return json.loads(read_text(file)) if file.exists() else {}

    def visual_hash(self, sid, options):
        fields = {"provider", "generation_mode", "video_model", "video_seconds", "video_size", "continuity_animation"}
        if options.delivery_formats or options.portrait_safe_area:
            fields.update({"delivery_formats","portrait_safe_area"})
        if options.provider == "local":
            fields.update({"width", "height", "fps", "motion"})
        return input_hash({"direction": self.direction_hash(sid), "options": options.model_dump(mode="json", include=fields)})

    def visual_current(self, sid, options, *, approved=False):
        record = self.visuals().get(sid)
        try:
            from .animation_formats import delivery_formats
            if options.delivery_formats and (not record or any(
                    not (version := record.get("formats",{}).get(name)) or version.get("status") != "succeeded"
                    or file_hash(self.assets.local_path(version["asset"]["filePath"])) != version["fileHash"]
                    for name in delivery_formats(options))):
                return False
            return bool(record and record.get("status") == "succeeded" and record["sourceHash"] == self.visual_hash(sid, options)
                        and file_hash(self.assets.local_path(record["asset"]["filePath"])) == record["fileHash"]
                        and (not approved or record.get("approval")))
        except (OSError, ValueError, KeyError):
            return False

    def generate_visual(self, sid, options, *, force=False):
        from .ffmpeg_renderer import FFmpeg
        from .asset_models import SceneAssets
        from .animation_rendering import ShotRenderer
        from .balanced_providers import video_provider
        from .video_providers import LocalVideoProvider
        from .library_media import LibraryMedia
        self.director.require_ready()
        if not self.direction_approved(sid):
            raise ApprovalRequired("Review and approve this scene's direction before animation.")
        if not force and self.visual_current(sid, options):
            return self.visuals()[sid]
        with project_lock(self.path):
            scene = next(s for s in self.assets.creative.artifact("storyboard").scenes if s.scene_id == sid)
            production = self.assets.load()
            image = production.records.get("scene_images:" + sid)
            if not image or not self.assets._intact(image):
                raise ValueError("Generate the current scene snapshot first.")
            work = self.path / "videos" / "visual_scenes" / sid / uuid4().hex
            work.mkdir(parents=True)
            key = self.visual_hash(sid, options)
            visuals = self.visuals()
            previous = visuals.get(sid, {})
            if previous.get("asset"):
                atomic_write(self.path / "history" / "visual_scenes" / sid / f"{uuid4().hex}.json",
                             json.dumps(previous, ensure_ascii=False, indent=2))
            record = {**previous, "status": "running", "sourceHash": key, "revision": previous.get("revision", 0),
                      "attempts": previous.get("attempts", 0) + 1, "approval": None, "error": ""}
            from .animation_formats import delivery_formats,composition_guidance,create_portrait,LANDSCAPE,PORTRAIT
            formats = delivery_formats(options)
            record["formats"] = {name:{"status":"queued"} for name in formats}
            record["formats"][formats[0]]["status"] = "running"
            visuals[sid] = record
            atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2))
            try:
                ffmpeg = FFmpeg(self.manager.settings.ffmpeg_path, self.manager.settings.render_timeout_sec)
                reusable = previous.get("formats",{}).get(formats[0],{})
                reuse_base = (not force and previous.get("sourceHash") == key
                    and reusable.get("status") == "succeeded" and reusable.get("asset"))
                if reuse_base:
                    try:
                        reuse_base = file_hash(self.assets.local_path(reusable["asset"]["filePath"])) == reusable["fileHash"]
                    except (OSError,KeyError,ValueError):
                        reuse_base = False
                if reuse_base:
                    clip = GeneratedAsset.model_validate(reusable["asset"])
                else:
                    if options.continuity_animation:
                        production.scenes[sid] = SceneAssets(scene_id=sid, image=image.asset.file_path, audio="", srt="", vtt="", timing="", duration=scene.duration_sec)
                        clip = ShotRenderer(self.director, ffmpeg, video_provider(options, self.manager.settings), self.stop_requested).render(scene, production, options, work / "visual.mp4", force=force)
                    else:
                        provider = LocalVideoProvider(ffmpeg) if options.provider == "local" else video_provider(options, self.manager.settings)
                        prompt = "\n".join(s.action + ". Camera: " + s.camera for s in self.direction(sid).shots)
                        if options.generation_mode == "text":
                            source_prompt = next(p.video_prompt for p in self.assets.creative.artifact("prompts").prompts if p.scene_id == sid)
                            prompt = source_prompt + "\nApproved direction: " + prompt
                        prompt += "\n" + composition_guidance(options)
                        if options.provider == "runway":
                            from .provider_prompts import compile_prompt
                            prompt = compile_prompt(self.assets.creative, prompt, video=True, task_key=f"animation:visual:{sid}:prompt")
                        config = ProviderConfig(provider=options.provider, model="ffmpeg-image-motion" if options.provider == "local" else options.video_model,
                            options={"output_path": str(work / "visual.mp4"), "duration": scene.duration_sec, "seconds": options.video_seconds,
                                     "size": options.video_size, "render_options": options.model_dump(mode="json")})
                        clip = LibraryMedia(self.manager, self.project_id)._call(provider, provider.generate_clip,
                            (prompt, [self.assets.local_path(image.asset.file_path)] if options.generation_mode == "image" else []), config, "animation:visual:" + sid)
                clip.file_path = Path(clip.file_path).relative_to(self.path).as_posix() if Path(clip.file_path).is_absolute() else clip.file_path
                clip.generation_params.update(deliveryFormats=formats,portraitSafeArea=options.portrait_safe_area)
                record["formats"][formats[0]] = {"status":"succeeded","asset":clip.model_dump(mode="json",by_alias=True),
                    "fileHash":file_hash(self.assets.local_path(clip.file_path))}
                # Persist landscape before portrait processing so errors never lose a paid result.
                atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2,default=str))
                if formats == [LANDSCAPE,PORTRAIT]:
                    if self.stop_requested():
                        raise InterruptedError()
                    record["formats"][PORTRAIT]["status"] = "running"
                    atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2,default=str))
                    portrait_path = work / "portrait.mp4"
                    duration = create_portrait(ffmpeg,self.assets.local_path(clip.file_path),portrait_path,options)
                    portrait = GeneratedAsset(type="video",provider="local",model="portrait-center-crop",
                        file_path=portrait_path.relative_to(self.path).as_posix(),duration=duration,
                        prompt_hash=clip.prompt_hash,generation_params={"sourceAssetId":clip.asset_id,
                            "sourceFile":clip.file_path,"deliveryFormat":PORTRAIT,"portraitSafeArea":True})
                    record["formats"][PORTRAIT] = {"status":"succeeded","asset":portrait.model_dump(mode="json",by_alias=True),
                        "fileHash":file_hash(portrait_path)}
                record.update(status="succeeded", revision=record["revision"]+1, asset=clip.model_dump(mode="json", by_alias=True),
                              fileHash=file_hash(self.assets.local_path(clip.file_path)), approval=None)
            except InterruptedError:
                record.update(status="stopped", error="Stopped after the current shot; saved shots can be resumed.")
                for version in record.get("formats",{}).values():
                    if version["status"] == "running":
                        version["status"] = "stopped"
                atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2))
                return record
            except Exception as exc:
                record.update(status="failed", error=str(exc))
                for version in record.get("formats",{}).values():
                    if version["status"] == "running":
                        version.update(status="failed",error=str(exc))
                atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2))
                raise
            atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2))
            return record

    def approve_visual(self, sid, options, *, expected):
        with project_lock(self.path):
            if not self.visual_current(sid, options) or self.visual_hash(sid, options) != expected:
                raise ValueError("Generate and review the current animated scene before approval.")
            visuals = self.visuals()
            visuals[sid]["approval"] = ApprovalRecord(content_hash=expected).model_dump(mode="json", by_alias=True)
            atomic_write(self.path / "visual_scenes.json", json.dumps(visuals, ensure_ascii=False, indent=2))

    def fitted_visual(self, sid, options, duration, destination):
        if not self.visual_current(sid, options, approved=True):
            raise ApprovalRequired("Generate and approve the animated scene on Animations → Animated scenes first.")
        from .ffmpeg_renderer import FFmpeg
        ffmpeg = FFmpeg(self.manager.settings.ffmpeg_path, self.manager.settings.render_timeout_sec)
        source = self.assets.local_path(self.visuals()[sid]["asset"]["filePath"])
        original = ffmpeg.validate(source)
        ffmpeg.run(["-i", source, "-an", "-vf", f"setpts={(duration/original):.9f}*PTS,fps={options.fps}",
                    "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p", destination])
        clip = GeneratedAsset.model_validate(self.visuals()[sid]["asset"])
        clip.file_path = Path(destination).relative_to(self.path).as_posix()
        clip.duration = duration
        clip.generation_params.update(approvedVisual=str(source.relative_to(self.path)), fittedToVoiceSeconds=duration)
        return clip
