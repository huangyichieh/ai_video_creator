"""Saved library history with explicit active voice and costume selections."""
from .ledger_lock import ledger_lock
from .files import input_hash, read_text
from .media_utils import file_hash


def active_visual(state, key):
    versions = state.get("visuals", {}).get(key, [])
    selection = state.get("activeVisuals", {}).get(key)
    if selection is None:
        return versions[-1] if versions else None
    return next((v for v in versions if v["revision"] == selection["revision"]), None)


def visual_current(media, state, key, signature):
    version = active_visual(state, key)
    if not version:
        return False
    selection = state.get("activeVisuals", {}).get(key, {})
    accepted = selection.get("sourceHash", version.get("sourceHash"))
    return accepted == signature and visual_intact(media, version)


def visual_intact(media, version):
    try:
        path = media.assets.local_path(version["asset"]["filePath"])
        return bool(version.get("fileHash") and path.is_file() and file_hash(path) == version["fileHash"])
    except (OSError, ValueError, KeyError):
        return False


def selected_samples(media, state, cid, profile):
    signature = input_hash(profile.model_dump(mode="json"))
    choices = state.get("activeSamples", {}).get(cid, {})
    selected = {}
    for sample in state.get("samples", {}).get(cid, []):
        if sample.get("profileHash") != signature or not media.sample_matches_provider(sample):
            continue
        choice = choices.get(sample["emotion"])
        if choice is None or choice == sample["revision"]:
            selected[sample["emotion"]] = sample
    return list(selected.values())


def sample_intact(media, sample):
    try:
        path = media.assets.local_path(sample["audio"])
        return bool(sample.get("fileHash") and path.is_file() and file_hash(path) == sample["fileHash"])
    except (OSError, ValueError, KeyError):
        return False


def sample_profile(media, cid, sample):
    """Recover older profile snapshots from the existing identity archives too."""
    from .animation_models import VoiceProfile
    from .identity import IdentityManager

    candidates = [sample.get("profile")]
    library = IdentityManager(media.manager, media.project_id).load()
    if library and cid in library.voices:
        candidates.append(library.voices[cid].model_dump(mode="json"))
    if not sample.get("profile"):
        for path in (media.path / "history" / "identity").glob("*.json"):
            try:
                import json
                candidates.append(json.loads(read_text(path)).get("voices", {}).get(cid))
            except (OSError, ValueError):
                continue
    for candidate in candidates:
        if candidate:
            profile = VoiceProfile.model_validate(candidate)
            if profile.character_id == cid and input_hash(profile.model_dump(mode="json")) == sample.get("profileHash"):
                return profile
    raise ValueError("This older sample has no recoverable voice profile. It can be previewed but cannot be applied.")


def invalidate_character(media, cid):
    """Caller owns the project lock; unrelated character approvals stay intact."""
    state = media.assets.load()
    state.character_approvals.pop(cid, None)
    state.consistency_report = {}
    media.assets._save(state)
    manifest = media.manager.load(media.project_id)
    for gate in ("characters", "assets"):
        manifest.asset_approvals.pop(gate, None)
    media.manager.save(manifest)
    media.assets._refresh()


def apply_costume(media, cid, costume_id, revision):
    from .locking import project_lock
    from .studio import WorldStudio

    WorldStudio(media.manager, media.project_id).require_approved()
    with project_lock(media.path), ledger_lock(media.path):
        signature = media.costume_signature(cid, costume_id)
        state = media.load()
        key = "costume:" + costume_id
        selected = next((v for v in state["visuals"].get(key, []) if v["revision"] == revision), None)
        if not selected or not visual_intact(media, selected):
            raise ValueError("This costume revision is missing or modified; choose an intact saved revision.")
        current = active_visual(state, key)
        if current == selected and visual_current(media, state, key, signature):
            return state
        state.setdefault("activeVisuals", {})[key] = {"revision": revision, "sourceHash": signature}
        media.save(state)
        invalidate_character(media, cid)
        return state


def apply_voice(media, cid, revision):
    from .identity import IdentityManager
    from .locking import project_lock
    from .studio import WorldStudio

    WorldStudio(media.manager, media.project_id).require_approved()
    with project_lock(media.path), ledger_lock(media.path):
        state = media.load()
        sample = next((s for s in state["samples"].get(cid, []) if s["revision"] == revision), None)
        if not sample or not sample_intact(media, sample):
            raise ValueError("This voice revision is missing or modified; choose an intact saved revision.")
        if not media.sample_matches_provider(sample):
            raise ValueError("This voice revision uses a different provider/model. Restore its provider settings before applying.")
        profile = sample_profile(media, cid, sample)
        identity = IdentityManager(media.manager, media.project_id)
        library = identity.load()
        if library is None or cid not in library.voices:
            raise ValueError("Choose an existing character.")
        # Applying saved data makes no generation or provider lookup request.
        identity.validate(library)
        if library.voices[cid] != profile:
            from .files import atomic_write
            from uuid import uuid4
            atomic_write(media.path / "history" / "identity" / f"{uuid4().hex}.json", library.model_dump_json(by_alias=True, indent=2))
            library.voices[cid] = profile
            library.approval = None
            identity._write(library)
            state.setdefault("activeSamples", {})[cid] = {}
        state.setdefault("activeSamples", {}).setdefault(cid, {})[sample["emotion"]] = revision
        state["voiceApprovals"].pop(cid, None)
        media.save(state)
        invalidate_character(media, cid)
        return state


def sample_signature(samples):
    return input_hash([{k: sample.get(k) for k in ("revision", "audio", "fileHash", "profileHash", "emotion")}
                       for sample in sorted(samples, key=lambda s: s["emotion"])])
