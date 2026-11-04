"""Tracked expandable panels with stable defaults and navigation persistence."""
import streamlit as st
from functools import wraps


def asset_title(label,exists):
    return ("🟢 " if exists else "🔴 ")+label


def polling_fragment(callback):
    """Keep live jobs responsive; poll saved external changes less often when idle."""
    @wraps(callback)
    def render(*args, **kwargs):
        from .ui_tasks import workspace_tasks
        tasks = workspace_tasks() + list(st.session_state.get("workspace-task-queue", []))
        interval = 1 if any(not task["future"].done() for task in tasks) else 5
        return st.fragment(run_every=interval)(callback)(*args, **kwargs)
    return render


def persistent_expander(label, *, key, expanded=False):
    panels = st.session_state.setdefault("ui-panel-state", {})
    entry = panels.setdefault(key, {"initial": bool(expanded), "open": bool(expanded), "label": label})
    # Status and revision labels participate in Streamlit's widget identity.
    # Carry the user's choice across those remounts, rather than resetting it.
    if entry["label"] != label:
        st.session_state[key] = st.session_state.get(key, entry["open"])
    panel = st.expander(label, key=key, expanded=entry["initial"], on_change="rerun")
    entry.update(label=label, open=panel.open)
    return panel


def preserve_panels():
    for key in st.session_state.get("ui-panel-state", {}):
        if key in st.session_state:
            st.session_state[key] = st.session_state[key]
