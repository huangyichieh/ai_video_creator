"""Scene-production orchestration. Never approves media or assembles Final."""
from dataclasses import dataclass
import json

from .animation import AnimationDirector
from .assets_workflow import next_assets_action
from .files import atomic_write, input_hash

STAGE_TABS = ("Scene images", "Direction & performance", "Animated scenes", "Voices & input",
        "Video effects", "Sound effects", "Scene review & continuity")
TABS = ("Check assets", *STAGE_TABS)


@dataclass(frozen=True)
class AnimationAction:
    stage: str
    title: str
    detail: str
    tab: str = STAGE_TABS[1]
    runnable: bool = False
    page: str = "Animations"


def animation_snapshot(engine, *, refresh=True):
    assets = engine.assets
    episode = bool(engine.manager.load(engine.project_id).world_project_id)
    try:
        production = assets.inspect() if refresh else assets.load()
    except (OSError, ValueError):
        # An early project still needs a visible checklist and an Assets link.
        production = assets.load()
    director = AnimationDirector(engine.manager, engine.project_id)
    direction = director.load()
    render = engine.load()
    rows = []

    def row(stage, subject, status, approval="Not required", revision=None, attempts=None, error="", tab=STAGE_TABS[1]):
        rows.append({"Stage": stage, "Subject": subject, "Status": status, "Approval": approval,
                     "Revision": revision, "Attempts": attempts, "Error": error, "Tab": tab})

    prerequisites = next_assets_action(assets, production)
    assets_ready = prerequisites.stage == "complete"
    from .storyboard_asset_check import check
    required_assets = check(engine.manager,engine.project_id) if episode else []
    missing_assets = [item for item in required_assets if not item["Ready"]]
    for item in required_assets:
        row("Check assets · "+item["Category"],item["Asset"],item["Status"],approval=item.get("Approval","Needs creation"),
            error="" if item["Ready"] else item["Scenes"],tab="Check assets")
    row("Assets prerequisites", "At least 1 character and 1 background", "Done" if assets_ready else "Not met",
        "Not required", error="" if assets_ready else "Add at least 1 character and 1 background on Assets.", tab="Assets")
    valid_plan = False
    if direction:
        try:
            director.validate_plan(direction.plan)
            valid_plan = direction.enabled
        except (OSError, ValueError):
            pass
    row("Animation direction", "Shot list / performance plan", "Ready" if valid_plan else "Missing / outdated")
    plan_approved = bool(valid_plan and direction.plan_approval and direction.plan_approval.content_hash == director.direction_hash(direction))
    performance_approved = bool(valid_plan and direction.performance_approval and direction.performance_approval.content_hash == input_hash(direction.plan.model_dump(mode="json")))
    row("Direction approval", "References, shots and locations", "Approved" if plan_approved else "Needs review", "Approved" if plan_approved else "Not approved")
    row("Performance approval", "Dialogue and character performance", "Approved" if performance_approved else "Needs review", "Approved" if performance_approved else "Not approved")
    names = {"scene_images": "Scene images", "voices": "Voice tracks", "subtitles": "Subtitle alignment", "consistency": "Scene input QA"}
    try:
        tasks = [t for t in assets.tasks(production.model_copy(deep=True)) if t.stage in names]
    except (OSError, ValueError):
        tasks = []
    for task in tasks:
        record = production.records.get(task.key)
        row(names[task.stage], task.subject, "Generating…" if record and record.status == "running" else record.status if record else "pending",
            revision=record.revision if record else 0, attempts=record.attempts if record else 0,
            error=record.error or "" if record else "", tab=STAGE_TABS[0] if task.stage == "scene_images" else STAGE_TABS[3])
    for stage in names:
        if not any(t.stage == stage for t in tasks):
            row(names[stage], "Awaiting scene inputs", "Blocked", tab=STAGE_TABS[0] if stage == "scene_images" else STAGE_TABS[3])
    inputs_approved = production.next_stage == "complete" and "assets" in engine.manager.load(engine.project_id).asset_approvals
    row("Scene input approval", "Images, dialogue, timing and QA", "Approved" if inputs_approved else "Needs review", "Approved" if inputs_approved else "Not approved", tab=STAGE_TABS[3])
    if inputs_approved and refresh:
        render = engine.inspect()
    finishing = engine.finishing.load()
    effect_tasks = {}
    if inputs_approved:
        board = assets.creative.artifact("storyboard").scenes
        effect_tasks = engine.finishing.tasks(production, board, render)
    for key, (sid, label, _) in effect_tasks.items():
        record = finishing.records.get(key)
        row("Lip-sync" if key.startswith("lipsync:") else "SFX / Ambience" if key.startswith("sfx:") else "VFX / CG",
            f"{sid} · {label}", "Generating…" if record and record.status == "running" else record.status if record else "pending",
            revision=record.revision if record else 0, attempts=record.attempts if record else 0, error=record.error or "" if record else "",
            tab=STAGE_TABS[5] if key.startswith("sfx:") else STAGE_TABS[4] if key.startswith("vfx:") else STAGE_TABS[2])
        if record and record.status == "succeeded" and not engine.finishing.task_intact(record, effect_tasks[key][2]):
            rows[-1]["Status"] = "outdated"
    for prefix, name in (("lipsync:", "Lip-sync"), ("sfx:", "SFX / Ambience"), ("vfx:", "VFX / CG")):
        if not any(key.startswith(prefix) for key in effect_tasks):
            row(name, "Configured layers", "Optional / not configured" if inputs_approved else "Awaiting scene inputs",
                tab=STAGE_TABS[5] if prefix == "sfx:" else STAGE_TABS[4] if prefix == "vfx:" else STAGE_TABS[2])
    for record in direction.shots.values() if direction else []:
        row("Video shots / chaining", record.shot_id, "Generating…" if record.status == "running" else record.status,
            revision=record.revision, attempts=record.attempts, error=record.error or "", tab=STAGE_TABS[2])
    if not direction or not direction.shots:
        row("Video shots / chaining", "Planned shots", "pending" if valid_plan else "Blocked", tab=STAGE_TABS[2])
    intact_scenes = {sid: r.status == "succeeded" and engine._scene_intact(r) for sid, r in render.scenes.items()}
    for sid, record in render.scenes.items():
        ready = intact_scenes[sid]
        approved = bool(ready and record.approved_hash == record.file_hash and record.approved_hash)
        row("Scene video", sid, "Generating…" if record.status == "running" else record.status if not (record.status == "succeeded" and not ready) else "stale",
            "Approved" if approved else "Not approved", record.revision, record.attempts, record.error or "", STAGE_TABS[6] if episode else STAGE_TABS[2])
    if not render.scenes:
        row("Scene video", "All storyboard scenes", "Awaiting scene input approval", tab=STAGE_TABS[2])
    approved_scenes = sum(bool(intact_scenes[sid] and r.approved_hash and r.approved_hash == r.file_hash) for sid, r in render.scenes.items())
    row("Scene approval", "All generated scenes", f"{approved_scenes}/{len(render.scenes)} approved",
        "Approved" if render.scenes and approved_scenes == len(render.scenes) else "Not approved", tab=STAGE_TABS[6])
    continuity_approved = not render.options.continuity_animation
    if render.options.continuity_animation and direction and direction.continuity_approval:
        from .animation_rendering import continuity_signature
        try:
            continuity_approved = direction.continuity_approval.content_hash == continuity_signature(director)
        except (OSError, ValueError):
            pass
    row("Continuity approval", "Faces, voices, motion and scene cuts", "Approved" if continuity_approved and render.options.continuity_animation else "Not enabled" if not render.options.continuity_animation else "Needs review",
        "Approved" if continuity_approved and render.options.continuity_animation else "Not required" if continuity_approved else "Not approved", tab=STAGE_TABS[6])

    scene_direction_ready, visuals_ready, visuals_approved = True, True, True
    if episode and valid_plan:
        from .scene_production import SceneProduction
        service = SceneProduction(engine.manager, engine.project_id)
        scene_ids = [s.scene_id for s in assets.creative.artifact("storyboard").scenes]
        scene_direction_ready = all(service.direction_approved(sid) for sid in scene_ids)
        visuals_ready = all(service.visual_current(sid, render.options) for sid in scene_ids)
        visuals_approved = all(service.visual_current(sid, render.options, approved=True) for sid in scene_ids)
        visual_records = service.visuals()
        for sid in scene_ids:
            row("Scene direction review", sid, "Approved" if service.direction_approved(sid) else "Needs review", tab=STAGE_TABS[1])
            record = visual_records.get(sid, {})
            row("Animated scene", sid, record.get("status", "pending"),
                "Approved" if service.visual_current(sid, render.options, approved=True) else "Not approved",
                record.get("revision", 0), record.get("attempts", 0), record.get("error", ""), STAGE_TABS[2])

    if missing_assets:
        action = AnimationAction("check_assets","Check assets",f"Create/import {len(missing_assets)} required storyboard asset(s) before animation.","Check assets")
    elif not assets_ready:
        action = AnimationAction("assets", "Add a character and background", "Add at least 1 character and 1 background; there is no full-library completion stage.", page="Assets")
    elif episode and production.next_stage == "scene_images":
        action = AnimationAction("scene_images", "Scene images", "Generate snapshots before direction and animation.", STAGE_TABS[0], assets.can_generate("scene_images", production))
    elif not valid_plan:
        action = AnimationAction("direction", "Prepare animation direction", "Build the current shot/performance plan.", runnable=True)
    elif production.next_stage == "scene_images":
        action = AnimationAction("scene_images", "Scene images", "Generate missing or outdated scene images.", STAGE_TABS[0], assets.can_generate("scene_images", production))
    elif not scene_direction_ready:
        action = AnimationAction("scene_direction_review", "Review direction for every scene", "Apply or revert each scene's direction suggestions, then approve.", STAGE_TABS[1])
    elif not plan_approved:
        action = AnimationAction("direction_review", "Approve animation and references", "Review the current shot plan and reference pack, then approve.")
    elif not performance_approved:
        action = AnimationAction("performance_review", "Approve performance direction", "Review dialogue emotions, delivery, gestures and expressions, then approve.")
    elif not visuals_ready:
        action = AnimationAction("visual_scenes", "Animated scenes", "Generate silent scene motion using approved snapshots and direction.", STAGE_TABS[2], True)
    elif not visuals_approved:
        action = AnimationAction("visual_review", "Approve animated scenes", "Inspect each generated visual and approve it before voice composition.", STAGE_TABS[2])
    elif production.next_stage in names:
        stage = production.next_stage
        action = AnimationAction(stage, names[stage], "Generate the next scene-input stage.", STAGE_TABS[3], assets.can_generate(stage, production))
    elif not inputs_approved:
        action = AnimationAction("input_review", "Approve scene inputs", "Review scene images, voice tracks, alignment and QA, then approve the asset batch.", STAGE_TABS[3])
    else:
        effects = [key for key in effect_tasks if not key.startswith("lipsync:") and not engine.finishing.task_intact(finishing.records.get(key), effect_tasks[key][2])]
        if effects:
            action = AnimationAction("effects", "SFX / Ambience and VFX", "Generate missing configured effect layers; optional layers are skipped when not configured.", STAGE_TABS[5] if effects[0].startswith("sfx:") else STAGE_TABS[4], True)
        elif not render.scenes or not all(intact_scenes.values()):
            action = AnimationAction("videos", "Compose scene voices and effects" if episode else "Scene videos / shots", "Compose approved visuals, voices and configured effects; preserve successful work.", STAGE_TABS[6] if episode else STAGE_TABS[2], True)
        elif not continuity_approved:
            action = AnimationAction("continuity_review", "Approve scene continuity", "Review character identity, motion and continuity at every cut.", STAGE_TABS[6])
        elif any(not r.approved_hash or r.approved_hash != r.file_hash for r in render.scenes.values()):
            action = AnimationAction("scene_review", f"Approve generated scenes {approved_scenes}/{len(render.scenes)}", "Review and approve every current scene before Final assembly. Approve individually below, or approve all scenes together.", STAGE_TABS[6])
        else:
            action = AnimationAction("complete", "Continue to Final", "All required scenes and continuity reviews are approved.", page="Final")
    rows = [{"Step": 0 if r["Stage"] == "Assets prerequisites" else TABS.index(r["Tab"]) + 1, **r} for r in rows]
    rows.sort(key=lambda r: r["Step"])
    return action, rows


def generate_animations(engine, *, pipeline=False, queue_run_id=None):
    from .animation_queue import AnimationQueue
    queue = AnimationQueue(engine, queue_run_id)
    if queue_run_id is None:
        initial = animation_snapshot(engine)[0]
        if not initial.runnable:
            return initial
        queue.start(initial, pipeline=pipeline)
    try:
        result = _generate_animations(engine, pipeline=pipeline, queue=queue)
    except Exception as exc:
        queue.finish("failed", str(exc))
        raise
    queue.finish("stopped" if engine.stop_requested() else "completed")
    return result


def _generate_animations(engine, *, pipeline, queue):
    """Run authorized generation only, stopping at settings, errors or reviews."""
    path = engine.path / "animation_workflow_progress.json"
    while not engine.stop_requested():
        action, _ = animation_snapshot(engine)
        if not action.runnable:
            return action
        atomic_write(path, json.dumps({"stage": action.stage, "status": "running", "title": action.title}))
        queue.stage_started(action.stage)
        try:
            if action.stage == "direction":
                AnimationDirector(engine.manager, engine.project_id).prepare()
                if engine.manager.load(engine.project_id).world_project_id:
                    from .scene_production import SceneProduction
                    service = SceneProduction(engine.manager, engine.project_id)
                    for scene in engine.assets.creative.artifact("storyboard").scenes:
                        if engine.stop_requested():
                            break
                        service.propose(scene.scene_id)
            elif action.stage == "visual_scenes":
                from .scene_production import SceneProduction
                service = SceneProduction(engine.manager, engine.project_id)
                service.stop_requested = engine.stop_requested
                for scene in engine.assets.creative.artifact("storyboard").scenes:
                    if engine.stop_requested():
                        break
                    service.generate_visual(scene.scene_id, engine.load().options)
            elif action.stage in ("scene_images", "voices", "subtitles", "consistency"):
                engine.assets.generate(action.stage)
            elif action.stage == "effects":
                production, board = engine._input()
                render = engine.inspect()
                finishing = engine.finishing.load()
                tasks = engine.finishing.tasks(production, board, render)
                keys = [key for key, (_, _, data) in tasks.items()
                        if not key.startswith("lipsync:") and not engine.finishing.task_intact(finishing.records.get(key), data)]
                engine.finishing.generate_selected(keys, force=False)
            elif action.stage == "videos":
                engine.generate_scenes()
            if not engine.stop_requested():
                queue.stage_finished()
            atomic_write(path, json.dumps({"stage": action.stage, "status": "stopped" if engine.stop_requested() else "completed"}))
        except Exception as exc:
            atomic_write(path, json.dumps({"stage": action.stage, "status": "failed", "error": str(exc)}))
            raise
        if not pipeline or engine.stop_requested():
            break
        next_action, _ = animation_snapshot(engine)
        if next_action.stage == action.stage:
            raise RuntimeError("Generation did not advance. Check stage errors and settings before retrying.")
    return animation_snapshot(engine)[0]
