"""Durable request IDs and separate sanitized, task-scoped failure reports."""
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import sys
from threading import RLock
from uuid import uuid4


_active = ContextVar("story_request_tracker", default=None)
_lock = RLock()
logger = logging.getLogger(__name__)


def _append(path, item):
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def _request(request):
    tracker = _active.get()
    if tracker is None:
        return
    client_id = f"story_{tracker['story_project_id']}_{uuid4().hex}"
    request.headers["X-Client-Request-Id"] = client_id
    row = {"story_project_id": tracker["story_project_id"], "step": tracker["step"],
           "client_request_id": client_id, "request_id": None,
           "created_at": datetime.now(timezone.utc).isoformat(),
           "method": request.method, "endpoint": request.url.path, "event": "sent"}
    tracker["requests"][client_id] = row
    for name in ("authorization", "xi-api-key", "x-api-key"):
        value = request.headers.get(name, "")
        if value:
            tracker["redactions"].add(value.split(None, 1)[-1] if name == "authorization" else value)
    _append(tracker["path"], row)


def _response(response):
    tracker = _active.get()
    if tracker is None:
        return
    client_id = response.request.headers.get("X-Client-Request-Id")
    original = tracker["requests"].get(client_id)
    if original is None:
        return
    row = dict(original, event="response", request_id=response.headers.get("x-request-id"),
               status_code=response.status_code)
    if row["request_id"] is None:
        row["request_id"] = response.headers.get("request-id") or response.headers.get("x-sync-request-id")
    tracker["requests"][client_id] = row
    _append(tracker["path"], row)
    content_type = response.headers.get("content-type", "")
    if response.status_code >= 400 or "json" in content_type or "text/" in content_type:
        response.read()
        try:
            body = response.json()
        except ValueError:
            body = response.text
        tracker["responses"].append({**row, "provider": response.request.url.host,
            "content_type": content_type, "body": body})


def track_client(client):
    """Install HTTP hooks once; ContextVar keeps parallel asset calls isolated."""
    transport = client if hasattr(client, "event_hooks") else getattr(client, "_client", None)
    if transport is None or not hasattr(transport, "event_hooks"):
        return
    with _lock:
        for event, callback in (("request", _request), ("response", _response)):
            if callback not in transport.event_hooks[event]:
                transport.event_hooks[event].append(callback)


def tracked_call(manager, story_id, step, callback, *args, task_key=None, validate_result=None, **kwargs):
    tracker = {"story_project_id": story_id, "step": step, "requests": {},
               "path": manager.project_path(story_id) / "requests.jsonl", "responses": [], "redactions": set()}
    project_path = manager.project_path(story_id)
    report_task = task_key or step
    token = _active.set(tracker)
    try:
        result = callback(*args, **kwargs)
        if validate_result is not None:
            result = validate_result(result)
    except Exception as exc:
        from .error_reports import record_failure
        failed = [row for row in tracker["responses"] if row["status_code"] >= 400 or
                  isinstance(row["body"], dict) and str(row["body"].get("status", "")).upper() in ("FAILED", "CANCELED", "CANCELLED", "ERROR")]
        if not failed and tracker["responses"] and not any(row["event"] == "sent" for row in tracker["requests"].values()):
            failed = tracker["responses"][-1:]
        failed.extend({**row, "event": "no_response", "status_code": None, "body": None}
                      for row in tracker["requests"].values() if row["event"] == "sent")
        try:
            record_failure(project_path, report_task, exc, failed, secrets=tracker["redactions"])
        except (OSError, ValueError) as storage_error:
            logger.warning("Could not save error report (%s)", type(storage_error).__name__)
        try:
            exc.error_report_project_path = str(project_path)
            exc.error_report_task = report_task
        except (AttributeError, TypeError):
            pass
        raise
    else:
        from .error_reports import clear_reports
        try:
            clear_reports(project_path, report_task)
        except (OSError, ValueError) as storage_error:
            logger.warning("Could not clear error report (%s)", type(storage_error).__name__)
        return result
    finally:
        _active.reset(token)
        error_type = sys.exc_info()[0]
        for client_id, row in list(tracker["requests"].items()):
            if row["event"] == "sent":
                row = dict(row, event="no_response", error_type=error_type.__name__ if error_type else None)
                tracker["requests"][client_id] = row
                _append(tracker["path"], row)
        provider = getattr(callback, "__self__", None)
        if provider is not None:
            usage = dict(getattr(provider, "last_usage", {}) or {})
            usage.update(request_ids=[row["request_id"] for row in tracker["requests"].values() if row["request_id"]],
                         client_request_ids=list(tracker["requests"]))
            provider.last_usage = usage


def request_fields(usage):
    return {name: usage.get(name, []) for name in ("request_ids", "client_request_ids")}


def request_rows(path):
    """Return the latest event per request, including sent-only timeout attempts."""
    if not path.exists():
        return []
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue  # A concurrently appended final line may still be incomplete.
        identity = item.get("client_request_id") or item.get("usage_record_id")
        if identity:
            rows[identity] = item
    return list(rows.values())
