from decimal import Decimal

from pydantic import TypeAdapter

from .files import atomic_write
from .models import UsageRecord
from .projects import ProjectManager


class CostTracker:
    """Single-writer ledger; totals can be rebuilt if manifest sync is interrupted."""

    def __init__(self, manager: ProjectManager, project_id: str):
        self.manager = manager
        self.project_id = project_id
        manager.load(project_id)
        self.path = manager.project_path(project_id) / "usage.json"

    def records(self) -> list[UsageRecord]:
        if not self.path.exists():
            return []
        return TypeAdapter(list[UsageRecord]).validate_json(self.path.read_text(encoding="utf-8"))

    def record(self, usage: UsageRecord) -> None:
        usage = UsageRecord.model_validate(usage.model_dump())
        usage.story_project_id = usage.story_project_id or self.project_id
        usage.source_project_id = usage.source_project_id or self.project_id
        if usage.story_project_id != self.project_id:
            raise ValueError("Usage belongs to another Story Studio project")
        records = self.records()
        previous = next((item for item in records if item.record_id == usage.record_id), None)
        if previous is not None and previous != usage:
            raise ValueError("Usage record ID already exists with different data")
        if previous is None:
            records.append(usage)
            payload = TypeAdapter(list[UsageRecord]).dump_json(records, by_alias=True, indent=2)
            atomic_write(self.path, payload.decode("utf-8") + "\n")
        self.sync()

    def sync(self) -> None:
        records = self.records()
        manifest = self.manager.load(self.project_id)
        manifest.estimated_cost = sum((r.estimated_cost for r in records), Decimal("0"))
        manifest.actual_cost = sum((r.cost for r in records), Decimal("0"))
        self.manager.save(manifest)
