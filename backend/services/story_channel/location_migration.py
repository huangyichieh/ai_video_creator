"""Preserve DETAIL/SIMPLE choices while normalizing older location workflows."""


def unify(engine,state,manifest):
    if manifest.workflow_version != "world_episode" or not (engine.path/"world.json").exists():
        return
    from .location_references import LocationReferenceSet
    from .background_library import locations
    # Episode reference bundles remain frozen until explicitly refreshed from World.
    if manifest.world_project_id:
        return
    for location in locations(engine.creative):
        spec = state.location_reference_sets.get(location.location_id)
        if spec is None:
            state.location_reference_sets[location.location_id] = LocationReferenceSet().model_dump(mode="json")
        elif spec.get("reference_type") not in ("KEY_360","SIMPLE"):
            spec["reference_type"] = "KEY_360"
            spec["generation_method"] = "dual_panorama"
            state.background_approvals.pop(location.location_id,None)
    if state.location_workflow_version < 2:
        for spec in state.location_reference_sets.values():
            spec["generation_method"] = "dual_panorama"
        state.location_workflow_version = 2
