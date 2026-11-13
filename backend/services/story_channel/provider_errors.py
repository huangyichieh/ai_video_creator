"""Useful provider errors without dumping request bodies or credentials."""
import os
import re


def safe_error_text(value):
    text = str(value)
    for name, secret in os.environ.items():
        if ("API_KEY" in name or "API_SECRET" in name or name == "OPENAI_ADMIN_KEY") and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
    return " ".join(text.split())[:1200]


def response_error(response):
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    for name in ("error", "message", "detail"):
        value = data.get(name)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for field in ("status", "code", "message"):
                if isinstance(value.get(field), str):
                    parts.append(value[field])
    issues = data.get("issues", data.get("details", []))
    if isinstance(issues, list):
        for issue in issues[:5]:
            if isinstance(issue, dict) and isinstance(issue.get("message"), str):
                path = issue.get("path", [])
                field = ".".join(str(part) for part in path) if isinstance(path, list) else ""
                parts.append((field + ": " if field else "") + issue["message"])
    return safe_error_text("; ".join(dict.fromkeys(parts)))
