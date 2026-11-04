"""Read-only episode-list labels from the same workflow used by Animations controls."""


def animation_episode_status(manager,project):
    from .pipeline import Pipeline
    from .studio import approved_content
    pipeline = Pipeline(manager,project.project_id)
    if (not all(approved_content(pipeline,name) for name in ("outline","script","prompts"))
            or not {"story","storyboard"} <= project.approvals.keys()):
        return "NOT READY TO ANIMATE"
    from .animations_workflow import animation_snapshot
    from .rendering import RenderPipeline
    from .file_cache import read_json
    try:
        engine = RenderPipeline(manager,project.project_id)
        action,rows = animation_snapshot(engine,refresh=False)
        running = next((row for row in rows if row["Status"].lower() in ("running","generating…","generating...")),None)
        if running:
            return "GENERATING · "+running["Stage"]
        if action.stage == "complete":
            return "ANIMATION COMPLETE"
        queue_path = engine.path/"animation_waiting_queue.json"
        queue = read_json(queue_path) if queue_path.exists() else {}
        if queue.get("status") == "running":
            return "GENERATING · "+queue.get("active_stage",action.title).replace("_"," ").title()
        if queue.get("status") == "stopped":
            return "STOPPED · "+action.title
        if any(row["Status"].lower() == "failed" for row in rows):
            failed = next(row for row in rows if row["Status"].lower() == "failed")
            return "FAILED · "+failed["Stage"]
        if queue.get("status") == "failed":
            return "FAILED · "+action.title
        if queue.get("status") == "queued":
            return "QUEUED · "+action.title
        if action.stage.endswith("review"):
            return "AWAITING APPROVAL · "+action.title
        if action.page == "Assets":
            return "NEEDS ASSETS · "+action.title
        if action.stage == "scene_images":
            images = [row for row in rows if row["Stage"] == "Scene images"]
            done = sum(row["Status"] == "succeeded" for row in images)
            return f"SCENE IMAGES · {done}/{len(images)}" if done else "NOT STARTED · Scene images"
        return "READY · "+action.title
    except (OSError,ValueError,RuntimeError,KeyError):
        return "NEEDS ATTENTION · Animation data unavailable"
