from .dialog_ui import dialog
"""Failure report dialogs shared by all production pages."""
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime

import streamlit as st

from .error_reports import matching_reports, redact
from .files import input_hash

_DIALOG_KEYS = {"active-stage-browser", "modify-target", "voice-design-target", "studio-modify-target",
    "outline-dialog-project", "remove-character-target", "delete-project-target", "api-key-dialog-open",
    "generation-preferences-open", "background-revisions-target", "background-enhance-target", "location-pair-revisions-target"}


def error_project(context):
    for name in ("engine", "pipeline", "director", "service", "studio"):
        path = getattr(context.get(name), "path", None)
        if path is not None:
            return str(path)
    manager, pid = context.get("manager"), context.get("project_id")
    if manager is not None and isinstance(pid, str):
        return str(manager.project_path(pid))
    return st.session_state.get("error-report-context-path")


def report_widget_key(namespace, context):
    values = {name: context[name] for name in ("cid", "sid", "subject", "stage", "key", "pid", "project_id", "step",
        "index", "i", "choice", "conflict", "filename", "label", "emotion", "name")
        if isinstance(context.get(name), (str, int))}
    record = context.get("record") or context.get("sample")
    if record is not None:
        for name in ("subject", "scene_id", "shot_id", "revision", "key"):
            value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
            if isinstance(value, (str, int)):
                values["record_" + name] = value
    return "error-report-" + input_hash([namespace, values])[:24]


def close_report():
    target = st.session_state.pop("error-report-target", None)
    if target and target.get("context_project") == st.session_state.get("generation-context-project"):
        st.session_state.update(target.get("return_dialogs", {}))


def render_error(message, *, project_path=None, task_key=None, key=None, **kwargs):
    clean = redact(str(message))
    error, action = st.columns([7, 1.5], vertical_alignment="center")
    error.error(clean, **kwargs)
    def open_report():
        suspended = {}
        for name in list(st.session_state):
            if name in _DIALOG_KEYS or name.startswith("background-removal-target-"):
                suspended[name] = st.session_state.pop(name)
        st.session_state["error-report-target"] = {"path": str(project_path) if project_path else None,
            "task": task_key, "message": clean, "return_dialogs": suspended,
            "context_project": st.session_state.get("generation-context-project")}
        from .dialog_ui import request_open
        request_open()
    action.button("Error Report", key=key or "error-report-" + input_hash([project_path, task_key, clean])[:24], on_click=open_report)


@dialog("Error Report", width="large", on_dismiss=close_report)
def report_dialog(target):
    st.error(target["message"])
    try:
        entries = matching_reports(Path(target["path"]), target["message"], target.get("task")) if target.get("path") else []
    except (OSError, ValueError):
        entries = []
        st.warning("The saved reports could not be read.")
    if not entries:
        st.info("No saved response is available. The task may have succeeded since this error, or this error occurred before a provider response was received.")
    for index, entry in enumerate(entries):
        date = datetime.fromisoformat(entry["created_at"]).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")
        with st.expander(f"{entry['task']} · {date} Taipei", expanded=index == 0):
            st.code(entry["error"]["type"] + ": " + entry["error"]["message"], language=None, wrap_lines=True)
            if not entry["responses"]:
                st.caption("No provider response was received or captured for this failure.")
            for response in entry["responses"]:
                status = response.get("status_code")
                st.caption(f"{response.get('method', '')} {response.get('endpoint', '')} · " + (f"HTTP {status}" if status else "No response"))
                if response.get("request_id"):
                    st.caption("Request ID: " + response["request_id"])
                body = response.get("body")
                if isinstance(body, (dict, list)):
                    st.json(body)
                elif body is not None:
                    st.code(str(body), language=None, wrap_lines=True)
    st.caption("Reports are stored locally and removed automatically when the corresponding task succeeds. Credentials are redacted.")
    if st.button("Close", key="close-error-report"):
        close_report()
        st.rerun(scope="app")


def show_error_report():
    target = st.session_state.get("error-report-target")
    if target:
        report_dialog(target)
