"""Provider contracts and local storage. The OpenAI LLM adapter lives in llm.py."""
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from .models import AudioAsset, GeneratedAsset, ProviderConfig
from .asset_models import Alignment

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    def generate(self, prompt: str, response_model: type[T], config: ProviderConfig) -> T: ...


@runtime_checkable
class ImageProvider(Protocol):
    def generate(self, prompt: str, references: list[Path], config: ProviderConfig) -> GeneratedAsset: ...


@runtime_checkable
class VideoProvider(Protocol):
    def generate_clip(self, prompt: str, references: list[Path], config: ProviderConfig) -> GeneratedAsset: ...


@runtime_checkable
class PerformanceProvider(Protocol):
    """Selected driving-performance transfer, separate from corrective lip-sync."""
    def generate_clip(self, prompt: str, references: list[Path], config: ProviderConfig) -> GeneratedAsset: ...


@runtime_checkable
class TTSProvider(Protocol):
    def generate(self, text: str, voice_id: str, config: ProviderConfig) -> AudioAsset: ...


@runtime_checkable
class AlignmentProvider(Protocol):
    def align(self, audio: Path, text: str, language: str, config: ProviderConfig) -> Alignment: ...


@runtime_checkable
class LipSyncProvider(Protocol):
    def generate(self, video: Path, audio: Path, config: ProviderConfig) -> GeneratedAsset: ...


@runtime_checkable
class SFXProvider(Protocol):
    def generate(self, prompt: str, config: ProviderConfig) -> GeneratedAsset: ...


@runtime_checkable
class StorageProvider(Protocol):
    def read_text(self, key: str) -> str: ...
    def write_text(self, key: str, content: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Storage key must point to a file inside the storage root")
        return path

    def read_text(self, key: str) -> str:
        return self._path(key).read_text(encoding="utf-8-sig")

    def write_text(self, key: str, content: str) -> None:
        from .files import atomic_write
        atomic_write(self._path(key), content)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
