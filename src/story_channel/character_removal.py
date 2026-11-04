"""Remove shared cast entries without rewriting existing episode snapshots."""
import json
import re
from contextlib import ExitStack
from dataclasses import dataclass
from uuid import uuid4

from .creative import Characters
from .files import atomic_write, read_text
from .locking import project_lock, deletion_marker
from .studio import WorldStudio


@dataclass
class RemovalStatus:
    approved: bool
    uses: list[str]

    @property
    def allowed(self):
        return not self.approved or not self.uses

    @property
    def reason(self):
        return "This character is approved and appears in: " + "; ".join(self.uses) if not self.allowed else ""


class CharacterRemoval:
    def __init__(self, manager, world_id):
        self.manager, self.world_id = manager, world_id
        manifest = manager.load(world_id)
        if manifest.world_project_id or manifest.workflow_version != "world_episode":
            raise ValueError("Remove shared characters from the World's Assets page.")
        self.studio = WorldStudio(manager, world_id)
        self.path = self.studio.path

    def projects(self):
        # Do not silently overlook an unreadable manifest during a usage check.
        projects = []
        for path in (self.manager.root / "03_stories").iterdir():
            if path.is_dir() and (path / "manifest.json").exists() and not deletion_marker(path).exists():
                project = self.manager.load(path.name)
                if project.project_id == self.world_id or project.world_project_id == self.world_id:
                    projects.append(project)
        return projects

    @staticmethod
    def mentioned(value, character):
        if isinstance(value, dict):
            return any(CharacterRemoval.mentioned(item, character) for item in value.values())
        if isinstance(value, list):
            return any(CharacterRemoval.mentioned(item, character) for item in value)
        if not isinstance(value, str):
            return False
        for token in (character.character_id, character.name):
            if token.isascii():
                if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(token) + r"(?![A-Za-z0-9_-])", value, re.IGNORECASE):
                    return True
            elif token in value:
                return True
        return False

    def statuses(self, projects=None):
        characters = self.studio.pipeline.artifact("characters").characters
        manifest = self.manager.load(self.world_id)
        media_file = self.path / "library_media.json"
        media = json.loads(read_text(media_file)) if media_file.exists() else {}
        group_approved = bool(manifest.asset_approvals.get("characters") or manifest.content_approvals.get("characters"))
        from .assets import AssetPipeline
        assets = AssetPipeline(self.manager, self.world_id)
        production = assets.load()
        result = {c.character_id: RemovalStatus(group_approved or assets.character_approved(c.character_id, production)
                  or bool(media.get("voiceApprovals", {}).get(c.character_id)), [])
                  for c in characters}
        for project in projects if projects is not None else self.projects():
            path = self.manager.project_path(project.project_id)
            for filename, label in (("story.json", "Story"), ("script.json", "Script"),
                                    ("storyboard.json", "Storyboard"), ("animation.json", "Animation"),
                                    ("outline.md", "Story outline")):
                if filename == "outline.md" and not project.world_project_id:
                    continue
                file = path / filename
                if not file.exists():
                    continue
                value = json.loads(read_text(file)) if file.suffix == ".json" else read_text(file)
                if filename == "animation.json":
                    if not isinstance(value, dict):
                        raise ValueError("Cannot check character usage in " + str(file))
                    # Casting a voice profile alone is not an appearance in a scene.
                    plan = value.get("plan", {})
                    value = {"performances": plan.get("performances", []), "shots": plan.get("shots", []),
                             "generatedShots": value.get("shots", {})}
                for character in characters:
                    if self.mentioned(value, character):
                        result[character.character_id].uses.append(f"{project.title} · {label}")
        return result

    def remove(self, character_id, *, expected):
        with ExitStack() as locks:
            locks.enter_context(project_lock(self.path))
            projects = self.projects()
            for project in sorted(projects, key=lambda item: item.project_id):
                if project.project_id != self.world_id:
                    locks.enter_context(project_lock(self.manager.project_path(project.project_id)))
            characters = self.studio.pipeline.artifact("characters")
            character = next((c for c in characters.characters if c.character_id == character_id), None)
            if character is None:
                raise ValueError("This character has already been removed.")
            if character.model_dump_json(by_alias=True) != expected:
                raise ValueError("The character changed. Reopen Remove character before confirming.")
            status = self.statuses(projects)[character_id]
            if not status.allowed:
                raise ValueError(status.reason)
            archive = self.path / "history" / "removed_characters" / character_id / uuid4().hex
            filenames = ("characters.json", "character_bible.md", "identity_library.json", "voice_profiles.json",
                         "costume_registry.json", "prop_registry.json", "identity_library.md", "production.json",
                         "library_media.json", "manifest.json")
            snapshot = {name: read_text(self.path / name) for name in filenames if (self.path / name).exists()}
            atomic_write(archive / "snapshot.json", json.dumps(snapshot, ensure_ascii=False, indent=2))
            remaining = [c for c in characters.characters if c.character_id != character_id]
            background_approval = self.manager.load(self.world_id).asset_approvals.get("backgrounds")
            pipeline = self.studio.pipeline
            if remaining:
                pipeline._commit("characters", Characters(characters=remaining), pipeline._fingerprint("characters"))
            else:
                manifest = self.manager.load(self.world_id)
                pipeline._invalidate(manifest, "characters", include_self=True)
                state = manifest.steps["characters"]
                state.status, state.output_hash, state.error = "pending", "", None
                pipeline._set_status(manifest)
                self.manager.save(manifest)
                for name in ("characters.json", "character_bible.md", f"characters/{character_id}.json"):
                    (self.path / name).unlink(missing_ok=True)
            from .identity import IdentityManager
            identity = IdentityManager(self.manager, self.world_id)
            library = identity.load()
            costume_ids = set()
            if library:
                costume_ids = {key for key, costume in library.costumes.items() if costume.character_id == character_id}
                library.voices.pop(character_id, None)
                library.costumes = {key: item for key, item in library.costumes.items() if key not in costume_ids}
                for prop in library.props.values():
                    if prop.owner_id == character_id:
                        prop.owner_id = None
                library.approval = None
                library.source_hash = identity.source() if remaining else ""
                identity._write(library)
            from .assets import AssetPipeline
            assets = AssetPipeline(self.manager, self.world_id)
            from .character_visual_inputs import CharacterVisualInputs
            CharacterVisualInputs(assets).remove(character_id)
            state = assets.load()
            key = "character_references:" + character_id
            state.records.pop(key, None)
            state.character_approvals.pop(character_id, None)
            state.character_images.pop(character_id, None)
            state.style_prompts.pop(key, None)
            state.asset_modifications.pop(key, None)
            state.voice_map.pop(character_id, None)
            state.source_hash = ""
            state.next_stage = "character_references"
            atomic_write(assets.state_path, state.model_dump_json(by_alias=True, indent=2))
            from .library_media import LibraryMedia
            media = LibraryMedia(self.manager, self.world_id)
            data = media.load()
            data.get("samples", {}).pop(character_id, None)
            data.get("activeSamples", {}).pop(character_id, None)
            data.get("voiceApprovals", {}).pop(character_id, None)
            for key in list(data.get("visuals", {})):
                if key in {"costume:" + costume for costume in costume_ids}:
                    data["visuals"].pop(key)
                    data.get("activeVisuals", {}).pop(key, None)
            media.save(data)
            manifest = self.manager.load(self.world_id)
            manifest.asset_state, manifest.asset_step = "stale", "character_references"
            manifest.asset_approvals.pop("characters", None)
            manifest.asset_approvals.pop("assets", None)
            if background_approval:
                manifest.asset_approvals["backgrounds"] = background_approval
            self.manager.save(manifest)
