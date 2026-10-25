import logging
from pathlib import Path

from .config import Settings
from .files import atomic_write, read_text
from .models import Manifest, utc_now

CHANNEL_DIRS = ("00_brand", "01_world", "02_characters", "03_stories",
                "04_shorts", "05_templates", "06_analytics")
PROJECT_DIRS = ("characters", "prompts", "images", "videos", "audio",
                "subtitles", "thumbnail", "output", "logs")
logger = logging.getLogger(__name__)


class ProjectManager:
    """Local, single-writer project persistence; manifest is the status source."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_root.resolve()

    def project_path(self, project_id: str) -> Path:
        # Validate identifiers before using them as filesystem components.
        from pydantic import TypeAdapter
        from .models import Identifier
        TypeAdapter(Identifier).validate_python(project_id)
        path = (self.root / "03_stories" / project_id).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Project path escapes the channel root")
        return path

    def create(self, title: str, outline_path: Path, *, language: str | None = None,
               target_duration_sec: int | None = None, project_id: str | None = None) -> Manifest:
        outline = outline_path.read_text(encoding="utf-8-sig")
        if not outline.strip():
            raise ValueError("Story outline must not be empty")
        fields = {"project_id": project_id} if project_id is not None else {}
        manifest = Manifest(title=title,
                            language=language if language is not None else self.settings.default_language,
                            target_duration_sec=(target_duration_sec if target_duration_sec is not None
                                                 else self.settings.default_target_duration_sec), **fields)
        path = self.project_path(manifest.project_id)
        for directory in CHANNEL_DIRS:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        path.mkdir()  # Never overwrite an existing project.
        try:
            for directory in PROJECT_DIRS:
                (path / directory).mkdir()
            atomic_write(path / "outline.md", outline)
            self.save(manifest)
        except Exception:
            # Only remove the newly created project, never an existing directory.
            import shutil
            shutil.rmtree(path)
            raise
        logger.info("Created project %s", manifest.project_id)
        return manifest

    def load(self, project_id: str) -> Manifest:
        manifest = Manifest.model_validate_json(
            read_text(self.project_path(project_id) / "manifest.json"))
        if manifest.project_id != project_id:
            raise ValueError("Manifest projectId does not match its directory")
        return manifest

    def save(self, manifest: Manifest) -> None:
        # Revalidate in case mutable lists/dictionaries were modified in place.
        checked = Manifest.model_validate(manifest.model_dump())
        path = self.project_path(checked.project_id)
        if not path.is_dir():
            raise FileNotFoundError(path)
        checked.updated_at = utc_now()
        atomic_write(path / "manifest.json", checked.model_dump_json(by_alias=True, indent=2) + "\n")
        manifest.updated_at = checked.updated_at

    def read_outline(self, project_id: str) -> str:
        return (self.project_path(project_id) / "outline.md").read_text(encoding="utf-8-sig")

    def list_projects(self) -> list[Manifest]:
        directory = self.root / "03_stories"
        if not directory.exists():
            return []
        projects = []
        for path in sorted(directory.iterdir()):
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            try:
                projects.append(self.load(path.name))
            except (OSError, ValueError) as exc:
                logger.warning("Cannot load project %s: %s", path.name, exc)
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)
