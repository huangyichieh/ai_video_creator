"""OS file locks release automatically if a worker exits unexpectedly."""
import os
import time
from contextlib import contextmanager
from pathlib import Path


class ProjectBusyError(RuntimeError):
    """A live worker owns the project's write lock."""


def deletion_marker(path: Path):
    return path.parent.parent / ".deleted_projects" / (path.name + ".json")


@contextmanager
def project_lock(path: Path, *, allow_deleted=False, timeout=0):
    with (path / ".project.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ProjectBusyError("This project is busy in another session. Try again when it finishes.") from exc
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            if not allow_deleted and path.parent.name == "03_stories" and deletion_marker(path).exists():
                raise FileNotFoundError("This project has been deleted.")
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
