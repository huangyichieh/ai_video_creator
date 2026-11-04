"""Task-scoped failure responses, removed after that task succeeds."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from .files import atomic_write, input_hash, read_text

_lock = RLock()


def redact(value, secrets=()):
    sensitive = list(secrets) + [v for k, v in os.environ.items()
        if ("API_KEY" in k or "API_SECRET" in k or k == "OPENAI_ADMIN_KEY") and len(v) >= 8]
    if isinstance(value, dict):
        return {key: "[redacted]" if re.sub(r"[^a-z]", "", str(key).lower()) in {
            "authorization", "apikey", "accesskey", "secretkey", "accesstoken", "refreshtoken", "token",
            "password", "secret", "cookie", "setcookie", "policy", "xamzsignature", "xamzcredential"
        } else redact(item, sensitive) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, sensitive) for item in value]
    if not isinstance(value, str):
        return value
    for secret in sensitive:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)Bearer\s+[^\s\"<>]+", "Bearer [redacted]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", value)
    return re.sub(r"(?i)([?&](?:token|key|api_key|signature|sig|x-amz-signature|x-amz-credential)=)[^&\s\"]+", r"\1[redacted]", value)


def _file(project_path, task):
    return Path(project_path) / "error_reports" / (input_hash(task) + ".json")


def reports(project_path, task=None):
    folder = Path(project_path) / "error_reports"
    found = []
    with _lock:
        for file in folder.glob("*.json"):
            data = json.loads(read_text(file))
            if task is None or data["task"] == task or data["task"].startswith(task + ":"):
                found.extend(data["reports"])
    return sorted(found, key=lambda item: item["created_at"], reverse=True)


def record_failure(project_path, task, error, responses=(), *, secrets=()):
    from .locking import deletion_marker
    if not Path(project_path).is_dir() or deletion_marker(Path(project_path)).exists():
        raise FileNotFoundError("Cannot save an error report for a deleted project")
    file = _file(project_path, task)
    entry = redact({"id": uuid4().hex, "task": task, "created_at": datetime.now(timezone.utc).isoformat(),
        "error": {"type": type(error).__name__, "message": str(error)}, "responses": list(responses)}, secrets)
    with _lock:
        data = json.loads(read_text(file)) if file.exists() else {"task": task, "reports": []}
        data["reports"].append(entry)
        atomic_write(file, json.dumps(data, ensure_ascii=False, indent=2))
    return entry


def clear_reports(project_path, task):
    with _lock:
        folder = Path(project_path) / "error_reports"
        for file in folder.glob("*.json"):
            data = json.loads(read_text(file))
            if data["task"] == task or data["task"].startswith(task + ":"):
                file.unlink(missing_ok=True)


def matching_reports(project_path, message, task=None):
    found = reports(project_path, task)
    if task and found:
        return found
    all_reports = reports(project_path)
    clean = redact(str(message))
    matched = [entry for entry in all_reports if entry["error"]["message"] in clean or clean in entry["error"]["message"]]
    return matched
