"""Reusable identity assets, independent of storyboard and animation production."""
import json
from uuid import uuid4

from pydantic import Field

from .animation_models import LocationBible, VoiceProfile
from .files import atomic_write, input_hash
from .locking import project_lock
from .models import ApprovalRecord, Identifier, Model, Nonempty
from .pipeline import Pipeline, ApprovalRequired


class Costume(Model):
    costume_id: Identifier
    character_id: Identifier
    description: Nonempty


class Prop(Model):
    prop_id: Identifier
    description: Nonempty
    owner_id: Identifier | None = None
    location_id: Identifier | None = None
    state: str = "intact"


class IdentityLibrary(Model):
    source_hash: str
    voices: dict[str, VoiceProfile]
    locations: dict[str, LocationBible]
    costumes: dict[str, Costume]
    props: dict[str, Prop] = Field(default_factory=dict)
    style: str = ""
    approval: ApprovalRecord | None = None


class IdentityManager:
    def __init__(self, manager, project_id):
        self.manager, self.project_id = manager, project_id
        self.path = manager.project_path(project_id)
        self._pipeline = None

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = Pipeline(self.manager, self.project_id)
        return self._pipeline

    def load(self):
        file = self.path / "identity_library.json"
        return IdentityLibrary.model_validate_json(file.read_text(encoding="utf-8")) if file.exists() else None

    def source(self):
        return input_hash({name: self.pipeline.artifact(name).model_dump(mode="json")
                           for name in ("world", "characters")})

    def validate(self, library):
        chars = {c.character_id for c in self.pipeline.artifact("characters").characters}
        locations = {loc.location_id for loc in self.pipeline.artifact("world").locations}
        if library.source_hash != self.source():
            raise ValueError("World or characters changed; rebuild the identity library")
        if set(library.voices) != chars or set(library.locations) != locations:
            raise ValueError("Keep exactly the current character and location IDs")
        for cid, profile in library.voices.items():
            if profile.character_id != cid:
                raise ValueError("Voice profile ID must match its character key")
        if any(key != loc.location_id for key, loc in library.locations.items()):
            raise ValueError("Location ID must match its key")
        for key, costume in library.costumes.items():
            if key != costume.costume_id or costume.character_id not in chars:
                raise ValueError("Costumes need a matching costume ID and an existing character")
        if {c.character_id for c in library.costumes.values()} != chars:
            raise ValueError("Every character needs at least one registered costume")
        for key, prop in library.props.items():
            if (key != prop.prop_id or prop.owner_id is not None and prop.owner_id not in chars
                    or prop.location_id is not None and prop.location_id not in locations):
                raise ValueError("Props need matching IDs and valid owner/location IDs")

    def _write(self, library):
        atomic_write(self.path / "identity_library.json", library.model_dump_json(by_alias=True, indent=2))
        for filename, entries in (("voice_profiles.json", library.voices), ("location_bible.json", library.locations),
                                  ("costume_registry.json", library.costumes), ("prop_registry.json", library.props)):
            atomic_write(self.path / filename, json.dumps({key: item.model_dump(mode="json", by_alias=True)
                         for key, item in entries.items()}, ensure_ascii=False, indent=2))
        atomic_write(self.path / "identity_library.md", "# Identity and reference library\n\n"
                     + "\n".join(f"- {key}: {item.description}" for key, item in library.costumes.items())
                     + "\n\n" + "\n".join(f"- {key}: {item.description}; state: {item.state}" for key, item in library.props.items()))

    def prepare(self):
        self.pipeline.inspect()
        with project_lock(self.path):
            manifest = self.manager.load(self.project_id)
            if any(not manifest.steps.get(name) or manifest.steps[name].status != "succeeded" for name in ("world", "characters")):
                raise ValueError("Complete World and Characters before building identity assets")
            old = self.load()
            chars = self.pipeline.artifact("characters").characters
            locations = self.pipeline.artifact("world").locations
            library = IdentityLibrary(source_hash=self.source(),
                voices={c.character_id: VoiceProfile(character_id=c.character_id, age_range=str(c.age), speaking_style=c.speaking_style)
                        for c in chars}, locations={loc.location_id: LocationBible(location_id=loc.location_id, description=loc.description)
                                                  for loc in locations},
                costumes={f"{c.character_id[:119]}_default": Costume(costume_id=f"{c.character_id[:119]}_default",
                          character_id=c.character_id, description=c.clothing) for c in chars})
            if old:
                same_source = old.source_hash == library.source_hash
                for cid in library.voices.keys() & old.voices.keys():
                    library.voices[cid] = old.voices[cid]
                for lid in library.locations.keys() & old.locations.keys():
                    library.locations[lid] = old.locations[lid].model_copy(deep=True) if same_source else old.locations[lid].model_copy(
                        update={"description": library.locations[lid].description})
                library.costumes.update({key: item for key, item in old.costumes.items() if item.character_id in library.voices
                                         and (same_source or key not in library.costumes)})
                library.props = {key: item for key, item in old.props.items()
                                 if (item.owner_id is None or item.owner_id in library.voices)
                                 and (item.location_id is None or item.location_id in library.locations)}
                library.style = old.style
            else:
                # Migrate explicit existing casting, never infer a provider voice from a logical ID.
                from .animation import load_animation
                direction = load_animation(self.path)
                if direction:
                    for cid in library.voices.keys() & direction.plan.voices.keys():
                        library.voices[cid] = direction.plan.voices[cid].model_copy(deep=True)
                    for lid in library.locations.keys() & direction.plan.locations.keys():
                        library.locations[lid] = direction.plan.locations[lid].model_copy(
                            update={"description": library.locations[lid].description}, deep=True)
            self.validate(library)
            if old:
                atomic_write(self.path / "history" / "identity" / f"{uuid4().hex}.json", old.model_dump_json(by_alias=True, indent=2))
            self._write(library)
            return library

    def save(self, library, *, expected):
        library = IdentityLibrary.model_validate(library)
        old = self.load()
        if old is None or old.model_dump_json(by_alias=True) != expected:
            raise ValueError("Identity library changed; reload before saving")
        self.validate(library)
        # Verify outside the project lock and before changing files or approvals.
        # The repeated optimistic check below protects edits made during lookup.
        from .voice_validation import validate_provider_voice
        for cid, profile in library.voices.items():
            profile.voice_id = profile.voice_id.strip()
            old_profile = old.voices.get(cid)
            if profile.voice_id and (not old_profile or profile.voice_id != old_profile.voice_id):
                validate_provider_voice(self.manager, self.project_id, profile.voice_id, speaker=cid)
        with project_lock(self.path):
            old = self.load()
            if old is None or old.model_dump_json(by_alias=True) != expected:
                raise ValueError("Identity library changed; reload before saving")
            self.validate(library)
            atomic_write(self.path / "history" / "identity" / f"{uuid4().hex}.json", old.model_dump_json(by_alias=True, indent=2))
            library.approval = None
            self._write(library)
            return library

    def signature(self, library):
        self.validate(library)
        from .assets import AssetPipeline
        assets = AssetPipeline(self.manager, self.project_id)
        state = assets.load()
        try:
            used_locations = {s.location_id for s in self.pipeline.artifact("storyboard").scenes}
        except OSError:
            used_locations = set(library.locations)
        keys = [f"character_references:{cid}" for cid in library.voices] + [f"backgrounds:{lid}" for lid in used_locations]
        records = {}
        for key in keys:
            record = state.records.get(key)
            if not record or record.status != "succeeded" or not assets._intact(record):
                raise ApprovalRequired("Generate intact character and background references before library approval")
            records[key] = record.file_hash
        manifest = self.manager.load(self.project_id)
        if not {"characters", "backgrounds"} <= manifest.asset_approvals.keys():
            raise ApprovalRequired("Approve character and background references first")
        if any(not profile.voice_id.strip() for profile in library.voices.values()):
            raise ApprovalRequired("Choose every character's explicit provider Voice ID")
        return input_hash({"library": library.model_dump(mode="json", exclude={"approval"}),
                           "references": records, "style": state.generic_prompt, "styleImage": state.style_reference_hash})

    def approve(self):
        # Inspect before acquiring the shared project lock; nested locks are prohibited.
        from .assets import AssetPipeline
        AssetPipeline(self.manager, self.project_id).inspect()
        with project_lock(self.path):
            library = self.load()
            if library is None:
                raise ValueError("Build the identity library first")
            library.approval = ApprovalRecord(content_hash=self.signature(library))
            self._write(library)
            return library

    def require_approved(self):
        library = self.load()
        if library is None or not library.approval or library.approval.content_hash != self.signature(library):
            raise ApprovalRequired("Review and approve the current identity library in Assets")
        return library
