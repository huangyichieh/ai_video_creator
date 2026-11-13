"""World-first projects and isolated episodes using the existing production engines."""
import json
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from .creative import World, Character, Characters, Location
from .files import atomic_write, input_hash, read_text
from .project_documents import read_document, write_document
from .locking import project_lock
from .models import Model, ApprovalRecord, Nonempty, utc_now
from .pipeline import Pipeline, ApprovalRequired
from .worldview import WorldSettings


class TextAdvice(Model):
    summary: str = ""
    proposed_text: str = ""
    reasons: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


class TextReview(Model):
    review_id: str = ""
    source_hash: str
    context_hash: str
    advice: TextAdvice
    decision: str = ""
    accepted_text: str = ""
    verification: TextAdvice | None = None
    verification_hash: str = ""


class WorldDelta(Model):
    rules: list[str] = Field(default_factory=list)
    technology_magic_rules: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    timeline: list[str] = Field(default_factory=list)
    forbidden_contradictions: list[str] = Field(default_factory=list)


class AdditionalSetting(Model):
    setting_id: str = Field(default_factory=lambda: uuid4().hex)
    text: Nonempty
    review: TextReview | None = None
    approval: ApprovalRecord | None = None
    delta: WorldDelta | None = None


class WorldState(Model):
    base_text: str = ""
    display_name: str | None = None
    default_language: str = ""
    settings: WorldSettings | None = None
    review: TextReview | None = None
    approval: ApprovalRecord | None = None
    base_world_hash: str = ""
    additions: list[AdditionalSetting] = Field(default_factory=list)


def generate_advice(pipeline, text, context, *, instructions="", stage="world_review"):
    """Use existing cost/retry tracking; demo never makes a paid request."""
    if pipeline.demo:
        advice = TextAdvice(summary="Offline structural check only. Production AI checks consistency, typos and improvements.", proposed_text=text)
    else:
        from .modification import modify_content
        from .worldview import WORLD_REVIEW_GUIDANCE
        guidance = WORLD_REVIEW_GUIDANCE if stage.startswith("world") else ""
        advice = modify_content(pipeline, TextAdvice(),
            "Review the supplied text for typos, internal contradictions, conflicts with approved canon, clarity, "
            "and opportunities to make it robust and interesting. Return the complete proposed text in its original language, "
            "reasons for improvements and any unresolved conflicts. Approved canon is immutable. "
            "Do not claim a contradiction is resolved unless the proposed text resolves it. For JSON input, proposed_text must be complete valid JSON preserving IDs, numbers, list order, structure and provider/model selectors. Change text fields only; do not use Markdown fences. " + guidance + " User direction: " + instructions,
            stage, context={"text": text, "approvedCanon": context})
    return TextReview(review_id=uuid4().hex, source_hash=input_hash(text), context_hash=input_hash(context), advice=advice)


class WorldStudio:
    def __init__(self, manager, project_id, pipeline=None):
        self.manager, self.project_id = manager, project_id
        self.path = manager.project_path(project_id)
        self.pipeline = pipeline or Pipeline(manager, project_id)

    @classmethod
    def create(cls, manager, text, *, default_language=None):
        title_hint = ""
        if isinstance(text, WorldSettings):
            if not any(getattr(text, field).strip() for field in WorldSettings.model_fields if field != "name"):
                raise ValueError("Enter at least one World settings point.")
            title_hint = text.name.strip() or next(getattr(text, field).strip().splitlines()[0] for field in WorldSettings.model_fields
                                                  if field != "name" and getattr(text, field).strip())
            text = text.text()
        if not text.strip():
            raise ValueError("Enter World settings.")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            outline = Path(tmp) / "world.txt"
            outline.write_text(text, encoding="utf-8")
            title = (title_hint or text.strip().splitlines()[0].lstrip("# "))[:80] or "New World"
            manifest = manager.create(title, outline, language=default_language)
        manifest.workflow_version = "world_episode"
        manager.save(manifest)
        studio = cls(manager, manifest.project_id)
        studio.save(WorldState(base_text=text))
        return manifest

    def load(self):
        file = self.path / "world_setting.json"
        if not file.exists():
            file = self.path / "world_settings.json"
        if file.exists():
            from .file_cache import read_model
            state = read_model(file, WorldState)
        else:
            # Read-only compatibility: existing projects are never silently approved.
            file = self.path / "world_source.json"
            text = read_document(file) if file.exists() else self.manager.read_outline(self.project_id)
            state = WorldState(base_text=text)
        projection = WorldSettings.from_text(state.base_text)
        if state.display_name is not None:
            projection.name = state.display_name
        if state.settings is not None and state.settings != projection:
            if state.approval:
                raise ApprovalRequired("Approved World settings fields changed outside the review workflow. Restore the approved JSON before continuing.")
            state.base_text = state.settings.text()
        state.settings = state.settings or projection
        state.default_language = state.default_language or self.manager.load(self.project_id).language
        old_review = self.path / "world_draft_review.json"
        if state.review is None and old_review.exists():
            state.review = TextReview.model_validate_json(read_text(old_review))
        return state

    def save(self, state):
        state.default_language = state.default_language or self.manager.load(self.project_id).language
        state.settings = WorldSettings.from_text(state.base_text)
        if state.display_name is not None:
            state.settings.name = state.display_name
        atomic_write(self.path / "world_setting.json", state.model_dump_json(by_alias=True, indent=2))
        # Remove only superseded settings files after the replacement is safely saved.
        for name in ("world_settings.json", "world_settings.md", "world_draft_review.json"):
            (self.path / name).unlink(missing_ok=True)

    def migrate(self):
        legacy = any((self.path / name).exists() for name in ("world_settings.json", "world_settings.md", "world_draft_review.json"))
        if not legacy and (self.path / "world_setting.json").exists():
            return self.load()
        with project_lock(self.path):
            state = self.load()
            self.save(state)
            return state

    def edit_settings(self, settings, *, default_language=None):
        settings = WorldSettings.model_validate(settings)
        from .worldview import WORLD_FIELD_NAMES
        if not any(getattr(settings, field).strip() for field in WORLD_FIELD_NAMES):
            raise ValueError("Enter at least one World settings point.")
        self.edit_base(settings.text(), default_language=default_language)

    def rename(self, name):
        """Change the display/project name without changing approved canon or reviews."""
        name = name.strip()
        if not name or "\n" in name or "\r" in name:
            raise ValueError("Enter a World name on a single line.")
        with project_lock(self.path):
            state = self.load()
            manifest = self.manager.load(self.project_id)
            manifest.title = name
            # Validate the title before writing either file.
            type(manifest).model_validate(manifest.model_dump())
            state.display_name = name
            self.save(state)
            self.manager.save(manifest)

    def canon(self):
        state = self.load()
        return {"worldSettings": state.base_text, "additionalSettings": [s.text for s in self.approved_additions(state)]}

    @staticmethod
    def approved_additions(state):
        return sorted((item for item in state.additions if item.approval), key=lambda item: (item.approval.approved_at, item.setting_id))

    def require_approved(self):
        state = self.load()
        if not state.approval:
            raise ApprovalRequired("Approve World settings before continuing to Assets or episodes.")
        manifest = self.manager.load(self.project_id)
        base = World.model_validate_json(read_text(self.path / "world_base.json"))
        base_hash = input_hash(base.model_dump(mode="json", by_alias=True))
        expected = input_hash({"text": state.base_text, "world": state.base_world_hash or base_hash})
        current_hash = input_hash(self.pipeline.artifact("world").model_dump(mode="json", by_alias=True))
        derived_hash = input_hash(self._effective_world(state).model_dump(mode="json", by_alias=True))
        if (state.approval.content_hash != expected or base_hash != (state.base_world_hash or base_hash)
                or current_hash != manifest.steps["world"].output_hash or current_hash != derived_hash):
            raise ApprovalRequired("Approved World files changed outside the app. Restore the approved files before continuing.")
        return state

    def _effective_world(self, state):
        world = World.model_validate_json(read_text(self.path / "world_base.json"))
        for item in self.approved_additions(state):
            if not item.delta:
                continue
            for name in ("rules", "technology_magic_rules", "organizations", "timeline", "forbidden_contradictions"):
                existing = getattr(world, name)
                existing.extend(value for value in getattr(item.delta, name) if value not in existing)
            ids = {location.location_id for location in world.locations}
            if ids & {location.location_id for location in item.delta.locations}:
                raise ValueError("Additional locations must use new IDs; approved locations cannot be replaced.")
            world.locations.extend(item.delta.locations)
        return World.model_validate(world.model_dump())

    def edit_base(self, text, *, default_language=None):
        if not text.strip():
            raise ValueError("World settings cannot be empty.")
        with project_lock(self.path):
            state = self.load()
            if state.approval:
                raise ValueError("Approved World settings are immutable. Add an additional setting instead.")
            self._preserve_legacy_outline()
            if default_language is not None:
                default_language = default_language.strip()
                if not default_language:
                    raise ValueError("Choose a default language.")
                manifest = self.manager.load(self.project_id)
                if manifest.language != default_language:
                    manifest.language = default_language
                    self.manager.save(manifest)
                    state.default_language = default_language
                    state.review = None
            state.base_text = text
            self.save(state)
            write_document(self.path / "outline.json", text)

    def review(self, *, setting_id=None, instructions=""):
        with project_lock(self.path):
            state = self.load()
            item = self._pending(state, setting_id) if setting_id else None
            if not item and state.approval:
                raise ValueError("Approved World settings are immutable.")
            text = item.text if item else state.base_text
            context = self.canon() if item else {}
            review = generate_advice(self.pipeline, text, context, instructions=instructions,
                                     stage="world_addition_review" if item else "world_review")
            if item:
                item.review = review
            else:
                state.review = review
            self.save(state)
            return review

    def approve_base(self, *, expected):
        with project_lock(self.path):
            state = self.load()
            if state.approval:
                return state
            if input_hash(state.base_text) != expected:
                raise ValueError("World settings changed. Review the current draft.")
            self._preserve_legacy_outline()
            if state.review is None:
                raise ApprovalRequired("Run AI review, then apply or revert its suggestion before approving World settings.")
            review = state.review
            if review.decision not in ("apply", "revert") or review.accepted_text != state.base_text:
                raise ApprovalRequired("AI review the current World draft and apply or revert the suggestion before approving.")
            checked = self._verify_selection(review, state.base_text, {}, "world_base_consistency")
            self.save(state)
            if checked.conflicts:
                raise ApprovalRequired("Resolve contradictions in the selected World draft: " + "; ".join(checked.conflicts))
            write_document(self.path / "outline.json", state.base_text)
            manifest = self.manager.load(self.project_id)
            if manifest.workflow_version == "world_episode":
                manifest = self.pipeline._refresh()
            # Preserve existing structured canon during migration; new projects
            # generate it from the reviewed World settings using the same module.
            if not manifest.steps.get("world") or manifest.steps["world"].status != "succeeded":
                self.pipeline._run_step("world", force=True)
            manifest = self.manager.load(self.project_id)
            manifest.workflow_version = "world_episode"
            manifest.provider_config.setdefault("llm:world", self.pipeline.config.model_copy(deep=True))
            if manifest.steps.get("characters") and manifest.steps["characters"].status == "succeeded":
                manifest.provider_config.setdefault("llm:characters", self.pipeline.config.model_copy(deep=True))
            self.manager.save(manifest)
            # Existing projects acquire a World-only input fingerprint without
            # changing their structured canon or historical generation hash.
            manifest.steps["world"].retained_input_hash = self.pipeline._fingerprint("world")
            if manifest.steps.get("characters") and manifest.steps["characters"].status == "succeeded":
                manifest.steps["characters"].retained_input_hash = self.pipeline._fingerprint("characters")
            for name, step in manifest.steps.items():
                if name not in ("world", "characters") and step.status == "succeeded":
                    step.retained_input_hash = self.pipeline._fingerprint(name)
            self.manager.save(manifest)
            state.approval = ApprovalRecord(content_hash=input_hash({"text": state.base_text, "world": manifest.steps["world"].output_hash}))
            state.base_world_hash = manifest.steps["world"].output_hash
            manifest.content_approvals["world"] = state.approval
            self.manager.save(manifest)
            atomic_write(self.path / "world_base.json", self.pipeline.artifact("world").model_dump_json(by_alias=True, indent=2))
            self.save(state)
            return state

    def resolve_base(self, decision, text, *, expected):
        with project_lock(self.path):
            state = self.load()
            if state.approval or input_hash(state.base_text) != expected:
                raise ValueError("World draft changed or is already approved.")
            if state.review is None:
                raise ApprovalRequired("Run AI review before applying or reverting World suggestions.")
            review = state.review
            if review.source_hash != expected or decision not in ("apply", "revert"):
                raise ValueError("Review the current World draft first.")
            selected = text if decision == "apply" else state.base_text
            if not selected.strip():
                raise ValueError("World settings cannot be empty.")
            review.decision, review.accepted_text = decision, selected
            review.verification, review.verification_hash = None, ""
            self._preserve_legacy_outline()
            state.base_text = selected
            self.save(state)
            write_document(self.path / "outline.json", selected)

    def _verify_selection(self, review, text, context, stage):
        signature = input_hash({"text": text, "canon": context})
        if review.verification is None or review.verification_hash != signature:
            checked = generate_advice(self.pipeline, text, context, stage=stage)
            review.verification, review.verification_hash = checked.advice, signature
        return review.verification

    def _preserve_legacy_outline(self):
        file = self.path / "legacy_episode_outline.json"
        if (self.path / "story.json").exists() and not file.exists() and self.manager.load(self.project_id).workflow_version == "legacy":
            write_document(file, self.manager.read_outline(self.project_id))

    def add_setting(self, text):
        with project_lock(self.path):
            state = self.require_approved()
            item = AdditionalSetting(text=text)
            state.additions.append(item)
            self.save(state)
            return item

    def generate_characters(self, prompt="", *, single=False):
        if single and not prompt.strip():
            raise ValueError("Enter a character prompt.")
        with project_lock(self.path):
            self.require_approved()
            context = self.pipeline._context("characters")
            existing = self.pipeline.artifact("characters").characters if (self.path / "characters.json").exists() else []
            context.update(approvedCanon=self.canon(), characterRequest=prompt,
                           appendCharacters=bool(existing),
                           existingCharacters=[c.model_dump(mode="json", by_alias=True) for c in existing],
                           requirement="Generate new requested characters only. Use new unique character IDs; preserve the established worldview.")
            if single:
                context["requirement"] += " Generate exactly ONE new character matching the supplied prompt."
            from .models import StepState
            manifest = self.manager.load(self.project_id)
            old_override = manifest.provider_config.get("llm:characters")
            old_state = manifest.steps.get("characters")
            old_state = old_state.model_copy(deep=True) if old_state else None
            manifest.steps.setdefault("characters", StepState())
            from .generation_preferences import openai_config
            manifest.provider_config["llm:characters"] = openai_config(self.pipeline, "characters")
            self.manager.save(manifest)
            try:
                output = self.pipeline._generate("characters", context)
                if single and len(output.characters) != 1:
                    raise ValueError("AI returned more than one character. Refine the prompt and try again; the existing cast was preserved.")
                if existing and self.pipeline.demo:
                    for c in output.characters:
                        c.character_id += "_" + uuid4().hex[:6]
                        c.name = prompt.strip()[:60] or c.name
                old_ids = {c.character_id for c in existing}
                if old_ids & {c.character_id for c in output.characters}:
                    raise ValueError("New characters must have unique IDs; the existing cast was preserved.")
            except Exception as exc:
                failed = self.manager.load(self.project_id)
                if old_override:
                    failed.provider_config["llm:characters"] = old_override
                else:
                    failed.provider_config.pop("llm:characters", None)
                attempts = failed.steps["characters"].attempts
                failed.steps["characters"] = old_state or StepState(status="failed", error=str(exc))
                failed.steps["characters"].attempts = attempts
                self.manager.save(failed)
                raise
            cast = Characters(characters=existing + output.characters)
            self.pipeline._commit("characters", cast, self.pipeline._fingerprint("characters"))
        from .identity import IdentityManager
        IdentityManager(self.manager, self.project_id).prepare()
        return cast

    @staticmethod
    def _pending(state, setting_id):
        item = next((s for s in state.additions if s.setting_id == setting_id), None)
        if item is None or item.approval:
            raise ValueError("Only pending additional settings can be changed.")
        return item

    def edit_setting(self, setting_id, text=None, *, remove=False):
        with project_lock(self.path):
            state = self.require_approved()
            item = self._pending(state, setting_id)
            if remove:
                state.additions.remove(item)
            else:
                item.text, item.review = text, None
            self.save(state)

    def approve_setting(self, setting_id, *, text, decision):
        with project_lock(self.path):
            state = self.require_approved()
            item = self._pending(state, setting_id)
            review = item.review
            if (not review or review.source_hash != input_hash(item.text) or review.context_hash != input_hash(self.canon())
                    or decision not in ("apply", "revert")):
                raise ValueError("AI review the current additional setting against the current canon first.")
            selected = text if decision == "apply" else item.text
            if not selected.strip():
                raise ValueError("Additional settings cannot be empty.")
            # Mandatory final check of edited or reverted text prevents bypassing
            # conflict checks by rejecting a proposed correction.
            checked = self._verify_selection(review, selected, self.canon(), "world_addition_consistency")
            self.save(state)
            if checked.conflicts:
                raise ApprovalRequired("Conflicts with approved canon: " + "; ".join(checked.conflicts))
            item.text = selected
            review.decision, review.accepted_text = decision, selected
            item.approval = ApprovalRecord(content_hash=input_hash({"text": selected, "canon": self.canon()}))
            if self.pipeline.demo:
                item.delta = WorldDelta(rules=[selected])
            else:
                from .modification import modify_content
                item.delta = modify_content(self.pipeline, WorldDelta(),
                    "Extract only additive structured World settings from this approved addition. Existing canon is immutable. "
                    "Return new rules, organizations, timeline entries and new locations with unique IDs as appropriate. "
                    "Never repeat or alter an existing location. Do not invent unrelated changes.",
                    "world_addition_structure", context={"addition": selected, "world": self.pipeline.artifact("world").model_dump(mode="json", by_alias=True)},
                    allow_new_ids=True)
            effective = self._effective_world(state)
            manifest = self.manager.load(self.project_id)
            step = manifest.steps["world"]
            step.revision += 1
            while (self.path / "history" / "world" / f"{step.revision:06d}.json").exists():
                step.revision += 1
            atomic_write(self.path / "history" / "world" / f"{step.revision:06d}.json", effective.model_dump_json(by_alias=True, indent=2))
            self.pipeline._write_outputs("world", effective)
            step.output_hash = input_hash(effective.model_dump(mode="json", by_alias=True))
            step.retained_input_hash = self.pipeline._fingerprint("world")
            if manifest.steps.get("characters") and manifest.steps["characters"].status == "succeeded":
                manifest.steps["characters"].retained_input_hash = self.pipeline._fingerprint("characters")
            self.manager.save(manifest)
            self.save(state)
        if (self.path / "characters.json").exists():
            from .identity import IdentityManager
            IdentityManager(self.manager, self.project_id).prepare()
        return item


class Episodes:
    def __init__(self, manager, world_id):
        self.manager, self.world_id = manager, world_id
        self.world = WorldStudio(manager, world_id)

    def list(self):
        return sorted((p for p in self.manager.list_projects() if p.world_project_id == self.world_id),
            key=lambda project:(project.created_at,project.project_id),reverse=True)

    def remove(self,episode_id,*,confirmation):
        """Delete one confirmed episode while retaining its parent World and siblings."""
        with project_lock(self.world.path):
            return self.manager.delete(episode_id,confirmation=confirmation,expected_world_id=self.world_id)

    def create(self, title, outline, duration):
        with project_lock(self.world.path):
            return self._create(title, outline, duration)

    def _create(self, title, outline, duration):
        self.world.require_approved()
        if not title.strip() or not outline.strip() or duration <= 0:
            raise ValueError("Enter an episode title, outline and positive target length.")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "outline.txt"
            source.write_text(outline, encoding="utf-8")
            parent = self.manager.load(self.world_id)
            episode = self.manager.create(title, source, language=parent.language, target_duration_sec=duration)
        episode.world_project_id = self.world_id
        episode.workflow_version = "world_episode"
        episode.canon_snapshot = self.world.canon()
        episode.provider_config = parent.provider_config.copy()
        for name in ("world", "characters"):
            if "llm:" + name not in episode.provider_config and parent.provider_config.get("llm"):
                episode.provider_config["llm:" + name] = parent.provider_config["llm"].model_copy(deep=True)
        self.manager.save(episode)
        self.sync(episode.project_id)
        return self.manager.load(episode.project_id)

    def adopt_existing(self):
        """Explicit copy migration: preserve all original project files and costs."""
        import shutil
        from .modules import MODULES
        if self.list():
            raise ValueError("An episode already exists; import the legacy story before creating other episodes.")
        source = self.manager.load(self.world_id)
        if not (self.world.path / "story.json").exists():
            raise ValueError("No existing story to import.")
        old_outline = self.world.path / "legacy_episode_outline.json"
        outline = read_document(old_outline) if old_outline.exists() else self.manager.read_outline(self.world_id)
        episode = self.create(source.title, outline, source.target_duration_sec)
        path = self.manager.project_path(episode.project_id)
        with project_lock(path):
            for folder in ("characters", "prompts", "images", "videos", "audio", "subtitles", "thumbnail", "output", "history", "locations"):
                if (self.world.path / folder).exists():
                    shutil.copytree(self.world.path / folder, path / folder, dirs_exist_ok=True)
            for file in self.world.path.iterdir():
                if file.is_file() and file.suffix == ".json" and file.name not in (
                        "manifest.json", "outline.json", "world_setting.json", "world_settings.json", "world_draft_review.json", "world_base.json", "usage.json", "legacy_episode_outline.json"):
                    shutil.copy2(file, path / file.name)
            episode.steps = {name: state.model_copy(deep=True) for name, state in source.steps.items()}
            episode.approvals = {name: approval.model_copy(deep=True) for name, approval in source.approvals.items()}
            episode.asset_approvals = {name: approval.model_copy(deep=True) for name, approval in source.asset_approvals.items()}
            episode.asset_state, episode.asset_step = source.asset_state, source.asset_step
            episode.render_state, episode.final_approval = source.render_state, source.final_approval
            self.manager.save(episode)
            pipeline = Pipeline(self.manager, episode.project_id)
            for name, state in episode.steps.items():
                if state.status == "succeeded":
                    state.retained_input_hash = pipeline._fingerprint(name)
            pipeline._set_status(episode)
            self.manager.save(episode)
        return episode

    def sync(self, episode_id):
        """Copy canonical inputs without sharing mutable episode production files."""
        self.world.require_approved()
        root = self.manager.load(self.world_id)
        episode = self.manager.load(episode_id)
        if episode.world_project_id != self.world_id:
            raise ValueError("Episode belongs to another World.")
        pipeline = Pipeline(self.manager, episode_id)
        with project_lock(pipeline.path):
            changed = False
            for name in ("world", "characters"):
                state = root.steps.get(name)
                if not state or state.status != "succeeded":
                    continue
                if name == "world" and episode.steps.get("world"):
                    continue  # Existing episodes retain their approved World snapshot.
                if name == "characters" and episode.shared_assets_hash and episode.steps.get("characters"):
                    continue  # An approved shared bundle remains an episode snapshot.
                from .modules import MODULES
                text = read_text(self.world.path / MODULES[name].filename)
                if episode.steps.get(name) and episode.steps[name].output_hash == state.output_hash:
                    continue
                output = MODULES[name].model.model_validate_json(text)
                config = root.provider_config.get("llm:" + name) or root.provider_config.get("llm")
                if config:
                    episode.provider_config["llm:" + name] = config.model_copy(deep=True)
                    self.manager.save(episode)
                if name == "world":
                    catalog = self.world.path / "background_library.json"
                    if catalog.exists():
                        atomic_write(pipeline.path / catalog.name, read_text(catalog))
                pipeline._write_outputs(name, output)
                episode.steps[name] = state.model_copy(deep=True)
                episode.steps[name].retained_input_hash = pipeline._fingerprint(name)
                if name == "characters":
                    pipeline._invalidate(episode, "characters", include_self=False)
                changed = True
            if changed:
                self.manager.save(episode)
                pipeline._set_status(episode)
                self.manager.save(episode)
        return episode

    def save_outline(self, episode_id, title, outline, duration, *, expected=None):
        with project_lock(self.manager.project_path(episode_id)):
            pipeline = Pipeline(self.manager, episode_id)
            manifest = self.manager.load(episode_id)
            if expected is not None and content_digest(pipeline, "outline") != expected:
                raise ValueError("Episode inputs changed. Reload the saved outline before saving.")
            if manifest.world_project_id != self.world_id:
                raise ValueError("Episode belongs to another World.")
            if not title.strip() or not outline.strip() or duration <= 0:
                raise ValueError("Enter title, outline and positive target length.")
            if (title, outline, duration) == (manifest.title, self.manager.read_outline(episode_id), manifest.target_duration_sec):
                return manifest
            atomic_write(pipeline.path / "history" / "episode_outline" / f"{utc_now().strftime('%Y%m%d%H%M%S%f')}.json",
                         json.dumps({"title": manifest.title, "outline": self.manager.read_outline(episode_id), "duration": manifest.target_duration_sec}, ensure_ascii=False))
            manifest.title, manifest.target_duration_sec = title, duration
            write_document(pipeline.path / "outline.json", outline)
            self.manager.save(manifest)
            pipeline._invalidate(manifest, "story", include_self=True)
            pipeline._invalidate(manifest, "script", include_self=True)
            manifest.content_approvals.clear()
            pipeline._set_status(manifest)
            self.manager.save(manifest)
            return manifest

    def refresh_world(self, episode_id):
        """Explicitly include new approved additions; reopen this episode's gates."""
        self.world.require_approved()
        with project_lock(self.manager.project_path(episode_id)):
            pipeline = Pipeline(self.manager, episode_id)
            manifest = self.manager.load(episode_id)
            if manifest.world_project_id != self.world_id:
                raise ValueError("Episode belongs to another World.")
            root = self.manager.load(self.world_id)
            world = self.world.pipeline.artifact("world")
            pipeline._write_outputs("world", world)
            manifest.steps["world"] = root.steps["world"].model_copy(deep=True)
            manifest.canon_snapshot = self.world.canon()
            manifest.shared_assets_hash = ""
            manifest.content_approvals.clear()
            self.manager.save(manifest)
            pipeline._invalidate(manifest, "story", include_self=True)
            pipeline._invalidate(manifest, "script", include_self=True)
            for name in ("world", "characters"):
                if name in manifest.steps:
                    manifest.steps[name].retained_input_hash = pipeline._fingerprint(name)
            pipeline._set_status(manifest)
            self.manager.save(manifest)
            return manifest

    def import_assets(self, episode_id,*,preserve_inputs=False):
        """Reuse approved immutable media, retaining references to their origin."""
        import shutil
        from .assets import AssetPipeline
        from .media_utils import file_hash
        if preserve_inputs:
            self.world.require_approved()
        else:
            self.sync(episode_id)
        existing = self.manager.load(episode_id)
        if existing.world_project_id != self.world_id:
            raise ValueError("Episode belongs to another World.")
        if existing.shared_assets_hash:
            target = AssetPipeline(self.manager, episode_id)
            parent = AssetPipeline(self.manager, self.world_id)
            with project_lock(target.path):
                state = target.load()
                from .files import atomic_copy
                def restore(relative, expected):
                    destination = target.local_path(relative)
                    if destination.exists() and file_hash(destination) == expected:
                        return
                    source_file = parent.local_path(relative)
                    if source_file.exists() and file_hash(source_file) == expected:
                        atomic_copy(source_file, destination)
                for record in state.records.values():
                    if record.stage not in ("character_references", "backgrounds") or not record.asset or target._intact(record):
                        continue
                    restore(record.asset.file_path, record.file_hash)
                for record in state.records.values():
                    if record.asset and record.asset.generation_params.get("sharedRoom"):
                        room = record.asset.generation_params["sharedRoom"]
                        restore(room["sceneFile"],room["sceneHash"])
                if target.shared_style_enabled(state):
                    for reference in state.style_references:
                        restore(reference["file"], reference["hash"])
                for reference in list(state.character_images.values()) + list(state.background_images.values()):
                    restore(reference["file"], reference["hash"])
                from .library_media import LibraryMedia
                from .bgm_library import entries
                for item in entries(self.manager,episode_id).values():
                    if item.get("file") and item.get("fileHash"):
                        restore(item["file"],item["fileHash"])
                for samples in LibraryMedia(self.manager, episode_id).load()["samples"].values():
                    for sample in samples:
                        restore(sample["audio"], sample["fileHash"])
            return target.load()
        if existing.canon_snapshot != self.world.canon():
            raise ApprovalRequired("Include the latest approved World additions on the episode Outline before importing its initial shared asset bundle.")
        parent = AssetPipeline(self.manager, self.world_id)
        source = parent.inspect()
        source_manifest = self.manager.load(self.world_id)
        target = AssetPipeline(self.manager, episode_id, demo=parent.demo)
        with project_lock(target.path):
            state = target.load()
            previous_state = state.model_dump(mode="json", exclude={"updated_at"})
            def copy_media(relative, expected=None):
                from .files import atomic_copy
                source_file, destination = parent.local_path(relative), target.local_path(relative)
                if destination.exists():
                    if expected is None and destination.stat().st_size == source_file.stat().st_size:
                        return  # Generated library paths are immutable, versioned files.
                    if expected is not None and file_hash(destination) == expected:
                        return
                atomic_copy(source_file, destination)
            def copy_metadata(relative, text):
                destination = target.path / relative
                if not destination.exists() or read_text(destination) != text:
                    atomic_write(destination, text)
            for field in ("generic_prompt", "style_reference", "style_reference_hash", "style_references", "provider_mode", "per_asset_references", "character_prompt_version"):
                setattr(state, field, getattr(source, field))
            # Shared reference inputs are authoritative, including removals.
            # update() retained obsolete episode edits when the World cleared
            # an edit during regeneration, making an intact imported image stale.
            shared_prefixes = ("character_references:", "backgrounds:")
            state.style_prompts = {
                **{key:value for key,value in state.style_prompts.items() if not key.startswith(shared_prefixes)},
                **{key:value for key,value in source.style_prompts.items() if key.startswith(shared_prefixes)}}
            state.asset_modifications = {
                **{key:value for key,value in state.asset_modifications.items() if not key.startswith(shared_prefixes)},
                **{key:value.model_copy(deep=True) for key,value in source.asset_modifications.items() if key.startswith(shared_prefixes)}}
            state.voice_map.update(source.voice_map)
            if parent.shared_style_enabled(source):
                for reference in source.style_references:
                    copy_media(reference["file"], reference["hash"])
            state.character_images = {cid: dict(reference) for cid, reference in source.character_images.items()}
            for reference in state.character_images.values():
                copy_media(reference["file"], reference["hash"])
            state.background_images = {lid: dict(reference) for lid, reference in source.background_images.items()}
            state.generation_inputs = {key: dict(value) for key, value in source.generation_inputs.items()}
            state.project_style_rules = source.project_style_rules
            state.project_style_image = dict(source.project_style_image) if source.project_style_image else None
            if state.project_style_image:
                copy_media(state.project_style_image["file"], state.project_style_image["hash"])
            state.background_modes = dict(source.background_modes)
            state.location_reference_sets = {lid: dict(spec) for lid, spec in source.location_reference_sets.items()}
            state.location_pair_prompts = {lid:dict(prompts) for lid,prompts in source.location_pair_prompts.items()}
            state.location_master_approvals = {lid: approval.model_copy(deep=True) for lid, approval in source.location_master_approvals.items()}
            state.location_yaw_approvals = {key: approval.model_copy(deep=True) for key, approval in source.location_yaw_approvals.items()}
            from .dual_panorama import is_key, root as location_root
            for lid, spec in source.location_reference_sets.items():
                from .shared_room import enabled
                if is_key(spec) or enabled(spec):
                    for source_file in location_root(parent, lid).rglob("*"):
                        if source_file.is_file() and source_file.suffix in (".png", ".json"):
                            copy_media(source_file.relative_to(parent.path).as_posix(), file_hash(source_file))
            for record in source.records.values():
                if record.asset and record.asset.generation_params.get("sharedRoom"):
                    room = record.asset.generation_params["sharedRoom"]
                    copy_media(room["sceneFile"],room["sceneHash"])
            for spec in state.location_reference_sets.values():
                for reference in spec.get("detail_references", {}).values():
                    record = source.records.get(reference.get("key"))
                    if parent._intact(record):
                        copy_media(reference["file"], reference["hash"])
            state.background_panorama_reviews = {lid: dict(review) for lid, review in source.background_panorama_reviews.items()}
            for reference in state.background_images.values():
                copy_media(reference["file"], reference["hash"])
            for name in ("background_library.json", "identity_library.json", "voice_profiles.json", "location_bible.json", "costume_registry.json", "prop_registry.json", "bgm_library.json"):
                file = parent.path / name
                if file.exists():
                    if name == "identity_library.json":
                        from .identity import IdentityManager,IdentityLibrary
                        identity_manager = IdentityManager(self.manager,episode_id)
                        scoped = identity_manager.scope_shared(IdentityLibrary.model_validate_json(read_text(file)))
                        current = identity_manager.load()
                        if current is None or scoped != current:
                            identity_manager._write(scoped)
                        continue
                    if name in ("voice_profiles.json","location_bible.json","costume_registry.json","prop_registry.json"):
                        continue  # Written from the episode-scoped identity above.
                    copy_metadata(name, read_text(file))
            for receipt in (parent.path / "audio" / "library").glob("*.source.json"):
                data = json.loads(read_text(receipt))
                copy_media(data["file"])
                copy_metadata(receipt.relative_to(parent.path), read_text(receipt))
            from .storyboard_asset_check import document
            root_sfx = document(parent.path,"storyboard_sfx.json").get("entries",{})
            episode_sfx = document(target.path,"storyboard_sfx.json").get("entries",{})
            for identity,item in root_sfx.items():
                existing_sfx = episode_sfx.get(identity,{})
                if not existing_sfx or existing_sfx.get("provenance",{}).get("sharedWorldProjectId") == self.world_id:
                    copy_media(item["file"],item["fileHash"])
                    episode_sfx[identity] = {**item,"provenance":{**item.get("provenance",{}),"sharedWorldProjectId":self.world_id}}
            if root_sfx:
                copy_metadata("storyboard_sfx.json",json.dumps({"entries":episode_sfx},ensure_ascii=False,indent=2))
            from .library_media import LibraryMedia
            media = LibraryMedia(self.manager, self.world_id).load()
            for versions in media["visuals"].values():
                for version in versions:
                    relative = version["asset"]["filePath"]
                    copy_media(relative)
            for samples in media["samples"].values():
                for sample in samples:
                    for field in ("audio", "video", "portrait"):
                        if sample.get(field):
                            copy_media(sample[field], sample.get("fileHash") if field == "audio" else None)
            copy_metadata("library_media.json", json.dumps(media, ensure_ascii=False, indent=2))
            for key, record in source.records.items():
                gate = "characters" if record.stage == "character_references" else "backgrounds"
                if record.stage not in ("character_references", "backgrounds"):
                    continue
                if not record.asset or not parent._intact(record):
                    continue
                copy_media(record.asset.file_path, record.file_hash)
                state.records[key] = record.model_copy(deep=True)
                state.records[key].asset.generation_params["sharedWorldProjectId"] = self.world_id
            state.source_hash = target._source()
            from .shared_room import enabled
            from .room_canonical import tasks as canonical_tasks, type_task
            from .background_library import BackgroundLibrary
            for lid,spec in state.location_reference_sets.items():
                if spec.get("reference_type") == "SIMPLE":
                    state.background_approvals.pop(lid,None)
                    if BackgroundLibrary(parent).approved(lid,source):
                        value = BackgroundLibrary(target).digest(lid,state,target.tasks(state,references_only=True))
                        if value:
                            state.background_approvals[lid] = ApprovalRecord(content_hash=value,note="Reused approved SIMPLE location")
                if enabled(spec) and source.background_approvals.get(lid):
                    value = BackgroundLibrary(target).digest(lid,state,canonical_tasks(lid,spec,type_task()))
                    if value:
                        state.background_approvals[lid] = ApprovalRecord(content_hash=value,note="Reused approved shared 3D room")
            tasks = target.tasks(state)
            manifest = self.manager.load(episode_id)
            approvals_changed = False
            for gate, stage in (("characters", "character_references"), ("backgrounds", "backgrounds")):
                subjects = {task.subject for task in tasks if task.stage == stage}
                scoped_approved = (all(parent.character_approved(subject,source) for subject in subjects) if gate == "characters" else
                    all(BackgroundLibrary(parent).approved(BackgroundLibrary(parent).subject_id(subject),source) for subject in subjects))
                if scoped_approved and target._stage_done(stage, state, tasks):
                    digest = target._gate_hash(gate, state, tasks)
                    if gate not in manifest.asset_approvals or manifest.asset_approvals[gate].content_hash != digest:
                        manifest.asset_approvals[gate] = ApprovalRecord(content_hash=digest, note="Reused approved references from World " + self.world_id)
                        approvals_changed = True
                    if gate == "characters":
                        state.character_approvals = {task.subject: manifest.asset_approvals[gate].model_copy(
                            update={"content_hash": target._character_approval_hash(task.subject, state)})
                            for task in tasks if task.stage == "character_references"}
            if approvals_changed:
                self.manager.save(manifest)
            if state.model_dump(mode="json", exclude={"updated_at"}) != previous_state:
                atomic_write(target.state_path, state.model_dump_json(by_alias=True, indent=2))
            from .identity import IdentityManager
            library = IdentityManager(self.manager, episode_id).load()
            bundle_ready = False
            if library and {"characters", "backgrounds"} <= manifest.asset_approvals.keys():
                try:
                    IdentityManager(self.manager, episode_id).require_approved()
                    used_characters = {task.subject for task in tasks if task.stage == "character_references"}
                    bundle_ready = all(LibraryMedia(self.manager, episode_id).voice_approved(cid, profile, media)
                        for cid, profile in library.voices.items() if cid in used_characters)
                    if not target.demo and any(s.narration.strip() for s in target.creative.artifact("storyboard").scenes):
                        bundle_ready = bundle_ready and bool(state.voice_map.get("narrator", "").strip())
                except (OSError, ValueError):
                    pass
            if bundle_ready:
                manifest.shared_assets_hash = input_hash({"references": {key: [record.file_hash, record.asset.asset_id] for key, record in state.records.items()
                    if record.stage in ("character_references", "backgrounds") and record.asset},
                    "identity": library.model_dump(mode="json", exclude={"approval"}),
                    "samples": {cid: LibraryMedia(self.manager, episode_id).selected_samples(cid, profile, media)
                                for cid, profile in library.voices.items()},
                    "costumes": media.get("activeVisuals", {})})
                self.manager.save(manifest)
            state.next_stage = target._next(state, tasks, manifest)
            if approvals_changed or state.model_dump(mode="json", exclude={"updated_at"}) != previous_state:
                target._save(state)
        return target.load()

    def refresh_assets(self, episode_id,*,preserve_inputs=False):
        self.world.require_approved()
        with project_lock(self.manager.project_path(episode_id)):
            manifest = self.manager.load(episode_id)
            if manifest.world_project_id != self.world_id or manifest.canon_snapshot != self.world.canon():
                raise ValueError("Include the latest World additions before refreshing shared assets.")
            manifest.shared_assets_hash = ""
            self.manager.save(manifest)
        return self.import_assets(episode_id,preserve_inputs=preserve_inputs)


def content_digest(pipeline, name):
    manifest = pipeline.manager.load(pipeline.project_id)
    if name == "outline":
        return input_hash({"title": manifest.title, "outline": pipeline.manager.read_outline(pipeline.project_id), "duration": manifest.target_duration_sec})
    return input_hash(pipeline.artifact(name).model_dump(mode="json", by_alias=True))


def approve_content(pipeline, name, *, expected):
    if name not in ("outline", "script", "prompts"):
        raise ValueError("Approve Outline, Script or Prompts using this workflow.")
    with project_lock(pipeline.path):
        if content_digest(pipeline, name) != expected:
            raise ValueError("Content changed. Review the current version before approving.")
        manifest = pipeline._refresh()
        if name != "outline" and (name not in manifest.steps or manifest.steps[name].status != "succeeded"):
            raise ApprovalRequired("Generate current content before approving.")
        manifest.content_approvals[name] = ApprovalRecord(content_hash=expected)
        pipeline.manager.save(manifest)
        return manifest


def approved_content(pipeline, name):
    approval = pipeline.manager.load(pipeline.project_id).content_approvals.get(name)
    try:
        return bool(approval and approval.content_hash == content_digest(pipeline, name))
    except (OSError, ValueError):
        return False


def review_differences(stage, text, proposed):
    from copy import deepcopy
    from .review_changes import text_fields
    if stage == "outline":
        original, suggested = {"text": text}, {"text": proposed}
    else:
        from .modules import MODULES
        original = MODULES[stage].model.model_validate_json(text).model_dump(mode="json", by_alias=True)
        suggested = MODULES[stage].model.model_validate_json(proposed).model_dump(mode="json", by_alias=True)
    before = dict(text_fields(original))
    after = dict(text_fields(suggested))
    candidate, changes = deepcopy(original), []
    for path, value in before.items():
        if path not in after or path[-1] in ("provider", "model"):
            continue
        if value != after[path]:
            target = candidate
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = after[path]
            changes.append({"path": list(path), "original": value, "suggested": after[path]})
    if candidate != suggested:
        raise ValueError("The AI proposal changed IDs, numbers, provider/model settings or structure. Review again; the saved original is unchanged.")
    return original, changes



def resolve_content_review(pipeline, name, text, *, expected, report_digest=None, decisions=()):
    """Atomically save reviewed episode content, its decisions and approval."""
    if name not in ("outline", "script", "prompts"):
        raise ValueError("Unsupported episode review stage.")
    with project_lock(pipeline.path):
        if content_digest(pipeline, name) != expected:
            raise ValueError("Content changed. Review the current version before approving.")
        file = pipeline.path / f"episode_{name}_review.json"
        report = TextReview.model_validate_json(read_text(file)) if file.exists() else None
        if (input_hash(report.model_dump()) if report else None) != report_digest:
            raise ValueError("The review changed. Reload the current suggestions before approving.")
        manifest = pipeline._refresh()
        if not manifest.world_project_id:
            raise ValueError("Use this review workflow for an episode.")
        WorldStudio(pipeline.manager, manifest.world_project_id).require_approved()
        original = pipeline.manager.read_outline(pipeline.project_id) if name == "outline" else pipeline.artifact(name).model_dump_json(by_alias=True, indent=2)
        if text != original:
            if not report or report.source_hash != input_hash(original):
                raise ApprovalRequired("Review the current original before applying suggested changes.")
            candidate, changes = review_differences(name, original, report.advice.proposed_text or original)
            if len(decisions) != len(changes):
                raise ApprovalRequired("Apply or revert every difference before saving.")
            for change, decision in zip(changes, decisions):
                if decision.get("path") != change["path"] or decision.get("choice") not in ("apply", "revert"):
                    raise ApprovalRequired("Apply or revert every current difference before saving.")
                selected = decision.get("text") if decision["choice"] == "apply" else change["original"]
                if not isinstance(selected, str):
                    raise ValueError("Applied suggestions must be text.")
                target = candidate
                for part in change["path"][:-1]:
                    target = target[part]
                target[change["path"][-1]] = selected
            if (candidate["text"] != text if name == "outline" else candidate != json.loads(text)):
                raise ValueError("Applied content must match the resolved review decisions.")
        if text != original:
            if name == "outline":
                if not text.strip():
                    raise ValueError("Episode outline cannot be empty.")
                write_document(pipeline.path / "history" / "episode_outline" / f"{uuid4().hex}.json", original)
                write_document(pipeline.path / "outline.json", text)
                pipeline._invalidate(manifest, "story", include_self=True)
                pipeline._invalidate(manifest, "script", include_self=True)
                manifest.content_approvals.clear()
                pipeline.manager.save(manifest)
            else:
                from .modules import MODULES, validate_output
                pipeline._gate(name, manifest)
                output = MODULES[name].model.model_validate_json(text)
                validate_output(name, output, pipeline._context(name))
                pipeline._commit(name, output, pipeline._fingerprint(name))
        manifest = pipeline.manager.load(pipeline.project_id)
        if name != "outline" and manifest.steps[name].status != "succeeded":
            raise ApprovalRequired("Generate current content before approval.")
        if report:
            report.decision, report.accepted_text = "resolved", text
            atomic_write(file, report.model_dump_json(by_alias=True, indent=2))
            receipt = {"review": report.model_dump(mode="json", by_alias=True), "decisions": list(decisions),
                       "original": original, "approvedHash": content_digest(pipeline, name)}
            atomic_write(pipeline.path / "history" / "episode_reviews" / name / f"{uuid4().hex}.json",
                         json.dumps(receipt, ensure_ascii=False, indent=2))
        manifest.content_approvals[name] = ApprovalRecord(content_hash=content_digest(pipeline, name), note="Reviewed and explicitly approved")
        pipeline._set_status(manifest)
        pipeline.manager.save(manifest)
        return manifest
