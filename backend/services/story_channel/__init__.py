"""Modular story production: project foundation and reviewed creative pipeline."""

# Retain imports used by historical UI regression tests; FastAPI does not serve it.
from pathlib import Path as _Path
__path__.append(str(_Path(__file__).resolve().parents[2] / "legacy_ui"))
