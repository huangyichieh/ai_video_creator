"""Resumable application service. UI and CLI share all workflow rules."""
import json
import logging
import time
from pathlib import Path

from .costs import CostTracker
from .request_tracking import tracked_call, request_fields
from .creative import Character, Characters, Prompts, Script, Story, Storyboard
from .markdown_exports import storyboard_markdown
from .project_documents import write_document
from .demo import DemoLLMProvider
from .files import atomic_write, input_hash
from .llm import OpenAILLMProvider, TransientProviderError, token_cost
from .locking import project_lock
from .models import ApprovalRecord, Model, ProjectStatus, ProviderConfig, StepState, UsageRecord, utc_now
from .modules import MODULES, PROMPT_VERSION, STEPS, build_prompt, validate_output, visual_prompt_adapter
from .projects import ProjectManager

logger = logging.getLogger(__name__)

# Generation context and reprocessing policy are separate. A review may read
# Characters/Story without requiring every existing production artifact to be
# regenerated when that review is refreshed.
REPROCESS = {
    "world": {"characters", "story", "review", "script", "storyboard", "prompts"},
    "characters": {"review"},
    "story": {"review"},
    "review": set(),
    "script": {"storyboard", "prompts"},
    "storyboard": {"prompts"},
    "prompts": set(),
}


class ApprovalRequired(ValueError):
    pass


class Pipeline:
    def __init__(self, manager: ProjectManager, project_id: str, provider=None, *, demo: bool | None = None,
                 llm_config: ProviderConfig | None = None, on_progress=None):
        self.manager = manager
        self.on_progress = on_progress
        self.project_id = project_id
        self.path = manager.project_path(project_id)
        existing = manager.load(project_id)
        self.settings = manager.settings
        saved = existing.provider_config.get("llm")
        self.demo = (llm_config.provider == "demo" if llm_config else demo if demo is not None else
                     (saved.provider == "demo" if saved else self.settings.llm_provider == "demo"))
        self.provider = provider or (DemoLLMProvider() if self.demo else
                                     OpenAILLMProvider(timeout=self.settings.llm_timeout_sec))
        self._custom_provider = provider is not None
        self.config = llm_config or (saved if saved and (saved.provider == "demo") == self.demo else
                                    ProviderConfig(provider="demo" if self.demo else self.settings.llm_provider,
                                                   model="demo-v1" if self.demo else self.settings.llm_model,
                                                   options={"max_output_tokens": self.settings.llm_max_output_tokens}))

    def _load(self):
        return self.manager.load(self.project_id)

    def artifact(self, step: str) -> Model:
        module = MODULES[step]
        from .file_cache import read_model
        return read_model(self.path / module.filename, module.model)

    def _context(self, step: str, *, fresh_assets=False) -> dict:
        manifest = self._load()
        context = {"title": manifest.title, "language": manifest.language,
                   "targetDurationSec": manifest.target_duration_sec,
                   "outline": self.manager.read_outline(self.project_id)}
        if manifest.workflow_version == "world_episode":
            from .studio import WorldStudio
            world_id = manifest.world_project_id or self.project_id
            canon = manifest.canon_snapshot or WorldStudio(self.manager, world_id).canon()
            if step in ("world", "characters"):
                canon = {**canon, "additionalSettings": []}
            context["approvedCanon"] = canon
            if step in ("world", "characters"):
                parent = self.manager.load(world_id)
                context.update(title=parent.title, targetDurationSec=parent.target_duration_sec,
                               outline=canon["worldSettings"])
        if not context["outline"].strip():
            raise ValueError("Story outline must not be empty")
        for dependency in MODULES[step].dependencies:
            context[dependency] = self.artifact(dependency).model_dump(mode="json", by_alias=True)
        if step not in ("world", "characters") and "world" in context and (self.path / "background_library.json").exists():
            from .background_library import locations
            context["world"]["locations"] = [loc.model_dump(mode="json", by_alias=True) for loc in locations(self)]
        if step == "storyboard":
            from .storyboard_assets import asset_catalog
            owner = manifest.world_project_id or self.project_id
            context["assetCatalog"] = asset_catalog(self.manager,owner,include_reusable=True) if fresh_assets else asset_catalog(self.manager,self.project_id)
            snapshot = self.path/"storyboard_reference_context.json"
            references = None
            if fresh_assets:
                from .storyboard_details import reference_context
                references = reference_context(self.manager,self.project_id)
            elif snapshot.exists():
                from .file_cache import read_json
                references = read_json(snapshot)
            if references is not None:
                context.update(references)
                known = {loc["locationId"]:loc for loc in context["world"]["locations"]}
                known.update({loc["locationId"]:loc for loc in references.get("sharedBackgrounds",[])})
                context["world"]["locations"] = list(known.values())
        if step == "prompts" and "storyboard" in context:
            known = {item["locationId"]:item for item in context["world"]["locations"]}
            for item in context["storyboard"].get("backgrounds",[]):
                known.setdefault(item["locationId"],item)
            context["world"]["locations"] = list(known.values())
        return context

    def _fingerprint(self, step: str, *, context=None) -> str:
        override = self._load().provider_config.get(f"llm:{step}")
        config = ({"provider": self.settings.prompt_provider, "model": self.settings.prompt_model}
                  if step == "prompts" else (override or self.config).model_dump(mode="json"))
        if step == "prompts" and override:
            config["llmOverride"] = override.model_dump(mode="json")
        context = self._context(step) if context is None else context
        schema = MODULES[step].model.model_json_schema()
        if step == "storyboard" and "detailBackgrounds" not in context:
            # Preserve fingerprints of completed pre-detail storyboards until
            # explicit generation adopts the expanded planning context/schema.
            schema.get("properties",{}).pop("detailBackgrounds",None)
            schema.get("$defs",{}).pop("StoryboardDetailBackground",None)
        return input_hash({"context": context, "config": config,
                           "version": PROMPT_VERSION + ("-concise-review-v2" if step == "review" else ""),
                           "schema": schema})

    def _invalidate(self, manifest, start: str, *, include_self: bool):
        affected = REPROCESS[start] | ({start} if include_self else set())
        for name in affected:
            manifest.content_approvals.pop(name, None)
        if start != "review":
            if manifest.render_state != "not_started":
                manifest.render_state = "stale"
            manifest.final_approval = None
            if manifest.asset_state != "not_started":
                manifest.asset_state = "stale"
            manifest.asset_approvals.clear()
        for step in affected:
            if step in manifest.steps:
                manifest.steps[step].status = "stale"
                manifest.steps[step].error = None
                manifest.steps[step].retained_input_hash = ""
        if start in ("world", "characters", "story", "review"):
            manifest.approvals.pop("story", None)
        if start in ("world", "script", "storyboard"):
            manifest.approvals.pop("storyboard", None)
        # Retain the original generation fingerprint as provenance. The separate
        # hash acknowledges updated context for artifacts preserved by this policy.
        for step, state in manifest.steps.items():
            if step != start and step not in affected and state.status == "succeeded":
                try:
                    state.retained_input_hash = self._fingerprint(step)
                except (OSError, ValueError):
                    pass  # Incomplete upstream sources stay blocked until repaired.
        manifest.approved_steps = [gate for gate in ("story", "storyboard") if gate in manifest.approvals]

    def _refresh(self):
        manifest = self._load()
        for step in STEPS:
            state = manifest.steps.get(step)
            if state is None or state.status != "succeeded":
                continue
            try:
                dependencies_ready = all(
                    manifest.steps.get(d, StepState()).status == "succeeded"
                    for d in MODULES[step].dependencies)
                valid = (state.output_hash == input_hash(self.artifact(step).model_dump(mode="json", by_alias=True))
                         and (not dependencies_ready or (state.retained_input_hash or state.input_hash) == self._fingerprint(step)))
            except (OSError, ValueError):
                valid = False
            if not valid:
                self._invalidate(manifest, step, include_self=True)
                break
        from .review_changes import recover_saved_storyboard_approval
        recovered = recover_saved_storyboard_approval(self, manifest)
        self._set_status(manifest)
        manifest.approved_steps = [gate for gate in ("story", "storyboard") if gate in manifest.approvals]
        self.manager.save(manifest)
        if recovered:
            approval = manifest.approvals["storyboard"]
            self._event("storyboard", "approved", content_hash=approval.content_hash, note=approval.note, recovered=True)
        prompts = manifest.steps.get("prompts")
        prompt_approval = manifest.content_approvals.get("prompts")
        from .animation_prompt_preparation import preparation_current
        if (manifest.world_project_id and "storyboard" in manifest.approvals
                and (not prompts or prompts.status not in ("running","failed"))
                and (not prompts or prompts.status != "succeeded" or not prompt_approval
                     or prompt_approval.content_hash != prompts.output_hash or not preparation_current(self,manifest))):
            from .animation_prompt_preparation import prepare_locked
            try:
                manifest = prepare_locked(self)
            except (OSError,ValueError,RuntimeError):
                # Keep the approved storyboard visible and expose a retry on
                # its tab if local prompt preparation fails.
                manifest = self._load()
        return manifest

    def _set_status(self, manifest):
        succeeded = lambda step: manifest.steps.get(step, StepState()).status == "succeeded"
        if manifest.asset_state == "approved" and succeeded("prompts") and {"story", "storyboard"} <= manifest.approvals.keys():
            manifest.status = ProjectStatus.RENDERED if manifest.render_state in ("review", "approved") else ProjectStatus.ASSETS_READY
        elif succeeded("storyboard"):
            manifest.status = ProjectStatus.STORYBOARD_GENERATED
        elif "story" in manifest.approvals:
            manifest.status = ProjectStatus.STORY_APPROVED
        elif succeeded("story"):
            manifest.status = ProjectStatus.STORY_GENERATED
        else:
            manifest.status = ProjectStatus.DRAFT
        manifest.current_step = "complete"
        for step in STEPS:
            if step == "script" and "story" not in manifest.approvals:
                manifest.current_step = "story_review" if succeeded("review") else "review"
                break
            if step == "prompts" and "storyboard" not in manifest.approvals:
                manifest.current_step = "storyboard_review" if succeeded("storyboard") else "storyboard"
                break
            if not succeeded(step):
                manifest.current_step = step
                break

    def inspect(self):
        with project_lock(self.path):
            return self._refresh()

    def _gate(self, step: str, manifest):
        if manifest.workflow_version == "world_episode":
            from .studio import WorldStudio, approved_content
            world = WorldStudio(self.manager, manifest.world_project_id or self.project_id)
            if step == "world" and world.load().approval:
                raise ApprovalRequired("Approved World settings are immutable. Add additional settings on World.")
            if step != "world":
                world.require_approved()
            if not manifest.world_project_id and step not in ("world", "characters"):
                raise ApprovalRequired("Create or select an episode on Story/Episode before generating story content.")
            if manifest.world_project_id:
                if step in ("world", "characters"):
                    raise ApprovalRequired("Shared World and Characters are managed on World and Assets.")
                if not approved_content(self, "outline"):
                    raise ApprovalRequired("Approve this episode's Outline before generating story content.")
                if step == "storyboard" and not approved_content(self, "script"):
                    raise ApprovalRequired("Review and approve Script before generating Storyboard.")
        if STEPS.index(step) >= STEPS.index("script") and "story" not in manifest.approvals:
            raise ApprovalRequired("Complete AI Review (Story): apply or revert every difference, then save and approve before continuing.")
        if step == "prompts" and "storyboard" not in manifest.approvals:
            raise ApprovalRequired("Review scenes.json, then approve storyboard before generating prompts.")

    def _write_outputs(self, step: str, output: Model):
        module = MODULES[step]
        atomic_write(self.path / module.filename, output.model_dump_json(by_alias=True, indent=2) + "\n")
        if isinstance(output, Characters):
            # Keep individual character files in sync with the authoritative bible.
            ids = {c.character_id for c in output.characters}
            for existing in (self.path / "characters").glob("*.json"):
                if existing.stem not in ids:
                    existing.unlink()
            for character in output.characters:
                atomic_write(self.path / "characters" / f"{character.character_id}.json",
                             character.model_dump_json(by_alias=True, indent=2) + "\n")
        elif isinstance(output, Script):
            from .animation_models import Performance
            intent = [Performance(scene_id=scene.scene_id, line_index=index, character_id=line.character_id,
                                  text=line.text, audio_tags=line.audio_tags, subtext=f"Draft scene purpose: {scene.purpose}", body_action=scene.action)
                      for scene in output.scenes for index, line in enumerate(scene.dialogue)]
            atomic_write(self.path / "performance_intent.json", json.dumps({"status": "draft",
                "scriptHash": input_hash(output.model_dump(mode="json", by_alias=True)),
                "performances": [line.model_dump(mode="json", by_alias=True) for line in intent]}, ensure_ascii=False, indent=2))
        elif isinstance(output, Prompts):
            for kind in ("image", "video"):
                data = [{"sceneId": p.scene_id, "provider": p.provider, "model": p.model,
                         "prompt": getattr(p, f"{kind}_prompt"), "negativePrompt": p.negative_prompt}
                        for p in output.prompts]
                atomic_write(self.path / "prompts" / f"{kind}_prompts.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def export_storyboard_markdown(self):
        """Render an on-demand export without persisting a second source of truth."""
        with project_lock(self.path):
            from .location_labels import location_labels
            return storyboard_markdown(self.artifact("storyboard"),location_labels(self.path))

    def _commit(self, step: str, output: Model, fingerprint: str):
        manifest = self._load()
        state = manifest.steps.setdefault(step, StepState())
        # Every successful version has an immutable canonical snapshot.
        revision = state.revision + 1
        snapshot = self.path / "history" / step / f"{revision:06d}.json"
        while snapshot.exists():
            revision += 1
            snapshot = self.path / "history" / step / f"{revision:06d}.json"
        atomic_write(snapshot, output.model_dump_json(by_alias=True, indent=2) + "\n")
        self._write_outputs(step, output)
        self._invalidate(manifest, step, include_self=False)
        manifest.content_approvals.pop(step, None)
        state.status = "succeeded"
        state.input_hash = fingerprint
        state.retained_input_hash = ""
        state.output_hash = input_hash(output.model_dump(mode="json", by_alias=True))
        state.revision = revision
        state.error = None
        state.updated_at = utc_now()
        manifest.provider_config["llm"] = self.config
        self._set_status(manifest)
        self.manager.save(manifest)
        self._event(step, "succeeded", revision=revision)

    def _event(self, step: str, status: str, **details):
        path = self.path / "logs" / "pipeline.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": utc_now().isoformat(), "step": step, "status": status, **details}, ensure_ascii=False) + "\n")
        if self.on_progress is not None:
            try:
                self.on_progress(self._load())
            except Exception:
                logger.warning("Could not update workflow progress display")

    def _run_step(self, step: str, *, force=False):
        manifest = self._refresh()
        self._gate(step, manifest)
        for dependency in MODULES[step].dependencies:
            if manifest.steps.get(dependency, StepState()).status != "succeeded":
                raise ValueError(f"Generate {dependency} before {step}")
        if not force and manifest.steps.get(step, StepState()).status == "succeeded":
            self._write_outputs(step, self.artifact(step))
            return
        # Pin the actual model only when generating a new revision. Editing future
        # preferences must not invalidate completed or approved artifacts.
        if not self.demo:
            from .generation_preferences import openai_config
            manifest.provider_config[f"llm:{step}"] = openai_config(self, step)
            self.manager.save(manifest)
        context = self._context(step,fresh_assets=step == "storyboard")
        fingerprint = self._fingerprint(step,context=context)
        self._invalidate(manifest, step, include_self=True)
        state = manifest.steps.setdefault(step, StepState())
        state.status = "running"
        state.error = None
        self._set_status(manifest)
        manifest.current_step = step
        manifest.provider_config["llm"] = self.config
        self.manager.save(manifest)
        self._event(step, "running")
        try:
            if step == "prompts" and f"llm:{step}" not in self._load().provider_config:
                output = visual_prompt_adapter(self.settings.prompt_provider).generate(
                    context, self.settings.prompt_provider, self.settings.prompt_model)
            else:
                output = self._generate(step, context)
            validate_output(step, output, context)
            if step == "storyboard":
                atomic_write(self.path/"storyboard_reference_context.json",json.dumps({key:context[key] for key in ("sharedBackgrounds","detailBackgrounds","locationTypes","assetCatalog")},ensure_ascii=False,indent=2))
            self._commit(step, output, fingerprint)
        except Exception as exc:
            manifest = self._load()
            state = manifest.steps[step]
            state.status = "failed"
            state.error = str(exc)
            state.updated_at = utc_now()
            manifest.current_step = step
            self.manager.save(manifest)
            self._event(step, "failed", error=state.error)
            logger.error("Project %s module %s failed: %s", self.project_id, step, exc)
            raise

    def _generate(self, step: str, context: dict):
        override = self._load().provider_config.get(f"llm:{step}")
        config = override or self.config
        provider = (self.provider if self._custom_provider else
                    OpenAILLMProvider(timeout=self.settings.llm_timeout_sec) if override and override.provider != "demo" else
                    DemoLLMProvider() if override and override.provider == "demo" and not self.demo else self.provider)
        if step == "prompts":
            context = {**context, "promptTarget": {"provider": self.settings.prompt_provider,
                                                   "model": self.settings.prompt_model}}
        for attempt in range(self.settings.llm_max_retries + 1):
            manifest = self._load()
            manifest.steps[step].attempts += 1
            self.manager.save(manifest)
            outcome = "failed"
            try:
                def validate(output):
                    output = MODULES[step].model.model_validate(output.model_dump())
                    validate_output(step, output, context)
                    return output
                output = tracked_call(self.manager, self.project_id, step, provider.generate, build_prompt(step, context),
                                      MODULES[step].model, config, validate_result=validate)
                outcome = "succeeded"
                return output
            except TransientProviderError:
                if attempt >= self.settings.llm_max_retries:
                    raise
                time.sleep(min(2 ** attempt, 8))
            finally:
                usage = dict(getattr(provider, "last_usage", {}) or {})
                cost, known = ((0, True) if config.provider == "demo" else token_cost(
                    usage, self.settings.input_cost_per_million, self.settings.output_cost_per_million,
                    self.settings.cached_input_cost_per_million))
                CostTracker(self.manager, self.project_id).record(UsageRecord(
                    provider=config.provider, model=config.model, step=step, **request_fields(usage),
                    input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                    retry_count=attempt, cost=cost, estimated_cost=cost, pricing_known=known, outcome=outcome))

    def run(self):
        """Resume until the next human gate or completion; never auto-approve."""
        with project_lock(self.path):
            for step in STEPS:
                manifest = self._refresh()
                if manifest.workflow_version == "world_episode":
                    if manifest.world_project_id and step in ("world", "characters"):
                        continue
                    if not manifest.world_project_id and step == "world" and "world" in manifest.content_approvals:
                        continue
                try:
                    self._gate(step, manifest)
                except ApprovalRequired:
                    return manifest
                self._run_step(step)
            return self._refresh()

    def generate(self, step: str, *, force=False):
        if step not in MODULES:
            raise ValueError(f"Unknown module: {step}")
        with project_lock(self.path):
            self._run_step(step, force=force)
            result = self._refresh()
        self._sync_storyboard_assets(step)
        return result

    def _sync_storyboard_assets(self,step):
        manifest = self._load()
        if step == "storyboard" and manifest.workflow_version == "world_episode" and manifest.world_project_id:
            from .storyboard_assets import sync
            sync(self.manager,manifest.world_project_id)

    def regenerate_with_openai(self, step: str):
        if step not in MODULES:
            raise ValueError(f"Unknown module: {step}")
        with project_lock(self.path):
            manifest = self._refresh()
            self._gate(step, manifest)
            for dependency in MODULES[step].dependencies:
                if manifest.steps.get(dependency, StepState()).status != "succeeded":
                    raise ValueError(f"Generate {dependency} before {step}")
            from .generation_preferences import openai_config
            manifest.provider_config[f"llm:{step}"] = openai_config(self, step, force=True)
            self.manager.save(manifest)
            self._run_step(step, force=True)
            result = self._refresh()
        self._sync_storyboard_assets(step)
        return result

    def modify_with_openai(self, step, instructions, *, expected=None):
        from .modification import modify_content, OutlineEdit
        if step not in MODULES and step != "outline":
            raise ValueError("Unknown content to modify")
        if step == "outline" and self._load().workflow_version == "world_episode":
            raise ValueError("Use Modify with prompt on World or the episode Outline; review suggestions before saving.")
        with project_lock(self.path):
            if step == "outline":
                old = self.manager.read_outline(self.project_id)
                if expected is not None and old != expected:
                    raise ValueError("The outline changed. Reopen Modify with OpenAI.")
                output = modify_content(self, OutlineEdit(text=old), instructions, step)
                write_document(self.path / "history" / "outline" / f"{input_hash(old)}.json", old)
                write_document(self.path / "outline.json", output.text)
                manifest = self._load()
                self._invalidate(manifest, "world", include_self=True)
                self._set_status(manifest)
                self.manager.save(manifest)
            else:
                manifest = self._refresh()
                self._gate(step, manifest)
                path = self.path / MODULES[step].filename
                if expected is not None and path.read_text(encoding="utf-8-sig") != expected:
                    raise ValueError("The content changed. Reopen Modify with OpenAI.")
                for dependency in MODULES[step].dependencies:
                    if manifest.steps.get(dependency, StepState()).status != "succeeded":
                        raise ValueError(f"Generate {dependency} before modifying {step}")
                context = self._context(step)
                output = modify_content(self, self.artifact(step), instructions, step, context=context)
                validate_output(step, output, context)
                self._commit(step, output, self._fingerprint(step))
            self._event(step, "modified_with_openai", prompt=instructions)
            return self._refresh()

    def edit(self, step: str, text: str):
        """Validate before writing; reprocess only explicit dependents and approvals."""
        if step == "outline":
            manifest = self._load()
            if manifest.workflow_version == "world_episode":
                if manifest.world_project_id:
                    from .studio import Episodes
                    return Episodes(self.manager, manifest.world_project_id).save_outline(
                        self.project_id, manifest.title, text, manifest.target_duration_sec)
                from .studio import WorldStudio
                return WorldStudio(self.manager, self.project_id).edit_base(text)
            if not text.strip():
                raise ValueError("Story outline must not be empty")
            with project_lock(self.path):
                old = self.manager.read_outline(self.project_id)
                archive = self.path / "history" / "outline" / f"{input_hash(old)}.json"
                write_document(archive, old)
                write_document(self.path / "outline.json", text)
                manifest = self._load()
                self._invalidate(manifest, "world", include_self=True)
                self._set_status(manifest)
                self.manager.save(manifest)
            return
        if step not in MODULES:
            raise ValueError(f"Unknown module: {step}")
        output = MODULES[step].model.model_validate_json(text)
        with project_lock(self.path):
            manifest = self._refresh()
            self._gate(step, manifest)
            for dependency in MODULES[step].dependencies:
                if manifest.steps.get(dependency, StepState()).status != "succeeded":
                    raise ValueError(f"Generate {dependency} before editing {step}")
            validate_output(step, output, self._context(step))
            self._commit(step, output, self._fingerprint(step))

    def edit_character(self, character_id: str, text: str, *, expected: str):
        """Merge one character draft without overwriting other characters."""
        edited = Character.model_validate_json(text)
        if edited.character_id != character_id:
            raise ValueError("Keep the characterId unchanged when editing a character asset.")
        path = self.path / MODULES["characters"].filename
        source = path.read_text(encoding="utf-8-sig")
        cast = Characters.model_validate_json(source)
        index = next((i for i, c in enumerate(cast.characters) if c.character_id == character_id), None)
        if index is None:
            raise ValueError("This character no longer exists. Reopen the Assets page.")
        if cast.characters[index] != Character.model_validate_json(expected):
            raise ValueError("This character changed in another editor. Reload its saved JSON before saving.")
        cast.characters[index] = edited
        self.edit_bundle({"characters": cast.model_dump_json(by_alias=True)}, expected={"characters": source})
        manifest = self._load()
        if manifest.workflow_version == "world_episode" and not manifest.world_project_id:
            from .identity import IdentityManager
            IdentityManager(self.manager, self.project_id).prepare()

    def edit_bundle(self, changes: dict[str, str], *, expected: dict[str, str], preserve_storyboard_approval=False, approve_storyboard_edit=False):
        """Validate a dialog's draft before saving; reject concurrent changes."""
        allowed = {"project", "outline", *STEPS}
        if not set(changes) <= allowed or not set(expected) <= allowed:
            raise ValueError("Unknown content in stage edit")
        retain_approval = preserve_storyboard_approval or approve_storyboard_edit
        if retain_approval and set(changes) != {"storyboard"}:
            raise ValueError("Approval retention is only supported for a scoped storyboard edit")
        with project_lock(self.path):
            manifest = self._refresh() if retain_approval else self._load()
            retained_approval = manifest.approvals.get("storyboard") if retain_approval else None
            approval_steps = STEPS[:STEPS.index("storyboard") + 1]
            if retained_approval and (any(name not in manifest.steps or manifest.steps[name].status != "succeeded" for name in approval_steps)
                    or retained_approval.content_hash != input_hash({name:manifest.steps[name].output_hash for name in approval_steps})):
                retained_approval = None
            if approve_storyboard_edit and not retained_approval:
                raise ApprovalRequired("Approve the storyboard on Story/Episode before editing it on Scene images.")
            if manifest.workflow_version == "world_episode" and ("outline" in changes or "project" in changes):
                raise ValueError("Edit World settings on World, or episode title/outline/length on Story/Episode.")

            def read(name):
                if name == "project":
                    return json.dumps({"title": manifest.title, "language": manifest.language,
                                       "targetDurationSec": manifest.target_duration_sec}, ensure_ascii=False, indent=2)
                if name == "outline":
                    return self.manager.read_outline(self.project_id)
                path = self.path / MODULES[name].filename
                return path.read_text(encoding="utf-8-sig")

            for name, baseline in expected.items():
                if read(name) != baseline:
                    raise ValueError("Content changed in another session. Close and reopen Browse before saving.")
            if not changes:
                return
            metadata = json.loads(changes.get("project", read("project")))
            if not isinstance(metadata, dict) or set(metadata) != {"title", "language", "targetDurationSec"}:
                raise ValueError("Project fields must be title, language, and targetDurationSec")
            checked = manifest.model_copy(deep=True)
            checked.title = metadata["title"]
            checked.language = metadata["language"]
            checked.target_duration_sec = metadata["targetDurationSec"]
            outline = changes.get("outline", read("outline"))
            if not outline.strip():
                raise ValueError("Story outline must not be empty")
            parsed = {name: MODULES[name].model.model_validate_json(text)
                      for name, text in changes.items() if name in MODULES}
            if "storyboard" in parsed:
                from .storyboard_editor import reconcile_detail_requirements
                board = parsed["storyboard"].model_dump(mode="json",by_alias=True)
                board["detailBackgrounds"] = reconcile_detail_requirements(board,{})
                parsed["storyboard"] = MODULES["storyboard"].model.model_validate(board)
            # Check every changed module against the draft inputs, without writing files.
            for name, output in parsed.items():
                context = {"title": checked.title, "language": checked.language,
                           "targetDurationSec": checked.target_duration_sec, "outline": outline}
                self._gate(name, manifest)
                for dependency in MODULES[name].dependencies:
                    source = parsed.get(dependency) or self.artifact(dependency)
                    context[dependency] = source.model_dump(mode="json", by_alias=True)
                # A user may shorten a storyboard by deleting a scene. Keep
                # generation's target-length constraint, but allow manual timing.
                if name == "storyboard":
                    context["manualStoryboardEdit"] = True
                validate_output(name, output, context)
            if "outline" in changes:
                write_document(self.path / "history" / "outline" / f"{input_hash(read('outline'))}.json", read("outline"))
                write_document(self.path / "outline.json", outline)
            if "project" in changes or "outline" in changes:
                self._invalidate(checked, "world", include_self=True)
                self._set_status(checked)
                self.manager.save(checked)
            for name in STEPS:
                if name in parsed:
                    self._commit(name, parsed[name], self._fingerprint(name))
            # An edited stage stays stale when unchanged upstream dependencies need regeneration.
            updated = self._refresh()
            if retained_approval and all(updated.steps[name].status == "succeeded" for name in approval_steps):
                digest = input_hash({name:updated.steps[name].output_hash for name in approval_steps})
                updated.approvals["storyboard"] = retained_approval.model_copy(update={"content_hash":digest,
                    "note":"Scene-image storyboard edit saved and approved by the user" if approve_storyboard_edit else
                        "Existing storyboard approval retained after scoped prop removal; dialogue and story beats preserved"})
                updated.approved_steps = [gate for gate in ("story","storyboard") if gate in updated.approvals]
                self._set_status(updated)
                self.manager.save(updated)
                self._event("storyboard","approval_retained",content_hash=digest,previous_content_hash=retained_approval.content_hash)
                if approve_storyboard_edit and updated.world_project_id:
                    from .animation_prompt_preparation import prepare_locked
                    prepare_locked(self)

    def approve(self, gate: str, note: str = ""):
        with project_lock(self.path):
            return self._approve(gate, note)

    def _approve(self, gate: str, note: str = ""):
        """Approval under the caller's project lock (also used by review saves)."""
        if gate not in ("story", "storyboard"):
            raise ValueError("Approval gate must be story or storyboard")
        manifest = self._refresh()
        required = STEPS[:STEPS.index("review") + 1] if gate == "story" else STEPS[:STEPS.index("storyboard") + 1]
        if any(manifest.steps.get(step, StepState()).status != "succeeded" for step in required):
            raise ValueError(f"All modules through {required[-1]} must be current before approval")
        if gate == "storyboard" and "story" not in manifest.approvals:
            raise ApprovalRequired("Approve story before storyboard")
        from .review_changes import resolution_current
        if gate == "story" and self.artifact("review").issues and not resolution_current(self, "story"):
            raise ApprovalRequired("Apply or revert every AI Review (Story) difference, then save and approve there")
        if gate == "storyboard" and (self.path / "dialogue_review.json").exists():
            from .dialogue_review import SavedDialogueReview, dialogue_context
            report = SavedDialogueReview.model_validate_json((self.path / "dialogue_review.json").read_text(encoding="utf-8"))
            resolved = resolution_current(self, "storyboard")
            if report.source_hash != input_hash(dialogue_context(self)) and not resolved:
                raise ApprovalRequired("Review the current dialogue again before approving storyboard")
            if report.report.issues and not resolved:
                raise ApprovalRequired("Apply or revert every AI Review (Storyboard) difference, then save and approve the review")
        digest = input_hash({step: manifest.steps[step].output_hash for step in required})
        manifest.approvals[gate] = ApprovalRecord(content_hash=digest, note=note)
        manifest.approved_steps = [key for key in ("story", "storyboard") if key in manifest.approvals]
        self._set_status(manifest)
        self.manager.save(manifest)
        self._event(gate, "approved", content_hash=digest, note=note)
        if gate == "storyboard" and manifest.world_project_id:
            from .animation_prompt_preparation import prepare_locked
            manifest = prepare_locked(self)
        return manifest

    def prepare_animation_prompts(self):
        """Retry local prompt preparation without reopening storyboard review."""
        with project_lock(self.path):
            self._refresh()
            from .animation_prompt_preparation import prepare_locked
            return prepare_locked(self)

    def save_to_library(self, step: str, name: str) -> Path:
        if step not in ("world", "characters"):
            raise ValueError("Only world and characters can be saved to the shared library")
        from pydantic import TypeAdapter
        from .models import Identifier
        TypeAdapter(Identifier).validate_python(name)
        with project_lock(self.path):
            manifest = self._refresh()
            if manifest.steps.get(step, StepState()).status != "succeeded":
                raise ValueError("Generate or import current content before saving to the library")
            directory = self.manager.root / ("01_world" if step == "world" else "02_characters")
            path = directory / f"{name}.json"
            if path.exists():
                raise FileExistsError(f"Library entry already exists: {name}; choose a new name")
            atomic_write(path, self.artifact(step).model_dump_json(by_alias=True, indent=2) + "\n")
            return path

    def use_library(self, step: str, name: str):
        if step not in ("world", "characters"):
            raise ValueError("Only world and characters can be imported from the shared library")
        from pydantic import TypeAdapter
        from .models import Identifier
        TypeAdapter(Identifier).validate_python(name)
        directory = self.manager.root / ("01_world" if step == "world" else "02_characters")
        self.edit(step, (directory / f"{name}.json").read_text(encoding="utf-8-sig"))
