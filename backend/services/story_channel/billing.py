"""Read-only OpenAI billing reports, separate from local generation estimates."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os

from openai import APIConnectionError, APIStatusError, OpenAI


def set_billing_project(manager, story_id, billing_project_id):
    from .locking import project_lock
    value = billing_project_id.strip() or None
    with project_lock(manager.project_path(story_id)):
        project = manager.load(story_id)
        project.billing_project_id = value
        manager.save(project)
    return project


def admin_key():
    value = os.environ.get("OPENAI_ADMIN_KEY", "").strip()
    return "" if value.startswith("YOUR_") else value


@dataclass(frozen=True)
class BillingReport:
    start_time: int
    end_time: int
    fetched_at: datetime
    project_id: str | None
    total: Decimal
    rows: list[dict]


def fetch_billing(start_time, end_time, *, key=None, project_id=None, organization=None, client=None):
    """Fetch every page before publishing a total; never store or return keys."""
    key = admin_key() if key is None else key
    if not key.strip():
        raise ValueError("Set OPENAI_ADMIN_KEY in .env to fetch billed costs.")
    if not 0 <= start_time < end_time:
        raise ValueError("Choose a valid billing period.")
    own_client = client is None
    if own_client:
        client = OpenAI(api_key="unused-for-admin-endpoints", admin_api_key=key,
                        base_url="https://api.openai.com/v1", organization=organization,
                        timeout=20, max_retries=0)
        # Project scoping belongs in the Costs API filter, not generation headers.
        client.project = None
    from .request_tracking import track_client
    track_client(client)
    params = dict(start_time=start_time, end_time=end_time, bucket_width="1d", limit=180,
                  group_by=["project_id", "line_item"])
    if project_id:
        params["project_ids"] = [project_id]
    rows, total, seen = [], Decimal(0), set()
    try:
        while True:
            raw = client.admin.organization.usage.with_raw_response.costs(**params)
            data = json.loads(raw.text, parse_float=Decimal)
            if not isinstance(data.get("data"), list) or not isinstance(data.get("has_more"), bool):
                raise ValueError("OpenAI returned an incomplete billing report. Try refreshing later.")
            for bucket in data["data"]:
                for result in bucket["results"]:
                    if project_id and result.get("project_id") != project_id:
                        raise ValueError("OpenAI returned costs outside the selected project; no total was saved.")
                    amount = result.get("amount") or {}
                    value = Decimal(str(amount.get("value")))
                    if amount.get("currency", "").lower() != "usd" or not value.is_finite():
                        raise ValueError("OpenAI returned an unsupported or incomplete billing amount.")
                    total += value
                    rows.append({"date": datetime.fromtimestamp(bucket["start_time"], timezone.utc).date().isoformat(),
                                 "project_id": result.get("project_id"), "line_item": result.get("line_item"),
                                 "amount": value})
            if not data["has_more"]:
                break
            cursor = data.get("next_page")
            if not cursor or cursor in seen or len(seen) >= 366:
                raise ValueError("OpenAI returned invalid billing pagination; no partial total was saved.")
            seen.add(cursor)
            params["page"] = cursor
        return BillingReport(start_time, end_time, datetime.now(timezone.utc), project_id, total, rows)
    except APIStatusError as exc:
        if exc.status_code in (401, 403):
            raise RuntimeError("Billing access denied. Check OPENAI_ADMIN_KEY and its organization permissions.") from None
        raise RuntimeError(f"OpenAI billing request failed (HTTP {exc.status_code}). Try refreshing later.") from None
    except APIConnectionError:
        raise RuntimeError("OpenAI billing could not connect or timed out. Try refreshing later.") from None
    except (KeyError, TypeError, ArithmeticError, json.JSONDecodeError):
        raise ValueError("OpenAI returned an incomplete billing report. Try refreshing later.") from None
    finally:
        if own_client:
            client.close()
