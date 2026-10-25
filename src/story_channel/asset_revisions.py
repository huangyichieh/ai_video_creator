"""Select immutable saved reference images without regenerating or approving."""
import logging

from .asset_models import AssetRecord
from .files import atomic_write, read_text
from .locking import project_lock
from .models import utc_now

STAGES = ("character_references", "backgrounds")


def history_path(engine, stage, subject):
    if stage not in STAGES:
        raise ValueError("Revision selection supports character references and backgrounds")
    from .models import Identifier
    from pydantic import TypeAdapter
    TypeAdapter(Identifier).validate_python(subject)
    return engine.local_path(f"history/assets/{stage}/{subject}")


def revisions(engine, stage, subject):
    folder = history_path(engine, stage, subject)
    records = {}
    for path in folder.glob("*.json"):
        try:
            record = AssetRecord.model_validate_json(read_text(path))
            if record.stage == stage and record.subject == subject and record.asset:
                records[record.asset.asset_id] = record
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("Could not read asset revision metadata: %s", path.name)
    current = engine.load().records.get(f"{stage}:{subject}")
    if current and current.asset:
        records.setdefault(current.asset.asset_id, current)
    return sorted(records.values(), key=lambda r: (r.revision, r.updated_at), reverse=True)


def apply_revision(engine, stage, subject, asset_id):
    history_path(engine, stage, subject)
    engine.creative.inspect()
    with project_lock(engine.path):
        state, tasks = engine._refresh()
        task = next((t for t in tasks if t.stage == stage and t.subject == subject), None)
        if task is None:
            raise ValueError("This reference is not used by the current project")
        selected = next((r for r in revisions(engine, stage, subject) if r.asset.asset_id == asset_id), None)
        if selected is None or not engine._intact(selected):
            raise ValueError("The selected revision is missing or modified; choose an intact saved revision")
        key = task.key
        current = state.records.get(key)
        # Archive legacy current records too, before replacing their selection.
        if current and current.asset:
            archive = history_path(engine, stage, subject) / f"{current.asset.asset_id}.json"
            if not archive.exists():
                atomic_write(archive, current.model_dump_json(by_alias=True, indent=2))
        record = selected.model_copy(deep=True)
        record.applied_input_hash = engine._hash(task, state)
        record.status, record.error, record.stale_reason = "succeeded", None, ""
        record.updated_at = utc_now()
        record.remote_job_id, record.remote_job_hash, record.remote_status = None, "", ""
        record.attempts = current.attempts if current else selected.attempts
        state.records[key] = record
        state.consistency_report = {}
        manifest = engine.manager.load(engine.project_id)
        gates = ("characters", "backgrounds", "assets") if stage == "character_references" else ("backgrounds", "assets")
        for gate in gates:
            manifest.asset_approvals.pop(gate, None)
        engine.manager.save(manifest)
        state.next_stage = engine._next(state, tasks, manifest)
        engine._save(state)
        # Recheck dependent scene-image fingerprints and revoke downstream reviews.
        return engine._refresh()[0]
