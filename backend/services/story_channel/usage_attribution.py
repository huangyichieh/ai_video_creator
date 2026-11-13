"""Explicit, backed-up reassignment of historical API usage to a local story."""
from contextlib import ExitStack
from datetime import datetime, timezone
import json
from uuid import uuid4

from pydantic import TypeAdapter

from .costs import CostTracker
from .files import atomic_copy, atomic_write
from .locking import project_lock
from .models import UsageRecord
from .request_tracking import _append


def assign_historical_requests(manager, target_id, *, cutoff=None):
    """Move historical OpenAI ledger rows, retaining original project provenance."""
    manager.load(target_id)
    cutoff = cutoff or datetime.now(timezone.utc)
    projects = sorted(manager.list_projects(), key=lambda project: project.project_id)
    adapter = TypeAdapter(list[UsageRecord])
    migration_id = uuid4().hex
    with ExitStack() as stack:
        for project in projects:
            stack.enter_context(project_lock(manager.project_path(project.project_id)))
        ledgers = {p.project_id: CostTracker(manager, p.project_id).records() for p in projects}
        target = {record.record_id: record for record in ledgers[target_id]}
        assigned, changed = [], set()
        for project in projects:
            kept = []
            for original in ledgers[project.project_id]:
                if original.provider != "openai" or original.created_at > cutoff:
                    kept.append(original)
                    continue
                record = original.model_copy(deep=True)
                record.source_project_id = record.source_project_id or project.project_id
                record.story_project_id = target_id
                record.attribution = "legacy_assigned"
                existing = target.get(record.record_id)
                if project.project_id != target_id and existing is not None and existing != record:
                    raise ValueError("Conflicting historical usage record IDs; no migration was written")
                target[record.record_id] = record
                if record != original or project.project_id != target_id:
                    assigned.append(record)
                    changed.update((project.project_id, target_id))
            if project.project_id != target_id:
                ledgers[project.project_id] = kept
        if not changed:
            return {"assigned": 0, "target_story_id": target_id}
        ledgers[target_id] = sorted(target.values(), key=lambda record: record.created_at)
        # Back up every affected ledger and manifest before making any changes.
        for story_id in sorted(changed):
            path = manager.project_path(story_id)
            backup = path / "history" / "usage_attribution" / migration_id
            for name in ("usage.json", "manifest.json", "requests.jsonl"):
                if (path / name).exists():
                    atomic_copy(path / name, backup / name)
        for story_id in sorted(changed):
            path = manager.project_path(story_id)
            atomic_write(path / "usage.json", adapter.dump_json(ledgers[story_id], by_alias=True, indent=2).decode() + "\n")
            CostTracker(manager, story_id).sync()
        for record in assigned:
            _append(manager.project_path(target_id) / "requests.jsonl", {
                "event": "legacy_assigned", "story_project_id": target_id,
                "source_project_id": record.source_project_id, "usage_record_id": record.record_id,
                "step": record.step, "created_at": record.created_at.isoformat(),
                "request_id": None, "client_request_id": None,
                "note": "Assigned by user instruction; original request IDs were not recorded.",
                "migration_id": migration_id})
        report = {"assigned": len(assigned), "target_story_id": target_id,
                  "source_story_ids": sorted(changed), "cutoff": cutoff.isoformat(), "migration_id": migration_id}
        atomic_write(manager.project_path(target_id) / "history" / "usage_attribution" / migration_id / "report.json",
                     json.dumps(report, indent=2) + "\n")
        return report
