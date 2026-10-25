import hashlib
import json
import os
import tempfile
import time
import shutil
import errno
from pathlib import Path
from typing import Any


def input_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_access(operation):
    """Bounded retries for Windows access/sharing/lock conflicts.

    CPython sometimes exposes these as errno 13 without a winerror value.
    Persistent denial is still raised; never replace it with empty file data.
    """
    for attempt in range(8):
        try:
            return operation()
        except PermissionError as exc:
            code = getattr(exc, "winerror", None)
            retryable = os.name == "nt" and (code in (5, 32, 33) or code is None and exc.errno in (errno.EACCES, errno.EPERM))
            if not retryable or attempt == 7:
                raise
            time.sleep(min(.01 * 2 ** attempt, .25))


def read_text(path: Path, *, encoding="utf-8-sig") -> str:
    return _file_access(lambda: path.read_text(encoding=encoding))


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows briefly denies replacement while another thread reads the file.
        _file_access(lambda: os.replace(name, path))
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def atomic_copy(source: Path, destination: Path) -> None:
    """Stream a large binary into place without exposing a partial download."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        _file_access(lambda: os.replace(temporary, destination))
    finally:
        temporary.unlink(missing_ok=True)
