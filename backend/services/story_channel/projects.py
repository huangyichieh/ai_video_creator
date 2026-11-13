import logging
from pathlib import Path

from .config import Settings
from .files import atomic_write, read_text
from .project_documents import read_outline, write_document
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
        from backend.paths import data_path
        self.root = data_path(settings.data_root)
        from backend.paths import storage_paths
        from backend.services.project_json_migration import migrate_project_markdown
        self.document_migration = migrate_project_markdown(
            self.root, storage_paths().database / "markdown-archive")

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
        from .project_documents import read_document
        outline = (read_document(outline_path) if outline_path.suffix == ".json"
                   else outline_path.read_text(encoding="utf-8-sig"))
        if not outline.strip():
            raise ValueError("Story outline must not be empty")
        fields = {"project_id": project_id} if project_id is not None else {}
        manifest = Manifest(title=title,
                            language=language if language is not None else self.settings.default_language,
                            target_duration_sec=(target_duration_sec if target_duration_sec is not None
                                                 else self.settings.default_target_duration_sec), **fields)
        path = self.project_path(manifest.project_id)
        from .locking import deletion_marker
        if deletion_marker(path).exists():
            raise ValueError("This project ID was deleted. Create a project with a new ID.")
        for directory in CHANNEL_DIRS:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        path.mkdir()  # Never overwrite an existing project.
        try:
            for directory in PROJECT_DIRS:
                (path / directory).mkdir()
            write_document(path / "outline.json", outline)
            self.save(manifest)
        except Exception:
            # Only remove the newly created project, never an existing directory.
            import shutil
            shutil.rmtree(path)
            raise
        logger.info("Created project %s", manifest.project_id)
        return manifest

    def load(self, project_id: str) -> Manifest:
        from .locking import deletion_marker
        if deletion_marker(self.project_path(project_id)).exists():
            raise FileNotFoundError("This project has been deleted.")
        from .file_cache import read_model
        manifest = read_model(self.project_path(project_id) / "manifest.json", Manifest)
        if manifest.project_id != project_id:
            raise ValueError("Manifest projectId does not match its directory")
        return manifest

    def save(self, manifest: Manifest) -> None:
        # Revalidate in case mutable lists/dictionaries were modified in place.
        checked = Manifest.model_validate(manifest.model_dump())
        path = self.project_path(checked.project_id)
        from .locking import deletion_marker
        if deletion_marker(path).exists():
            raise FileNotFoundError("This project has been deleted.")
        if not path.is_dir():
            raise FileNotFoundError(path)
        from .ledger_lock import ledger_lock
        with ledger_lock(path):
            # Generation can hold an older manifest while another service adds
            # usage. Never overwrite newer ledger totals with that snapshot.
            if (path / "usage.json").exists():
                from .costs import CostTracker
                from decimal import Decimal
                records = CostTracker(self, checked.project_id).records()
                checked.estimated_cost = sum((row.estimated_cost for row in records), Decimal(0))
                checked.actual_cost = sum((row.cost for row in records), Decimal(0))
            checked.updated_at = utc_now()
            atomic_write(path / "manifest.json", checked.model_dump_json(by_alias=True, indent=2) + "\n")
            manifest.updated_at = checked.updated_at
            manifest.estimated_cost, manifest.actual_cost = checked.estimated_cost, checked.actual_cost

    def read_outline(self, project_id: str) -> str:
        return read_outline(self.project_path(project_id))

    def list_projects(self) -> list[Manifest]:
        directory = self.root / "03_stories"
        if not directory.exists():
            return []
        projects = []
        for path in sorted(directory.iterdir()):
            from .locking import deletion_marker
            if deletion_marker(path).exists():
                continue
            if not path.is_dir() or not (path / "manifest.json").exists():
                continue
            try:
                projects.append(self.load(path.name))
            except (OSError, ValueError) as exc:
                logger.warning("Cannot load project %s: %s", path.name, exc)
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def delete(self, project_id: str, *, confirmation: str, expected_world_id: str | None = None) -> list[str]:
        """Delete a confirmed World and its episodes, blocking active writers."""
        import json
        import shutil
        from contextlib import ExitStack
        from .files import _file_access
        from .locking import project_lock, deletion_marker
        root_path = self.project_path(project_id)
        marker = deletion_marker(root_path)
        if marker.exists():
            plan = json.loads(read_text(marker))  # Retry a previously confirmed, interrupted deletion.
        else:
            project = self.load(project_id)
            plan = {"project_id": project_id, "title": project.title, "project_ids": [project_id],"world_project_id":project.world_project_id}
        if plan["project_id"] != project_id or confirmation != plan["title"]:
            raise ValueError("Type the full project name exactly to confirm deletion.")
        if expected_world_id is not None and (plan.get("world_project_id") != expected_world_id or plan["project_ids"] != [project_id]):
            raise ValueError("This episode does not belong to the selected World.")
        stories = (self.root / "03_stories").resolve()
        if not stories.is_relative_to(self.root) or stories == self.root:
            raise ValueError("Project storage escapes the channel root.")
        def checked_path(pid):
            raw = self.root / "03_stories" / pid
            path = self.project_path(pid)
            if path.parent != stories or raw.is_symlink() or getattr(raw, "is_junction", lambda: False)():
                raise ValueError("Cannot delete a linked project directory or a path outside project storage.")
            return path
        root_path = checked_path(project_id)
        with ExitStack() as locks:
            if root_path.exists():
                locks.enter_context(project_lock(root_path, allow_deleted=True))
            if not marker.exists():
                current = self.load(project_id)
                if current.title != confirmation:
                    raise ValueError("The project name changed. Reopen Delete project.")
                if expected_world_id is not None and current.world_project_id != expected_world_id:
                    raise ValueError("This episode does not belong to the selected World.")
                if expected_world_id is None:
                    plan["project_ids"] += [p.project_id for p in self.list_projects() if p.world_project_id == project_id]
            paths = [checked_path(pid) for pid in plan["project_ids"]]
            for path in paths:
                if path != root_path and path.exists():
                    locks.enter_context(project_lock(path, allow_deleted=True))
            # All locks must be available before anything is marked or removed.
            # Markers live outside project folders so Windows can close lock files
            # before removal while preventing another worker from writing again.
            for path in paths:
                atomic_write(deletion_marker(path), json.dumps(plan, ensure_ascii=False))
        for path in reversed(paths):
            if path.exists():
                _file_access(lambda path=path: shutil.rmtree(path))
        logger.info("Deleted project %s and %s episode(s)", project_id, len(paths)-1)
        return plan["project_ids"]
