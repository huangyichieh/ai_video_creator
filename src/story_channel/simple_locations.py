"""Single-master locations, independent of the floor-plan master-pair workflow."""
from .background_library import BackgroundLibrary, locations
from .locking import project_lock


def save(engine, lid, *, reference_type=None, prompt=None):
    library = BackgroundLibrary(engine)
    library._root()
    lid = library.subject_id(lid)
    from .location_references import LocationReferenceSet
    with project_lock(engine.path):
        state = engine.load()
        spec = state.location_reference_sets.setdefault(lid, LocationReferenceSet().model_dump(mode="json"))
        if reference_type is not None:
            kind = LocationReferenceSet(reference_type=reference_type).reference_type
            if kind != spec.get("reference_type"):
                spec["reference_type"] = kind
                spec["generation_method"] = "dual_panorama"
                for key in list(state.location_master_approvals):
                    if key == lid or key.startswith(lid+":"):
                        state.location_master_approvals.pop(key,None)
                for key in list(state.location_yaw_approvals):
                    if key.startswith(lid+":") or key.startswith(lid+"__"):
                        state.location_yaw_approvals.pop(key,None)
                if kind == "SIMPLE" and not spec.get("simple_prompt", "").strip():
                    spec["simple_prompt"] = next(loc.description for loc in locations(engine.creative) if loc.location_id == lid)
        if prompt is not None:
            if not prompt.strip():
                raise ValueError("Enter a detail prompt before generating a SIMPLE background.")
            spec["simple_prompt"] = prompt.strip()
        state.background_approvals.pop(lid,None)
        manifest = engine.manager.load(engine.project_id)
        for gate in ("backgrounds","assets"):
            manifest.asset_approvals.pop(gate,None)
        engine.manager.save(manifest)
        engine._save(state)
        return engine._refresh()[0]


def request(engine, state, task):
    lid = BackgroundLibrary(engine).subject_id(task.subject)
    spec = state.location_reference_sets[lid]
    if not spec.get("simple_prompt", "").strip():
        raise ValueError("Enter and save a detail prompt before generating a SIMPLE background.")
    prompt, references = task.prompt, []
    if lid in state.background_images:
        references.append(BackgroundLibrary(engine).image(state, lid))
        prompt += "\nImage 1 is optional environment guidance: use it to guide the requested scene, composition and appearance."
    from .style_rules import apply_rules, style_reference
    prompt = apply_rules(prompt, state.project_style_rules)
    prompt,references = style_reference(engine, state, prompt, references)
    from .background_constraints import EMPTY_BACKGROUND
    return prompt+"\n"+EMPTY_BACKGROUND,references
