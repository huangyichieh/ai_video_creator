"""Short, reentrant locks shared by ledger and manifest writers."""
from contextlib import contextmanager
from threading import RLock, local

from .locking import project_lock

_guard = RLock()
_locks = {}
_held = local()


@contextmanager
def ledger_lock(path):
    key = str(path.resolve())
    with _guard:
        lock = _locks.setdefault(key, RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        directory = path / ".ledger"
        directory.mkdir(exist_ok=True)
        with project_lock(directory, timeout=30):
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
