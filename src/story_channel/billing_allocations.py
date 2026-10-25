"""User-declared assignment of fetched billed totals to Story Studio projects."""
from decimal import Decimal
from uuid import uuid4

from pydantic import Field, TypeAdapter

from .files import atomic_copy, atomic_write
from .locking import project_lock
from .models import Model, Identifier, utc_now


class BillingAllocation(Model):
    allocation_id: str = Field(default_factory=lambda: uuid4().hex)
    story_project_id: Identifier
    organization: str = "default"
    openai_project_id: str | None = None
    start_time: int
    end_time: int
    amount: Decimal = Field(allow_inf_nan=False)
    fetched_at: str
    assigned_at: str = Field(default_factory=lambda: utc_now().isoformat())
    source: str = "OpenAI Costs API"
    attribution: str = "Assigned by explicit user instruction; aggregate billing snapshot"
    line_items: list[dict] = Field(default_factory=list)


def allocations(manager):
    path = manager.root / "06_analytics" / "billing_allocations.json"
    return TypeAdapter(list[BillingAllocation]).validate_json(path.read_text(encoding="utf-8")) if path.exists() else []


def assigned_billing(manager, story_id):
    records = [record for record in allocations(manager) if record.story_project_id == story_id]
    return sum((record.amount for record in records), Decimal(0)), records


def assign_report(manager, story_id, report, *, organization=None):
    manager.load(story_id)
    allocation = BillingAllocation(story_project_id=story_id, organization=organization or "default",
        openai_project_id=report.project_id, start_time=report.start_time, end_time=report.end_time,
        amount=report.total, fetched_at=report.fetched_at.isoformat(),
        line_items=[dict(row, amount=str(row["amount"])) for row in report.rows])
    folder = manager.root / "06_analytics"
    folder.mkdir(parents=True, exist_ok=True)
    with project_lock(folder):
        records = allocations(manager)
        # The same reported charges must not be counted for two stories or twice
        # when a refreshed, overlapping snapshot is explicitly assigned again.
        kept = [old for old in records if not (
            old.organization == allocation.organization and old.openai_project_id == allocation.openai_project_id
            and old.start_time < allocation.end_time and allocation.start_time < old.end_time)]
        if len(kept) != len(records) and any(old.start_time != allocation.start_time for old in records if old not in kept):
            raise ValueError("This report overlaps a differently started allocation period; choose the same start date to avoid losing charges")
        path = folder / "billing_allocations.json"
        history = folder / "billing_allocation_history" / allocation.allocation_id
        if path.exists():
            atomic_copy(path, history / "previous_allocations.json")
        atomic_write(history / "assigned_report.json", allocation.model_dump_json(by_alias=True, indent=2))
        kept.append(allocation)
        atomic_write(path, TypeAdapter(list[BillingAllocation]).dump_json(kept, by_alias=True, indent=2).decode() + "\n")
    return allocation
