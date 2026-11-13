"""Retire global style inputs without invalidating already reviewed media."""


def retire_shared_style(engine, state, manifest):
    """The caller owns the project lock. Never approve stale or changed data."""
    from .background_library import BackgroundLibrary
    from .identity import IdentityManager
    from .library_media import LibraryMedia
    from .library_revisions import active_visual, visual_current

    tasks = engine.tasks(state)
    current = {task.key for task in tasks if engine.record_current(state.records.get(task.key), task, state)}
    characters = {cid: approval for cid, approval in state.character_approvals.items()
                  if engine.character_approved(cid, state, tasks)}
    backgrounds = BackgroundLibrary(engine)
    locations = {lid: approval for lid, approval in state.background_approvals.items()
                 if backgrounds.approved(lid, state, tasks)}
    gates = {gate: approval for gate, approval in manifest.asset_approvals.items()
             if approval.content_hash == engine._gate_hash(gate, state, tasks)}
    media = LibraryMedia(engine.manager, engine.project_id)
    media.assets = engine
    saved = media.load()
    identity = IdentityManager(engine.manager, engine.project_id).load()
    costumes = []
    for costume in identity.costumes.values() if identity else []:
        key = "costume:" + costume.costume_id
        try:
            signature = media.costume_signature(costume.character_id, costume.costume_id, state=state)
            if visual_current(media, saved, key, signature):
                costumes.append((key, costume))
        except (OSError, ValueError, KeyError, StopIteration):
            pass

    state.per_asset_references = True
    for task in tasks:
        if task.key in current:
            record = state.records[task.key]
            digest = engine._hash(task, state)
            if digest != (record.applied_input_hash or record.input_hash):
                record.applied_input_hash = digest
    for key, costume in costumes:
        version = active_visual(saved, key)
        saved["activeVisuals"][key] = {"revision": version["revision"],
            "sourceHash": media.costume_signature(costume.character_id, costume.costume_id, state=state)}
    if costumes:
        media.save(saved)
    # Keep the original prompts, input hashes and revision files as provenance.
    for cid, approval in characters.items():
        state.character_approvals[cid] = approval.model_copy(update={
            "content_hash": engine._character_approval_hash(cid, state)})
    for lid, approval in locations.items():
        state.background_approvals[lid] = approval.model_copy(update={
            "content_hash": backgrounds.digest(lid, state, tasks)})
    for gate, approval in gates.items():
        manifest.asset_approvals[gate] = approval.model_copy(update={
            "content_hash": engine._gate_hash(gate, state, tasks)})
    engine.manager.save(manifest)


def narrow_character_context(engine, state, manifest):
    """Preserve existing reviewed images when removing unrelated prompt fields."""
    old_tasks = engine.tasks(state)
    current = {task.key for task in old_tasks if task.stage == "character_references"
               and engine.record_current(state.records.get(task.key), task, state)}
    approved = {cid: approval for cid, approval in state.character_approvals.items()
                if engine.character_approved(cid, state, old_tasks)}
    gates = {gate: approval for gate, approval in manifest.asset_approvals.items()
             if gate in ("characters", "assets") and approval.content_hash == engine._gate_hash(gate, state, old_tasks)}
    state.character_prompt_version = 1
    tasks = engine.tasks(state)
    for task in tasks:
        if task.key in current:
            state.records[task.key].applied_input_hash = engine._hash(task, state)
    for cid, approval in approved.items():
        state.character_approvals[cid] = approval.model_copy(update={"content_hash": engine._character_approval_hash(cid, state)})
    for gate, approval in gates.items():
        manifest.asset_approvals[gate] = approval.model_copy(update={"content_hash": engine._gate_hash(gate, state, tasks)})
    engine.manager.save(manifest)
