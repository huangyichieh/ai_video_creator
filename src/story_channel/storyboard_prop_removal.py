"""Remove episode prop-mode requirements with narrowly scoped storyboard edits."""
from .creative import Storyboard,StoryboardProp


def remove(pipeline, selections, expected, *, provider=None):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    if source != expected:
        raise ValueError("Content changed in another session. Reopen the dialog before deleting props.")
    from .storyboard_asset_check import check
    rows = {row["ID"]:row for row in check(pipeline.manager,pipeline.project_id) if row["Category"] == "Props"}
    keys = set(selections)
    if not keys or not keys <= rows.keys():
        raise ValueError("Select current prop modes from the checklist.")
    original = Storyboard.model_validate_json(source)
    candidate = original.model_copy(deep=True)
    from .storyboard_editor import inventory,save
    catalog = inventory(pipeline)
    declared = {item.prop_id:item for item in candidate.props}
    removed = {}
    removed_ids = set()
    for key in keys:
        _,pid,mode = key.split(":",2)
        removed.setdefault(pid,set()).add(mode)
    for pid,modes in removed.items():
        prop = declared.get(pid)
        if prop is None:
            prop = StoryboardProp.model_validate(catalog["props"][pid])
            candidate.props.append(prop)
        remaining = [mode for mode in prop.modes if mode not in modes]
        if not remaining:
            removed_ids.add(pid)
        else:
            prop.modes = remaining
    candidate.props = [item for item in candidate.props if item.prop_id not in removed_ids]
    affected = {scene.scene_id for scene in original.scenes if set(scene.prop_ids) & removed.keys()}
    for scene in candidate.scenes:
        scene.prop_ids = [pid for pid in scene.prop_ids if pid not in removed_ids]
    allowed = {"visual_description","action","camera_movement","interaction_target","sfx","sfx_timing"}
    def validate(output):
        if output.model_dump(exclude={"scenes"}) != candidate.model_dump(exclude={"scenes"}) or len(output.scenes) != len(candidate.scenes):
            raise ValueError("Prop removal changed unrelated storyboard assets or scenes. Nothing was saved.")
        for before,after in zip(candidate.scenes,output.scenes):
            if before.scene_id not in affected:
                if after != before:
                    raise ValueError("Prop removal changed an unrelated scene. Nothing was saved.")
                continue
            if before.model_dump(exclude=allowed) != after.model_dump(exclude=allowed):
                raise ValueError("Prop removal changed protected story content, timing or dependencies. Nothing was saved.")
            if all(getattr(before,field) == getattr(after,field) for field in allowed):
                raise ValueError("Storyboard text must be adapted to work without the selected props or modes. Nothing was saved.")
        return output
    from .modification import modify_content
    output = modify_content(pipeline,candidate,
        "Remove the selected prop-mode dependencies and adapt ONLY affected scenes so their staging works without them. "
        "The dependency lists and declarations in CURRENT are already corrected; preserve them exactly. "
        "For affected scenes, edit only visualDescription, action, cameraMovement, interactionTarget and prop-related SFX and their sfxTiming. "
        "Replace prop-dependent actions with plausible gestures, blocking or environmental actions that preserve the same story beat. "
        "Remove references to deleted props/modes in these fields; do not invent replacement prop requirements. "
        "If other modes of a prop remain, that prop remains available only in those modes. "
        "Preserve all dialogue, narration, plot, characters, backgrounds, camera angles, timing, order, IDs and unrelated scenes exactly. "
        "Return the complete storyboard in the original language. Every affected scene needs a targeted text adjustment.",
        "storyboard",context={"originalStoryboard":original.model_dump(mode="json",by_alias=True),
            "removedPropModes":{pid:sorted(modes) for pid,modes in removed.items()},"affectedSceneIds":sorted(affected)},
        provider=provider,task_key="storyboard:remove_props",validate_output=validate)
    return save(pipeline,output.model_dump(mode="json",by_alias=True),expected,preserve_storyboard_approval=True)
