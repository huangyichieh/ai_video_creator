"""Shot caching and first-frame chaining, with reference and continuity audits."""
from .balanced_providers import ActTwoProvider
from decimal import Decimal
import json
import math
from pathlib import Path
from uuid import uuid4

from .animation import AnimationDirector
from .animation_models import AnimatedShot
from .costs import CostTracker
from .files import atomic_write, input_hash
from .llm import TransientProviderError
from .media_utils import file_hash
from .models import ApprovalRecord, GeneratedAsset, ProviderConfig, UsageRecord
from .pipeline import ApprovalRequired
from .request_tracking import tracked_call, request_fields


class ShotRenderer:
    def __init__(self, director, ffmpeg, provider, stop_requested=lambda: False):
        self.director, self.ffmpeg, self.provider = director, ffmpeg, provider
        self.stop_requested = stop_requested
        self.manager, self.project_id, self.path = director.manager, director.project_id, director.path

    def previous_frame(self, scene, board, options=None):
        position = next(i for i, item in enumerate(board) if item.scene_id == scene.scene_id)
        if position == 0:
            return None
        previous = board[position-1]
        if previous.location_id != scene.location_id or set(previous.character_ids) != set(scene.character_ids):
            return None
        state = self.director.load()
        records = sorted((r for r in state.shots.values() if r.scene_id == previous.scene_id and r.status == "succeeded" and r.asset and r.end_frame
                          and (options is None or r.asset.generation_params.get("videoSize","1280x720") == options.video_size)),
                         key=lambda r: r.asset.generation_params.get("sequence", [0, 0]))
        if not records:
            return None
        record = records[-1]
        path = self.director.assets.local_path(record.end_frame)
        return path if path.exists() and file_hash(path) == record.end_frame_hash else None

    def render(self, scene, production, options, destination, *, force=False):
        if options.provider not in ("openai", "runway") or options.generation_mode != "image":
            raise ValueError("Continuity animation requires OpenAI image-to-video; local preview and text-only modes do not enforce identity references")
        state = self.director.require_ready()
        board = self.director.assets.creative.artifact("storyboard").scenes
        pack = self.director.references()
        from .location_references import LocationReferenceResolver, camera_segments
        resolver = LocationReferenceResolver(self.director.assets, production)
        manifest = self.manager.load(self.project_id)
        backgrounds = resolver.resolve(scene.location_id, scene.camera_start_angle, scene.camera_end_angle, scene.target_area,
            pitch=scene.camera_pitch,fov=scene.camera_fov,character_position=scene.character_position,
            interaction_target=scene.interaction_target) if manifest.workflow_version == "world_episode" else [f"backgrounds:{scene.location_id}"]
        from .shared_room import merge_references
        merge_references(self.director.assets,production,backgrounds,pack)
        required = [f"character_references:{cid}" for cid in scene.character_ids] + backgrounds + [f"scene_images:{scene.scene_id}"]
        if any(key not in pack for key in required):
            raise ValueError("Each animated shot requires intact character, location and approved scene-image references")
        scene_provenance = pack[f"scene_images:{scene.scene_id}"]["referenceHashes"]
        if any(scene_provenance.get(key) != pack[key]["sha256"] for key in required[:-1]):
            raise ValueError("The scene image was not built from the current reference pack; regenerate it")
        references = {key: pack[key] for key in required}
        from .identity import IdentityManager
        library = IdentityManager(self.manager, self.project_id).load()
        plans = [shot for shot in state.plan.shots if shot.scene_id == scene.scene_id]
        scene_duration = production.scenes[scene.scene_id].duration
        previous = self.previous_frame(scene, board, options)
        paths, durations, active = [], [], set()
        for plan in plans:
            share = scene_duration / len(plans)
            count = max(1, math.ceil(share / min(8, options.video_seconds - plan.overlap_seconds)))
            camera_start = plan.camera_start_angle if plan.camera_start_angle is not None else scene.camera_start_angle or 0
            camera_end = plan.camera_end_angle if plan.camera_end_angle is not None else scene.camera_end_angle
            camera_end = camera_start if camera_end is None else camera_end
            count = max(count, len(camera_segments(camera_start, camera_end)))
            target_area = plan.target_area or scene.target_area or plan.interaction_target
            duration = share/count
            for part in range(count):
                if self.stop_requested():
                    raise InterruptedError("Stopped after the current animation shot. Completed shots were saved.")
                sid = f"{plan.shot_id}_part_{part+1:03d}"
                active.add(sid)
                record = state.shots.get(sid)
                start_angle = camera_start + (camera_end-camera_start)*part/count
                end_angle = camera_start + (camera_end-camera_start)*(part+1)/count
                selected_backgrounds = resolver.resolve(scene.location_id, start_angle, end_angle, target_area,
                    pitch=plan.camera_pitch,fov=plan.camera_fov,
                    character_position=plan.character_position if plan.character_position is not None else scene.character_position,
                    interaction_target=plan.interaction_target or scene.interaction_target,
                    prefer_details=True) if manifest.workflow_version == "world_episode" else backgrounds
                merge_references(self.director.assets,production,selected_backgrounds,pack)
                if not selected_backgrounds:
                    raise ValueError("Generate current location references for this shot's planned camera angles.")
                required = [f"character_references:{cid}" for cid in scene.character_ids] + selected_backgrounds + [f"scene_images:{scene.scene_id}"]
                if any(key not in pack for key in required):
                    raise ValueError("A selected shot reference is missing or outdated.")
                references = {key: pack[key] for key in required}
                start = previous if previous and (plan.chain_previous or part > 0 and camera_end != camera_start) else self.director.assets.local_path(production.scenes[scene.scene_id].image)
                prompt = (f"Animate shot {sid}, scene {scene.scene_id}. Characters: {scene.character_ids}. "
                    f"This is action segment {part+1} of {count}; continue from the current pose rather than restarting the action. "
                    f"Location ID: {scene.location_id}. Action: {plan.action}. Camera: {plan.camera}. "
                    f"Camera yaw {start_angle:g} to {end_angle:g} degrees clockwise from the fixed location center; target area: {target_area}. "
                    f"Camera pitch {plan.camera_pitch:g} degrees; horizontal FOV {plan.camera_fov:g} degrees. "
                    "Use only the selected angle/area references. Preserve the previous segment's ending frame and fixed object layout. "
                    "Matching detail backgrounds are scene-ready local views: preserve their environment appearance and perspective where relevant. "
                    "Panorama masters anchor world-space layout; never relocate fixed objects or replace a local view with a wide panorama. "
                    f"Start pose: {plan.start_pose}. End pose: {plan.end_pose}. "
                    f"Interaction target: {plan.interaction_target}. Costume IDs: {plan.costume_ids}. Prop IDs: {plan.prop_ids}. "
                    f"Location bible: {state.plan.locations[scene.location_id].model_dump_json()}. "
                    f"Preserve the supplied first frame's faces, hair, proportions, costumes, props and background structure. "
                    f"Continue motion and screen direction naturally; no redesign, teleporting or lighting changes. "
                    f"Keep {plan.overlap_seconds} seconds of extra action after the planned {duration:.3f}-second cut for motion handles. "
                    + " Performance direction: " + json.dumps([p.model_dump(mode="json") for p in state.plan.performances if p.scene_id == scene.scene_id], ensure_ascii=False))
                if library:
                    prompt += " Registered costume and prop descriptions: " + json.dumps({
                        "costumes": [library.costumes[key].model_dump(mode="json") for key in plan.costume_ids],
                        "props": [library.props[key].model_dump(mode="json") for key in plan.prop_ids]}, ensure_ascii=False)
                if record and record.modification_prompt:
                    prompt += " Requested modification to the saved shot, preserving unrelated details: " + record.modification_prompt
                from .style_rules import apply_rules
                prompt = apply_rules(prompt, production.project_style_rules)
                from .animation_formats import composition_guidance
                prompt += " " + composition_guidance(options)
                driving = None
                last_frame = None
                if plan.last_frame:
                    if options.provider != "runway" or plan.performance_video:
                        raise ValueError("Terminal keyframes are supported by Kling shots, not Act-Two or legacy OpenAI shots")
                    last_frame = self.director.assets.local_path(plan.last_frame)
                    from PIL import Image
                    with Image.open(last_frame) as image:
                        image.verify()
                if plan.performance_video:
                    if count != 1:
                        raise ValueError("Split long Act-Two performance into individual short shots before generating")
                    if options.provider != "runway" or not scene.dialogue or not any(word in (scene.shot_type + " " + plan.camera).lower() for word in ("close", "近景", "特寫")):
                        raise ValueError("Act-Two is only for selected dialogue close-ups using Runway production")
                    if len(scene.character_ids) != 1 or plan.performance_character_id != scene.character_ids[0]:
                        raise ValueError("Act-Two requires a single identified visible performer; split multi-character dialogue into separate shots")
                    driving = self.director.assets.local_path(plan.performance_video)
                    if not driving.is_file():
                        raise ValueError("Upload a driving performance video for the selected shot")
                    actual = self.ffmpeg.validate(driving)
                    if not 3 <= actual <= 30 or actual < options.video_seconds:
                        raise ValueError("Act-Two driving video must be 3-30 seconds and cover the generated shot duration")
                key = input_hash({"plan": plan.model_dump(mode="json"), "prompt": prompt, "references": references,
                    "start": file_hash(start), "seconds": options.video_seconds, "size": options.video_size,
                    "model": options.video_model, "duration": duration, "performanceVideoHash": file_hash(driving) if driving else None,
                    "lastFrameHash": file_hash(last_frame) if last_frame else None,
                    "editSource": record.modification_source if record else None})
                intact = record and record.status == "succeeded" and record.input_hash == key and record.asset
                if intact:
                    try:
                        intact = (file_hash(self.director.assets.local_path(record.asset.file_path)) == record.file_hash
                                  and file_hash(self.director.assets.local_path(record.end_frame)) == record.end_frame_hash)
                    except (OSError, ValueError):
                        intact = False
                if force or not intact:
                    record = record or AnimatedShot(shot_id=sid, scene_id=scene.scene_id)
                    if force:
                        record.remote_job_id, record.remote_job_key = None, ""
                    state.shots[sid] = record
                    record.status, record.error = "running", None
                    state.continuity_approval = None
                    work = self.path / "videos" / "shots" / sid / uuid4().hex
                    work.mkdir(parents=True)
                    self.director.save_state(state)
                    for attempt in range(self.manager.settings.llm_max_retries+1):
                        record.attempts += 1
                        outcome = "failed"
                        def update(job_id, status, progress):
                            record.remote_job_id, record.remote_job_key = job_id, key
                            record.remote_status, record.remote_progress = status, progress
                            self.director.save_state(state)
                        reuse = record.remote_job_id if (not force or attempt > 0) and record.remote_job_key == key and record.remote_status not in ("failed", "cancelled") else None
                        config = ProviderConfig(provider=options.provider, model="act_two" if plan.performance_video else options.video_model, options={
                            "output_path": str(work / "clip.mp4"), "seconds": options.video_seconds, "size": options.video_size,
                            "job_id": reuse, "job_update": update, "poll_timeout": self.manager.settings.render_timeout_sec,
                            "duration": options.video_seconds, "render_options": options.model_dump(mode="json"),
                            "edit_source": record.modification_source if record.asset and record.asset.generation_params.get("startFrameHash") == file_hash(start) else None,
                            "reference_pack": references, "last_frame": str(last_frame) if last_frame and part == count-1 else None,
                            "performance_video": str(driving) if driving else None, "performance_duration": actual if driving else None,
                            "expression_intensity": plan.expression_intensity})
                        selected_provider = ActTwoProvider(timeout=self.manager.settings.llm_timeout_sec) if driving else self.provider
                        try:
                            provider_prompt = prompt
                            from .balanced_providers import KlingVideoProvider
                            if isinstance(selected_provider, KlingVideoProvider):
                                from .provider_prompts import compile_prompt
                                provider_prompt = compile_prompt(self.director.assets.creative, prompt, video=True, task_key=f"animation:shot:{sid}:prompt")
                            asset = tracked_call(self.manager, self.project_id, f"animation:shot:{sid}", selected_provider.generate_clip, provider_prompt,
                                [start] + ([self.director.assets.local_path(pack[k]["file"]) for k in required[:-1]] if options.provider == "runway" and not driving else []), config)
                            rate = self.manager.settings.act_two_cost_per_second if driving else self.manager.settings.runway_video_cost_per_second if options.provider == "runway" else self.manager.settings.video_cost_per_second
                            if rate is not None:
                                asset.cost = rate * Decimal(str((getattr(selected_provider, "last_usage", {}) or {}).get("video_seconds", 0)))
                            clip = Path(asset.file_path).resolve()
                            if not clip.is_relative_to(self.path):
                                raise ValueError("Shot provider returned a file outside the project")
                            if self.ffmpeg.validate(clip) + 1/options.fps < duration:
                                raise ValueError("Animated clip is shorter than the planned cut; retry instead of looping it")
                            frame = work / "cut_frame.png"
                            self.ffmpeg.cut_frame(clip, frame, max(0, duration-1/options.fps))
                            asset.file_path = clip.relative_to(self.path).as_posix()
                            asset.source_scene_id = scene.scene_id
                            asset.generation_params.update(inputHash=key, referencePack=references, startFrame=str(start.relative_to(self.path)),
                                videoSize=options.video_size,deliveryFormats=options.delivery_formats,portraitSafeArea=options.portrait_safe_area,
                                startFrameHash=file_hash(start), sequence=[plan.order, part], overlapSeconds=plan.overlap_seconds,
                                direction=plan.model_dump(mode="json"), cameraStartAngle=start_angle, cameraEndAngle=end_angle,
                                targetArea=target_area, performanceHash=input_hash([
                                    p.model_dump(mode="json") for p in state.plan.performances if p.scene_id == scene.scene_id]))
                            record.asset, record.file_hash = asset, file_hash(clip)
                            record.end_frame, record.end_frame_hash = frame.relative_to(self.path).as_posix(), file_hash(frame)
                            record.duration, record.input_hash, record.status = duration, key, "succeeded"
                            record.revision += 1
                            outcome = "succeeded"
                            self.director.save_state(state)
                            break
                        except Exception as exc:
                            record.status, record.error = "failed", str(exc)
                            self.director.save_state(state)
                            if not isinstance(exc, TransientProviderError) or attempt == self.manager.settings.llm_max_retries:
                                raise
                        finally:
                            usage = dict(getattr(selected_provider, "last_usage", {}) or {})
                            seconds = usage.get("video_seconds", 0)
                            rate = self.manager.settings.act_two_cost_per_second if driving else self.manager.settings.runway_video_cost_per_second if options.provider == "runway" else self.manager.settings.video_cost_per_second
                            cost = rate * Decimal(str(seconds)) if rate is not None else Decimal(0)
                            CostTracker(self.manager, self.project_id).record(UsageRecord(provider=options.provider, model="act_two" if plan.performance_video else options.video_model,
                                provider_job_id=usage.get("job_id"),
                                step=f"animation:shot:{sid}", scene_id=scene.scene_id, video_seconds=seconds, cost=cost,
                                estimated_cost=cost, pricing_known=rate is not None and "video_seconds" in usage,
                                outcome=outcome, retry_count=attempt, **request_fields(usage)))
                previous = self.director.assets.local_path(record.end_frame)
                paths.append(self.director.assets.local_path(record.asset.file_path))
                durations.append(duration)
        state.shots = {sid: record for sid, record in state.shots.items() if record.scene_id != scene.scene_id or sid in active}
        self.director.save_state(state)
        self.ffmpeg.join_visuals(paths, durations, destination, options)
        return GeneratedAsset(type="video", provider=options.provider, model="act_two" if plan.performance_video else options.video_model, source_scene_id=scene.scene_id,
            file_path=str(destination), duration=scene_duration, prompt_hash=input_hash([s.model_dump(mode="json") for s in plans]),
            generation_params={"animatedShots": sorted(active), "referencePack": references, "looped": False})


def continuity_signature(director):
    state = director.require_ready()
    from .render_models import RenderState
    options = RenderState.model_validate_json((director.path / "render.json").read_text(encoding="utf-8")).options
    production = director.assets.load()
    pack = director.references()
    rows = []
    for scene in director.assets.creative.artifact("storyboard").scenes:
        records = [r for r in state.shots.values() if r.scene_id == scene.scene_id]
        if not records or any(r.status != "succeeded" or not r.asset for r in records):
            raise ApprovalRequired("Complete every animated shot before continuity approval")
        plans = [p for p in state.plan.shots if p.scene_id == scene.scene_id]
        expected = {f"{plan.shot_id}_part_{i+1:03d}" for plan in plans for i in range(max(1,
            math.ceil((scene.duration_sec if director.manager.load(director.project_id).world_project_id else production.scenes[scene.scene_id].duration)
                      / len(plans) / min(8, options.video_seconds-plan.overlap_seconds))))}
        if {record.shot_id for record in records} != expected:
            raise ApprovalRequired("The shot plan changed; generate every current shot before continuity approval")
        for record in records:
            if not record.end_frame:
                raise ApprovalRequired("A shot has no continuity cut frame; render it again")
            plan = next(p for p in plans if p.shot_id == record.shot_id.rsplit("_part_", 1)[0])
            used_refs = record.asset.generation_params.get("referencePack", {})
            if (record.asset.generation_params.get("direction") != plan.model_dump(mode="json")
                    or any(pack.get(key) != value for key, value in used_refs.items())
                    or record.asset.generation_params.get("performanceHash") != input_hash([
                        p.model_dump(mode="json") for p in state.plan.performances if p.scene_id == scene.scene_id])):
                raise ApprovalRequired("Shots use outdated direction or references; regenerate before continuity approval")
            if (file_hash(director.assets.local_path(record.asset.file_path)) != record.file_hash
                    or file_hash(director.assets.local_path(record.end_frame)) != record.end_frame_hash):
                raise ApprovalRequired("Shot or cut frame changed; render it again before continuity review")
            rows.append((record.shot_id, record.input_hash, record.file_hash, record.end_frame_hash))
    from .rendering import RenderPipeline
    finishing = RenderPipeline(director.manager, director.project_id).finishing
    layers = {scene.scene_id: signature for scene in director.assets.creative.artifact("storyboard").scenes
              if (signature := finishing.signature(scene.scene_id)) is not None}
    return input_hash({"direction": director.direction_hash(state), "shots": sorted(rows), **({"finishing": layers} if layers else {})})


def approve_continuity(director, note=""):
    from .locking import project_lock
    with project_lock(director.path):
        signature = continuity_signature(director)
        state = director.load()
        state.continuity_approval = ApprovalRecord(content_hash=signature, note=note)
        director.save_state(state)
        atomic_write(director.path / "continuity_review.json", json.dumps({"signature": signature,
            "technicalChecks": "Reference hashes, explicit voice approvals, shot integrity and chained cut frames",
            "humanApproved": True, "note": note,
            "limitations": "Identity, background geometry, emotional delivery and motion quality are human-reviewed, not automatically certified."}, indent=2))
        return state
