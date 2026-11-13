"""Bounded, versioned disk caches. Callers always receive independent data."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json

from .files import read_text


def version(path):
    stat = Path(path).stat()
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino


@lru_cache(maxsize=128)
def _model(path, stamp, model):
    return model.model_validate_json(read_text(Path(path)))


def read_model(path, model):
    path = Path(path).resolve()
    return _model(str(path), version(path), model).model_copy(deep=True)


@lru_cache(maxsize=128)
def _json(path, stamp):
    return json.loads(read_text(Path(path)))


def read_json(path):
    path = Path(path).resolve()
    return deepcopy(_json(str(path), version(path)))


@lru_cache(maxsize=128)
def _validated(path, stamp, adapter):
    return adapter.validate_json(read_text(Path(path)))


def read_validated(path, adapter):
    """Versioned validation for typed lists, with isolated mutable results."""
    path = Path(path).resolve()
    return deepcopy(_validated(str(path), version(path), adapter))
