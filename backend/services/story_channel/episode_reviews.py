"""Episode content review reused by HTTP and compatibility UI."""
from .studio import WorldStudio, generate_advice
from .files import atomic_write, read_text, input_hash

def episode_review_context(pipeline, stage):
    manifest = pipeline.manager.load(pipeline.project_id)
    world = WorldStudio(pipeline.manager, manifest.world_project_id or pipeline.project_id)
    return {"canon": manifest.canon_snapshot or world.canon(), "stage": stage,
            "title": manifest.title, "targetDurationSec": manifest.target_duration_sec}

def review_episode(pipeline, stage, instructions=""):
    from .locking import project_lock
    with project_lock(pipeline.path):
        text = pipeline.manager.read_outline(pipeline.project_id) if stage == "outline" else pipeline.artifact(stage).model_dump_json(by_alias=True, indent=2)
        report = generate_advice(pipeline, text, episode_review_context(pipeline, stage), instructions=instructions,
            stage="episode_" + stage + "_review")
        file = pipeline.path / f"episode_{stage}_review.json"
        if file.exists():
            old = read_text(file)
            atomic_write(pipeline.path / "history" / "episode_reviews" / stage / f"{input_hash(old)}.json", old)
        atomic_write(file, report.model_dump_json(by_alias=True, indent=2))
        return report
