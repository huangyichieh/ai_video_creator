"""Reusable prop/costume visuals and character talking samples, using existing adapters."""
import json
from pathlib import Path
from uuid import uuid4
from decimal import Decimal

from .files import atomic_write, read_text, input_hash
from .models import ProviderConfig, UsageRecord
from .request_tracking import tracked_call, request_fields
from .costs import CostTracker
from .locking import project_lock
from .ledger_lock import ledger_lock
from .assets import AssetPipeline
from .studio import WorldStudio
from .media_utils import file_hash
from .dual_panorama import PANORAMA_MODEL as PROP_IMAGE_MODEL
from .visual_generation import DESIGN_MODEL as CHARACTER_IMAGE_MODEL


class LibraryMedia:
    @property
    def stop_requested(self):
        from threading import local
        context = self.__dict__.setdefault("_stop_context", local())
        return getattr(context, "callback", lambda: False)

    @stop_requested.setter
    def stop_requested(self, callback):
        from threading import local
        self.__dict__.setdefault("_stop_context", local()).callback = callback

    def __init__(self, manager, project_id):
        self.manager, self.project_id = manager, project_id
        self.assets = AssetPipeline(manager, project_id)
        self.path = self.assets.path
        self.file = self.path / "library_media.json"
        self.stop_requested = lambda: False

    def load(self):
        from .file_cache import read_json
        state = read_json(self.file) if self.file.exists() else {"props": {}, "visuals": {}, "samples": {}}
        state.setdefault("voiceApprovals", {})
        state.setdefault("activeVisuals", {})
        state.setdefault("activeSamples", {})
        return state

    def selected_samples(self, cid, profile=None, state=None):
        from .identity import IdentityManager
        from .library_revisions import selected_samples
        profile = profile or IdentityManager(self.manager, self.project_id).load().voices[cid]
        return selected_samples(self, state if state is not None else self.load(), cid, profile)

    def apply_costume_revision(self, character_id, costume_id, revision):
        from .library_revisions import apply_costume
        return apply_costume(self, character_id, costume_id, revision)

    def apply_voice_revision(self, character_id, revision):
        from .library_revisions import apply_voice
        return apply_voice(self, character_id, revision)

    def delete_voice_revision(self, character_id, revision, *, expected=None):
        from .revision_deletion import delete_library
        return delete_library(self, character_id, revision, expected=expected)

    def delete_costume_revision(self, character_id, costume_id, revision, *, expected=None):
        from .revision_deletion import delete_library
        return delete_library(self, character_id, revision, costume_id=costume_id, expected=expected)

    def save(self, state):
        with ledger_lock(self.path):
            atomic_write(self.file, json.dumps(state, ensure_ascii=False, indent=2))

    def sample_matches_provider(self, sample):
        asset = sample.get("audioAsset", {})
        return (asset.get("provider") == ("demo" if self.assets.demo else self.manager.settings.tts_provider)
                and asset.get("model") == ("demo-voice" if self.assets.demo else self.manager.settings.tts_model))

    def add_prop(self, title, description, modes):
        WorldStudio(self.manager, self.project_id).require_approved()
        if not title.strip() or not description.strip():
            raise ValueError("Enter a prop title and description.")
        with project_lock(self.path), ledger_lock(self.path):
            state = self.load()
            pid = "prop_" + uuid4().hex[:12]
            state["props"][pid] = {"title": title, "description": description,
                                    "modes": list(dict.fromkeys(m.strip() for m in modes if m.strip())) or ["default"]}
            self.save(state)
            # Register it in the existing identity library for shot references.
            from .identity import IdentityManager, Prop
            identity = IdentityManager(self.manager, self.project_id)
            library = identity.load()
            if library:
                library.props[pid] = Prop(prop_id=pid, description=description)
                library.approval = None
                identity._write(library)
            return pid

    def _call(self, provider, callback, args, config, stage):
        if stage.startswith("library:voice_sample:"):
            from .style_rules import audio_rules
            rules = audio_rules(self.assets.load().project_style_rules, kind="voice")
            if rules:
                config.options["project_style_rules"] = rules
        outcome = "failed"
        try:
            result = tracked_call(self.manager, self.project_id, stage, callback, *args, config)
            outcome = "succeeded"
            return result
        finally:
            usage = dict(getattr(provider, "last_usage", {}) or {})
            settings = self.manager.settings
            image_rate = settings.runway_image_cost_per_image if config.provider == "runway" else settings.image_cost_per_image
            video_rate = settings.runway_video_cost_per_second if config.provider == "runway" else settings.video_cost_per_second
            voice_rate = settings.elevenlabs_tts_cost_per_million_characters if config.provider == "elevenlabs" else settings.tts_cost_per_million_characters
            cost = Decimal(0)
            for rate, key, divisor in ((image_rate, "image_count", 1), (video_rate, "video_seconds", 1), (voice_rate, "audio_characters", 1000000)):
                if rate is not None:
                    cost += rate * Decimal(str(usage.get(key, 0))) / divisor
            CostTracker(self.manager, self.project_id).record(UsageRecord(provider=config.provider, model=config.model, step=stage,
                image_count=usage.get("image_count", 0), video_seconds=usage.get("video_seconds", 0), audio_characters=usage.get("audio_characters", 0),
                cost=cost, estimated_cost=cost, pricing_known=config.provider in ("demo", "local") or any(rate is not None and key in usage for rate, key in ((image_rate, "image_count"), (video_rate, "video_seconds"), (voice_rate, "audio_characters"))),
                outcome=outcome, **request_fields(usage)))

    def image(self, key, prompt, references=(), *, size=None):
        WorldStudio(self.manager, self.project_id).require_approved()
        with project_lock(self.path):
            return self._image_locked(key, prompt, references, size=size)

    def _image_locked(self, key, prompt, references=(), *, source_hash="", size=None):
        is_prop = key.startswith("prop:")
        if is_prop and size not in (None,"1024x1024","1536x1024"):
            raise ValueError("Choose a supported prop image resolution.")
        if key.startswith("prop:"):
            _,pid,mode = key.split(":",2)
            current = self.load()
            if pid in current.get("removedProps",[]) or mode in current.get("removedPropModes",{}).get(pid,[]):
                raise ValueError("This prop or mode was removed. Save a new draft before generating it again.")
        work = self.path / "images" / "library" / uuid4().hex
        work.mkdir(parents=True)
        settings = self.manager.settings
        source = self.assets.load()
        if self.assets.shared_style_enabled(source) and source.generic_prompt.strip():
            prompt = prompt + "\nShared art direction: " + source.generic_prompt
        from .style_rules import apply_rules
        prompt = apply_rules(prompt, source.project_style_rules)
        provider = self.assets.panorama_image_provider if is_prop else self.assets.character_image_provider
        config = ProviderConfig(provider="demo" if self.assets.demo else "openai",
            model=PROP_IMAGE_MODEL if is_prop else CHARACTER_IMAGE_MODEL,
            options={"output_path": str(work / "image.png"), "size": (size or "1024x1024") if is_prop else settings.image_size, "quality": settings.image_quality})
        refs = self.assets._style_references(source) + list(references)
        from .style_rules import style_reference, reference_tags
        prompt, refs = style_reference(self.assets, source, prompt, refs)
        if is_prop:
            prompt += ("\nMANDATORY PROP PRESENTATION — overrides conflicting background, scene or composition directions above: "
                "Show ONLY the requested prop in its specified mode, fully visible, centered and isolated against a clean, "
                "uniform pure white (#FFFFFF) background. No scenery, room, floor, tabletop, pedestal, people, hands, "
                "unrelated objects, captions, labels, watermarks, gradients or cast shadows. "
                "Use style references and shared art direction only for the prop's own materials, colors, linework, texture and shading; "
                "never copy their backgrounds or additional objects. Keep the background pure white to every image edge.")
        tags = reference_tags(self.assets, source, refs)
        if tags:
            config.options["reference_tags"] = tags
        asset = self._call(provider, provider.generate, (prompt, refs), config, "library:image:" + key)
        if is_prop:
            asset.generation_params["requestedSize"] = config.options["size"]
        asset.file_path = Path(asset.file_path).relative_to(self.path).as_posix()
        with ledger_lock(self.path):
            state = self.load()
            versions = state["visuals"].setdefault(key, [])
            versions.append({"revision": len(versions) + 1, "prompt": prompt, "sourceHash": source_hash,
                             "fileHash": file_hash(self.assets.local_path(asset.file_path)), "asset": asset.model_dump(mode="json", by_alias=True)})
            state["activeVisuals"][key] = {"revision": versions[-1]["revision"], "sourceHash": source_hash}
            self.save(state)
        return asset

    def _costume_source(self, character_id, costume_id, *, state=None):
        from .identity import IdentityManager
        identity = IdentityManager(self.manager, self.project_id)
        library = identity.load()
        if library is None:
            raise ValueError("Create the character before generating its costume visual.")
        identity.validate(library)
        costume = library.costumes.get(costume_id)
        if costume is None or costume.character_id != character_id:
            raise ValueError("Choose a costume belonging to this character.")
        character = next(c for c in self.assets.creative.artifact("characters").characters if c.character_id == character_id)
        state = state if state is not None else self.assets.load()
        task = next(t for t in self.assets.tasks(state) if t.stage == "character_references" and t.subject == character_id)
        record = state.records.get(task.key)
        if not self.assets.record_current(record, task, state):
            raise ValueError("Generate or apply a current character model sheet first.")
        return costume, character, record, state

    def costume_signature(self, character_id, costume_id, *, state=None):
        costume, character, record, state = self._costume_source(character_id, costume_id, state=state)
        return input_hash({**({"projectStyleRules": state.project_style_rules} if state.project_style_rules else {}),
            **({"projectStyleImage": state.project_style_image} if state.project_style_image else {}),
            "costume": costume.model_dump(mode="json"), "character": character.model_dump(mode="json"),
            "identityImage": [record.asset.asset_id, record.file_hash],
            "style": state.generic_prompt if self.assets.shared_style_enabled(state) else "",
            "styleImages": state.style_references if self.assets.shared_style_enabled(state) else [],
            "provider": "demo" if self.assets.demo else "openai",
            "model": CHARACTER_IMAGE_MODEL, "size": self.manager.settings.image_size,
            "quality": self.manager.settings.image_quality, "version": "costume-model-sheet-v1"})

    def generate_costume(self, character_id, costume_id, *, expected=None):
        WorldStudio(self.manager, self.project_id).require_approved()
        with project_lock(self.path):
            costume, character, record, state = self._costume_source(character_id, costume_id)
            if expected is not None and costume.model_dump_json(by_alias=True) != expected:
                raise ValueError("The costume changed. Review its saved description before generating.")
            signature = self.costume_signature(character_id, costume_id)
            prompt = ("Create a clean character COSTUME MODEL SHEET on a neutral background: matching full-body front, side, back "
                "and three-quarter views at the same scale, consistent across every panel. No text, labels or watermarks. "
                "Use the supplied character reference sheet for face, hair, anatomy and proportions. Replace its clothing with "
                "ONLY this costume, identical in every view: " + costume.description + ". Character: " + character.name + ".")
            return self._image_locked("costume:" + costume_id, prompt,
                [self.assets.local_path(record.asset.file_path)], source_hash=signature)

    def talking_sample(self, character_id, text, emotion="neutral", *, video=True):
        WorldStudio(self.manager, self.project_id).require_approved()
        from .identity import IdentityManager
        from .voice_validation import validate_provider_voice
        from .ffmpeg_renderer import FFmpeg
        from .render_models import RenderOptions
        from .balanced_providers import KlingVideoProvider
        from .video_providers import LocalVideoProvider
        library = IdentityManager(self.manager, self.project_id).load()
        profile = library.voices[character_id]
        if not profile.voice_id.strip():
            raise ValueError("Set and save the character's provider Voice ID first.")
        validate_provider_voice(self.manager, self.project_id, profile.voice_id, speaker=character_id, demo=self.assets.demo)
        record = self.assets.load().records.get("character_references:" + character_id)
        if video and (not record or not record.asset or not self.assets._intact(record)):
            raise ValueError("Generate a current character reference first.")
        if not text.strip():
            raise ValueError("Enter sample speech.")
        work = self.path / "videos" / "character_samples" / uuid4().hex
        work.mkdir(parents=True)
        settings = self.manager.settings
        config = ProviderConfig(provider="demo" if self.assets.demo else settings.tts_provider, model=settings.tts_model,
            options={"output_path": str(work / "speech.wav"), "voice_profile": profile.model_copy(update={"baseline_emotion": emotion}).model_dump(mode="json")})
        from .media_utils import file_hash
        from copy import deepcopy
        state = self.load()
        signature = input_hash(profile.model_dump(mode="json"))
        reusable = next((sample for sample in self.selected_samples(character_id, profile, state)
            if video and sample.get("profileHash") == signature and sample["text"] == text and sample["emotion"] == emotion
            and self.sample_matches_provider(sample)
            and self.assets.local_path(sample["audio"]).exists()
            and file_hash(self.assets.local_path(sample["audio"])) == sample.get("fileHash")), None)
        if reusable:
            result = {key: deepcopy(reusable[key]) for key in ("text", "emotion", "voiceId", "audio", "profileHash", "audioAsset", "fileHash")}
            audio_path = self.assets.local_path(result["audio"])
        else:
            audio = self._call(self.assets.tts_provider, self.assets.tts_provider.generate,
                               (text, profile.voice_id), config, "library:voice_sample:" + character_id)
            audio_path = Path(audio.file_path)
            result = {"text": text, "emotion": emotion, "voiceId": profile.voice_id,
                      "audio": audio_path.relative_to(self.path).as_posix(), "profileHash": signature,
                      "audioAsset": audio.model_dump(mode="json", by_alias=True), "fileHash": file_hash(audio_path)}
            result["audioAsset"]["filePath"] = result["audio"]
        result.update(revision=0, status="generating_video" if video else "succeeded",
                      profile=profile.model_dump(mode="json"))
        self._save_sample(character_id, result, reused_revision=reusable["revision"] if reusable else None)
        if video:
            try:
                first_frame = self.assets.local_path(record.asset.file_path)
                if not self.assets.demo and not self.stop_requested():
                    character = next(c for c in self.assets.creative.artifact("characters").characters if c.character_id == character_id)
                    portrait_prompt = ("Create ONE cinematic medium close-up of this character, facing camera. No reference sheet, grid, panels or text. "
                        "Preserve the supplied character identity, face, hair and fixed costume. Appearance: " + character.appearance
                        + ". Costume: " + character.clothing + ". Match the supplied character reference's art style.")
                    from .style_rules import apply_rules
                    portrait_prompt = apply_rules(portrait_prompt, self.assets.load().project_style_rules)
                    from .style_rules import style_reference, reference_tags
                    portrait_state = self.assets.load()
                    portrait_prompt, portrait_references = style_reference(self.assets, portrait_state, portrait_prompt,
                        self.assets._style_references(portrait_state) + [first_frame])
                    portrait_config = ProviderConfig(provider="openai", model=CHARACTER_IMAGE_MODEL,
                        options={"output_path": str(work/"portrait.png"), "size": settings.image_size, "quality": settings.image_quality})
                    tags = reference_tags(self.assets, portrait_state, portrait_references)
                    if tags:
                        portrait_config.options["reference_tags"] = tags
                    if not self.stop_requested():
                        portrait = self._call(self.assets.character_image_provider, self.assets.character_image_provider.generate,
                            (portrait_prompt, portrait_references), portrait_config,
                            "library:talking_portrait:" + character_id)
                        first_frame = Path(portrait.file_path)
                        result["portrait"] = first_frame.relative_to(self.path).as_posix()
                        self._save_sample(character_id, result)
                if self.stop_requested():
                    result["status"] = "stopped"
                    self._save_sample(character_id, result)
                    return result
                provider = LocalVideoProvider() if self.assets.demo else KlingVideoProvider(timeout=settings.llm_timeout_sec)
                prompt = ("A single character speaking naturally to camera with " + emotion + " emotion. "
                          "Subtle expressive mouth movement, eye contact, gentle head motion. Preserve supplied character identity, "
                          "costume and art style. A brief three-second sample. Static medium close-up, no text. Spoken phrase: " + text)
                from .style_rules import apply_rules
                prompt = apply_rules(prompt, self.assets.load().project_style_rules)
                if not self.assets.demo:
                    prompt = compile_prompt(self.assets.creative, prompt, video=True,
                        task_key=f"library:talking_sample:{character_id}:prompt")
                config = ProviderConfig(provider="demo" if self.assets.demo else "runway", model="ffmpeg-image-motion" if self.assets.demo else "klingO3_pro",
                    options={"output_path": str(work / "visual.mp4"), "seconds": 3, "duration": 3, "size": "1280x720",
                             "render_options": RenderOptions().model_dump(mode="json")})
                clip = self._call(provider, provider.generate_clip, (prompt, [first_frame]), config, "library:talking_sample:" + character_id)
                FFmpeg().run(["-i", clip.file_path, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0",
                              "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-t", "3", "-shortest", work / "sample.mp4"])
                result.update(video=(work / "sample.mp4").relative_to(self.path).as_posix(), prompt=prompt,
                              provider=config.provider, model=config.model, status="succeeded")
            except Exception as exc:
                result.update(status="failed", error=str(exc))
                self._save_sample(character_id, result)
                raise
        self._save_sample(character_id, result)
        return result

    def _save_sample(self, cid, result, *, reused_revision=None):
        """Merge only this sample under a short lock; never save a stale library."""
        from .identity import IdentityManager
        with ledger_lock(self.path):
            profile = IdentityManager(self.manager, self.project_id).load().voices[cid]
            if result["profileHash"] != input_hash(profile.model_dump(mode="json")):
                raise ValueError("Voice settings changed during generation. Generated audio is retained; generate again with the current voice settings.")
            state = self.load()
            samples = state["samples"].setdefault(cid, [])
            if not result["revision"]:
                result.setdefault("revisionId", uuid4().hex)
                result["revision"] = max((sample["revision"] for sample in samples), default=0) + 1
                samples.append(dict(result))
                if reused_revision is not None:
                    # A deletion may have renumbered the reusable audio while
                    # this request was preparing its video. Resolve by content.
                    reused = next((s["revision"] for s in samples if s["revision"] != result["revision"]
                                   and s.get("audio") == result["audio"] and s.get("profileHash") == result["profileHash"]
                                   and s.get("emotion") == result["emotion"]), result["revision"])
                    state["activeSamples"].setdefault(cid, {}).setdefault(result["emotion"], reused)
                else:
                    state["activeSamples"].setdefault(cid, {})[result["emotion"]] = result["revision"]
                    state["voiceApprovals"].pop(cid, None)
            else:
                index = next((i for i, sample in enumerate(samples)
                              if sample.get("revisionId") == result["revisionId"]), None) if result.get("revisionId") else next(
                    (i for i, sample in enumerate(samples) if sample["revision"] == result["revision"]
                     and sample.get("audio") == result.get("audio")), None)
                if index is None:
                    raise ValueError("This voice revision was deleted while its animation was generating. Generated files are retained.")
                result["revision"] = samples[index]["revision"]
                samples[index] = dict(result)
            self.save(state)

    def voice_approved(self, cid, profile=None, state=None):
        from .identity import IdentityManager
        from .media_utils import file_hash
        try:
            profile = profile or IdentityManager(self.manager, self.project_id).load().voices[cid]
            state = state or self.load()
            signature = input_hash(profile.model_dump(mode="json"))
            approval = state["voiceApprovals"].get(cid)
            from .library_revisions import sample_intact, sample_signature
            samples = self.selected_samples(cid, profile, state)
            return bool(approval and approval["contentHash"] == signature
                        and approval.get("selectionHash", sample_signature(samples)) == sample_signature(samples)
                        and any(sample["emotion"] == "neutral" for sample in samples)
                        and all(sample_intact(self, sample) for sample in samples))
        except (OSError, ValueError, KeyError, AttributeError):
            return False

    def voice_approval_missing(self, cid, profile=None, state=None):
        from .identity import IdentityManager
        from .library_revisions import sample_intact
        library = IdentityManager(self.manager, self.project_id).load() if profile is None else None
        profile = profile or (library.voices.get(cid) if library else None)
        if profile is None or not profile.voice_id.strip():
            return ["Save a provider Voice ID."]
        saved = self.load() if state is None else state
        samples = self.selected_samples(cid, profile, saved)
        missing = []
        if not any(sample["emotion"] == "neutral" for sample in samples):
            missing.append("Generate a neutral voice sample for the current voice settings.")
        for sample in samples:
            if not sample_intact(self, sample):
                missing.append(f"Regenerate or apply an intact {sample['emotion']} sample (revision {sample['revision']}); its audio is missing or modified.")
        return missing

    def approve_voice(self, cid):
        from .identity import IdentityManager
        from .media_utils import file_hash
        from .models import ApprovalRecord
        with project_lock(self.path), ledger_lock(self.path):
            state = self.load()
            profile = IdentityManager(self.manager, self.project_id).load().voices[cid]
            signature = input_hash(profile.model_dump(mode="json"))
            from .library_revisions import sample_signature
            samples = self.selected_samples(cid, profile, state)
            missing = self.voice_approval_missing(cid, profile, state)
            if missing:
                raise ValueError(" ".join(missing))
            state["voiceApprovals"][cid] = ApprovalRecord(content_hash=signature).model_dump(mode="json", by_alias=True)
            state["voiceApprovals"][cid]["selectionHash"] = sample_signature(samples)
            self.save(state)
            return state
