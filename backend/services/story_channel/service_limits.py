"""Process-wide limits for complete provider jobs, including remote polling."""
from contextlib import contextmanager
from functools import wraps
from threading import BoundedSemaphore, local, RLock
from contextvars import ContextVar

LIMITS = {"openai": 2, "runway": 1, "elevenlabs": 2}
_slots = {name: BoundedSemaphore(limit) for name, limit in LIMITS.items()}
_held = local()
_task = ContextVar("generation_task", default=None)
_activity_lock = RLock()
_active = dict.fromkeys(LIMITS, 0)


@contextmanager
def task_context(task):
    token = _task.set(task)
    try:
        yield
    finally:
        _task.reset(token)


def activity():
    with _activity_lock:
        return {name: {"active": _active[name], "limit": limit} for name, limit in LIMITS.items()}


@contextmanager
def service_slot(service):
    held = getattr(_held, "services", set())
    if service not in _slots or service in held:
        yield
        return
    task = _task.get()
    if task is not None:
        task.update(phase="waiting", service=service)
    with _slots[service]:
        if task is not None and task.get("stop_event") is not None and task["stop_event"].is_set():
            task["phase"] = "stopped"
            raise InterruptedError("Stopped before sending the next provider request.")
        with _activity_lock:
            _active[service] += 1
        if task is not None:
            task.update(phase="running", service=service)
        _held.services = held | {service}
        try:
            yield
        finally:
            _held.services = held
            with _activity_lock:
                _active[service] -= 1
            if task is not None:
                task.update(phase="saving", service=service)


def limited(service):
    def decorate(callback):
        @wraps(callback)
        def run(*args, **kwargs):
            if args and hasattr(type(args[0]), "last_usage"):
                args[0].last_usage = {}
            with service_slot(service):
                return callback(*args, **kwargs)
        return run
    return decorate
