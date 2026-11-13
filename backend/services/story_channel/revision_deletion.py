"""Remove revision metadata and renumber surviving selections under write locks."""
import json
from uuid import uuid4

from .files import atomic_write, input_hash
from .ledger_lock import ledger_lock
from .locking import project_lock
from .asset_models import AssetRecord


def archive(engine, kind, subject, value):
    # Keep recovery metadata and media used by existing episode snapshots.
    file = engine.path / "history" / "deleted_revisions" / (uuid4().hex + ".json")
    atomic_write(file, json.dumps({"kind": kind, "subject": subject, "before": value}, ensure_ascii=False, indent=2))


def approvals_before(engine, state, cid=None, lid=None):
    from .background_library import BackgroundLibrary
    tasks = engine.tasks(state)
    manifest = engine.manager.load(engine.project_id)
    return (bool(cid and engine.character_approved(cid, state, tasks)),
            bool(lid and BackgroundLibrary(engine).approved(lid, state, tasks)),
            {gate for gate, approval in manifest.asset_approvals.items()
             if gate in ("characters", "backgrounds", "assets") and approval.content_hash == engine._gate_hash(gate, state, tasks)})


def finish(engine, state, before, *, cid=None, lid=None, changed=False):
    from .background_library import BackgroundLibrary
    tasks = engine.tasks(state)
    manifest = engine.manager.load(engine.project_id)
    if changed:
        if cid:
            state.character_approvals.pop(cid, None)
        if lid:
            state.background_approvals.pop(lid, None)
            state.background_panorama_reviews.pop(lid, None)
        for gate in (("characters", "backgrounds", "assets") if cid else ("backgrounds", "assets")):
            manifest.asset_approvals.pop(gate, None)
        state.consistency_report = {}
    else:
        # Display numbers changed; the approved content and selection did not.
        if cid and before[0] and cid in state.character_approvals:
            state.character_approvals[cid].content_hash = engine._character_approval_hash(cid, state)
        if lid and before[1] and lid in state.background_approvals:
            state.background_approvals[lid].content_hash = BackgroundLibrary(engine).digest(lid, state, tasks)
        for gate in before[2]:
            if gate in manifest.asset_approvals:
                manifest.asset_approvals[gate].content_hash = engine._gate_hash(gate, state, tasks)
    engine.manager.save(manifest)
    engine._save(state)
    return engine._refresh()[0]


def delete_image(engine, stage, subject, asset_id):
    from .asset_revisions import history_path, revisions
    from .background_library import BackgroundLibrary
    folder = history_path(engine, stage, subject)
    with project_lock(engine.path), ledger_lock(engine.path):
        state, _ = engine._refresh()
        key = f"{stage}:{subject}"
        history = sorted(revisions(engine, stage, subject), key=lambda r: (r.revision, r.updated_at))
        deleted = next((r for r in history if r.asset.asset_id == asset_id), None)
        if deleted is None:
            raise ValueError("This revision no longer exists. Refresh the revision list.")
        cid = subject if stage == "character_references" else None
        lid = BackgroundLibrary(engine).subject_id(subject) if stage == "backgrounds" else None
        before = approvals_before(engine, state, cid, lid)
        current = state.records.get(key)
        active_deleted = bool(current and current.asset and current.asset.asset_id == asset_id)
        archive(engine, stage, subject, {"history": [r.model_dump(mode="json", by_alias=True) for r in history],
                                        "production": state.model_dump(mode="json", by_alias=True)})
        remaining = [r for r in history if r.asset.asset_id != asset_id]
        for number, record in enumerate(remaining, 1):
            record.revision = number
            atomic_write(folder / (record.asset.asset_id + ".json"), record.model_dump_json(by_alias=True, indent=2))
            if current and current.asset and current.asset.asset_id == record.asset.asset_id:
                current.revision = number
        if active_deleted:
            state.records[key] = AssetRecord(stage=stage, subject=subject, attempts=current.attempts)
            if stage == "backgrounds" and subject.endswith("__master"):
                state.location_master_approvals.pop(lid, None)
            if stage == "backgrounds" and subject.rsplit("__", 1)[-1] in ("master_panorama", "front_panorama", "back_panorama"):
                state.location_master_approvals.pop(lid+":"+subject.rsplit("__", 1)[-1], None)
                from .dual_panorama import invalidate_world
                invalidate_world(engine, lid)
        # UUID filenames are stable; renumbering never moves media files.
        for path in folder.glob("*.json"):
            from .files import read_text
            try:
                saved = AssetRecord.model_validate_json(read_text(path))
            except (OSError, ValueError):
                continue
            if saved.asset and saved.asset.asset_id == asset_id:
                path.unlink()
        return finish(engine, state, before, cid=cid, lid=lid, changed=active_deleted)


def delete_library(media, cid, revision, *, costume_id=None, expected=None):
    from .identity import IdentityManager
    from .library_revisions import active_visual, sample_signature
    engine = media.assets
    with project_lock(media.path), ledger_lock(media.path):
        identity = IdentityManager(media.manager, media.project_id).load()
        if costume_id and (costume_id not in identity.costumes or identity.costumes[costume_id].character_id != cid):
            raise ValueError("Choose a costume belonging to this character.")
        state = media.load()
        production = engine.load()
        before = approvals_before(engine, production, cid)
        key = "costume:" + costume_id if costume_id else cid
        versions = state["visuals" if costume_id else "samples"].get(key, [])
        selected = next((v for v in versions if v["revision"] == revision), None)
        if selected is None or expected and input_hash(selected) != expected:
            raise ValueError("The revision list changed. Select the revision again before deleting.")
        active = active_visual(state, key) if costume_id else None
        samples = media.selected_samples(cid, state=state) if not costume_id else []
        voice_approved = not costume_id and media.voice_approved(cid, state=state)
        changed = (active is selected) if costume_id else any(v["revision"] == revision for v in samples)
        archive(engine, "costume" if costume_id else "voice", key, state)
        remaining = sorted((v for v in versions if v is not selected), key=lambda v: v["revision"])
        numbers = {v["revision"]: number for number, v in enumerate(remaining, 1)}
        if costume_id:
            selection = state["activeVisuals"].get(key, {})
            state["activeVisuals"][key] = {**selection, "revision": numbers.get(active["revision"], 0) if active else 0}
        else:
            choices = state["activeSamples"].setdefault(cid, {})
            for emotion, old in list(choices.items()):
                choices[emotion] = numbers.get(old, 0)
            for sample in samples:
                choices[sample["emotion"]] = numbers.get(sample["revision"], 0)
        for number, item in enumerate(remaining, 1):
            item["revision"] = number
        state["visuals" if costume_id else "samples"][key] = remaining
        if not costume_id:
            if changed:
                state["voiceApprovals"].pop(cid, None)
            elif voice_approved:
                state["voiceApprovals"][cid]["selectionHash"] = sample_signature(media.selected_samples(cid, state=state))
        media.save(state)
        return finish(engine, production, before, cid=cid, changed=changed)
