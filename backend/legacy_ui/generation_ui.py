"""Consistent, task-owned progress in generation button labels."""
from contextlib import nullcontext

import streamlit as st


def generation_phase(key):
    if not key:
        return None
    from .ui_tasks import workspace_tasks
    for task in workspace_tasks() + list(st.session_state.get("workspace-task-queue", [])):
        if (task.get("generation_button_key") == key and not task["future"].done()
                and task.get("generation_button_project") == st.session_state.get("generation-context-project")):
            phase = task.get("phase", "running")
            return "queued" if phase in ("queued", "waiting") else phase
    return None


def generation_active(key):
    return generation_phase(key) is not None


def generation_queued(key):
    return generation_phase(key) == "queued"


def generation_button(label, *, key=None, container=None, activity=None, active_label="Generating…", **kwargs):
    """Preserve native button behavior and associate launches with their control."""
    generating = active_label == "Reviewing..." or any(word in label.casefold() for word in ("generat", "retry", "resume", "compose", "queue", "check reference"))
    callback = kwargs.get("on_click")
    if generating and key and callable(callback):
        def start(*args, **callback_kwargs):
            st.session_state["generation-button-trigger"] = key
            return callback(*args, **callback_kwargs)
        kwargs["on_click"] = start
    with container if container is not None else nullcontext():
        phase = generation_phase(key) if activity is None else activity
        if phase in ("queued", "waiting", "running", "saving"):
            kwargs["disabled"] = True
        displayed = ("Queuing…" if phase in ("queued", "waiting") else "Saving…" if phase == "saving"
                     else active_label if phase == "running" else label)
        clicked = st.button(displayed, key=key, **kwargs)
        if generating and key:
            if clicked:
                st.session_state["generation-button-trigger"] = key
        return clicked


def review_button(label="AI Review",**kwargs):
    return generation_button(label,active_label="Reviewing...",**kwargs)
