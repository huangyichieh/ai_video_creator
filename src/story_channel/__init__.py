"""Compatibility namespace; implementation now lives under backend/."""
from pathlib import Path
_root=Path(__file__).resolve().parents[2]
__path__=[str(_root/'backend/services/story_channel'),str(_root/'backend/legacy_ui')]
