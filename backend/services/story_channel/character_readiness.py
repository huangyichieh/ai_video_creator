"""One completeness rule shared by character cards and approval services."""
def character_approval_hint(engine, subject, state, checks, *, approved=False, busy=False):
    if approved:
        return "This character is already approved."
    reasons = []
    if not checks.get("Visual", False):
        reasons.append("Visual: generate or apply a current character model sheet.")
    if not checks.get("Voice", True):
        from .library_media import LibraryMedia
        media = LibraryMedia(engine.manager, engine.project_id)
        media.assets = engine
        missing = media.voice_approval_missing(subject)
        reasons.append("Voice: " + (" ".join(missing) if missing else "review and approve the current voice samples."))
    if not checks.get("Costumes", True):
        from .identity import IdentityManager
        library = IdentityManager(engine.manager, engine.project_id).load()
        costumes = [item for item in library.costumes.values() if item.character_id == subject] if library else []
        if not costumes:
            reasons.append("Costumes: add a costume and generate its model sheet.")
        else:
            from .library_revisions import visual_current
            from .library_media import LibraryMedia
            media = LibraryMedia(engine.manager, engine.project_id)
            media.assets = engine
            saved = media.load()
            for costume in costumes:
                try:
                    current = visual_current(media, saved, "costume:" + costume.costume_id,
                                             media.costume_signature(subject, costume.costume_id, state=state))
                except (OSError, ValueError, KeyError, StopIteration):
                    current = False
                if not current:
                    reasons.append(f"Costume {costume.costume_id}: generate or apply a current model sheet.")
    if busy:
        reasons.append("Wait for the current task to finish.")
    return "\n\n".join(reasons) or "Approve the current character visual, voice and costume assets."


def character_readiness(engine, subject, state, tasks=None):
    from .identity import IdentityManager
    from .library_media import LibraryMedia
    from .library_revisions import active_visual, visual_current

    tasks = tasks if tasks is not None else engine.tasks(state)
    task = next((t for t in tasks if t.key == "character_references:" + subject), None)
    record = state.records.get("character_references:" + subject)
    checks = {"Visual": bool(task and engine.record_current(record, task, state))}
    snapshot = {}
    if engine.manager.load(engine.project_id).workflow_version != "world_episode":
        return checks, snapshot
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    saved = media.load()
    library = IdentityManager(engine.manager, engine.project_id).load()
    profile = library.voices.get(subject) if library else None
    checks["Voice"] = bool(profile and profile.voice_id.strip() and media.voice_approved(subject, profile, saved))
    snapshot["voice"] = profile.model_dump(mode="json") if profile else None
    snapshot["samples"] = [{key: sample.get(key) for key in ("audio", "fileHash", "profileHash", "emotion", "revision")}
                           for sample in media.selected_samples(subject, profile, saved)] if profile else []
    costumes = [item for item in library.costumes.values() if item.character_id == subject] if library else []
    checks["Costumes"] = bool(costumes)
    snapshot["costumes"] = {}
    for costume in costumes:
        key = "costume:" + costume.costume_id
        latest = active_visual(saved, key)
        current = False
        try:
            current = visual_current(media, saved, key, media.costume_signature(subject, costume.costume_id, state=state))
        except (OSError, ValueError, KeyError, StopIteration):
            pass
        checks["Costumes"] &= current
        snapshot["costumes"][costume.costume_id] = {"description": costume.description,
            "source": saved.get("activeVisuals", {}).get(key, {}).get("sourceHash", latest.get("sourceHash")) if latest else None,
            "asset": latest.get("asset") if latest else None, "file": latest.get("fileHash") if latest else None}
    snapshot["checks"] = checks
    return checks, snapshot
