"""Explicit asset identities and immutable reference inputs for scene stills."""
from .media_utils import file_hash

LOCK = ("Asset lock: match cast faces, hair, proportions, clothing AND rendering style exactly. "
        "Use location geometry and supplied prop designs. Style guides are STYLE ONLY; never restyle/redesign cast. "
        "Compose one scene, not a sheet; ignore reference backgrounds around isolated assets.")


def manifest(engine,state,task):
    from .storyboard_asset_check import document
    from .check_prop_assets import selected
    scene = next(item for item in engine.creative.artifact("storyboard").scenes if item.scene_id == task.subject)
    result = []
    for key in task.refs:
        record = state.records.get(key)
        asset = record.asset if record else None
        result.append({"role":"character" if key.startswith("character_references:") else "background",
            "id":key.split(":",1)[1],"file":asset.file_path if asset else None,
            "fileHash":record.file_hash if asset else None,"assetId":asset.asset_id if asset else None,
            "intact":engine._intact(record)})
    media = document(engine.path,"library_media.json")
    for field in ("props","visuals","activeVisuals"):
        media.setdefault(field,{})
    declared = {item.prop_id:item for item in engine.creative.artifact("storyboard").props}
    for pid in scene.prop_ids:
        modes = declared[pid].modes if pid in declared else media["props"].get(pid,{}).get("modes",["default"])
        for mode in modes:
            version = selected(media,pid,mode)
            asset = version.get("asset",{})
            from .storyboard_asset_check import intact
            result.append({"role":"prop","id":pid+"/"+mode,"file":asset.get("filePath"),
                "fileHash":version.get("fileHash"),"assetId":asset.get("assetId"),"revision":version.get("revision"),
                "intact":intact(engine.path,{"file":asset.get("filePath"),"fileHash":version.get("fileHash")})})
    return result


def request(engine,state,task,config,*,runway=False):
    from .style_rules import apply_rules,style_reference
    entries = [dict(item) for item in config.options["sceneAssetReferences"]]
    if not any(item["role"] == "background" for item in entries):
        raise ValueError("Select an existing background/detail asset before generating this scene image.")
    references = []
    for item in entries:
        if not item["file"]:
            raise ValueError("Missing scene asset reference: "+item["id"]+". Create it on Assets first.")
        path = engine.local_path(item["file"])
        if not path.is_file() or file_hash(path) != item["fileHash"]:
            raise ValueError("Scene asset reference is missing or modified: "+item["id"]+". Refresh Assets before generating.")
        references.append(path)
    prompt = task.prompt
    for field in ("generic_prompt","style_prompt"):
        if config.options.get(field):
            prompt += "\nCompatible style detail: "+config.options[field]
    prompt = apply_rules(prompt,state.project_style_rules)
    # Legacy style uploads remain style-only and never displace cast references.
    for path in engine._style_references(state):
        if path not in references:
            references.append(path)
            entries.append({"role":"style","id":"legacy-style","file":path.relative_to(engine.path).as_posix(),"fileHash":file_hash(path)})
    prompt,with_style = style_reference(engine,state,prompt,references)
    if len(with_style) > len(references):
        path = with_style[-1]
        entries.append({"role":"style","id":"project-style","file":path.relative_to(engine.path).as_posix(),"fileHash":file_hash(path)})
    references = with_style
    modification = state.asset_modifications.get(task.key)
    if modification:
        path = engine.local_path(modification.source_path)
        if not path.is_file() or file_hash(path) != modification.source_hash:
            raise ValueError("The current image changed. Reopen Modify.")
        references.append(path)
        entries.append({"role":"current","id":"image-to-edit","file":modification.source_path,"fileHash":modification.source_hash})
        prompt += "\nEdit the supplied current image, preserving unrelated composition. Request: "+modification.prompt
    legend = "Inputs: "+"; ".join(f"{index}={item['role']}:{item['id']}" for index,item in enumerate(entries,1))+". "
    suffix = legend+LOCK
    if runway:
        from .provider_prompts import compile_prompt
        from .runway_prompt_limits import MAX_PROMPT_UNITS,prompt_units
        # Reserve the non-compressible identity legend and provider packing notice.
        limit = MAX_PROMPT_UNITS-prompt_units(suffix)-170
        if limit < 100:
            raise ValueError("Too many scene assets for a reliable Runway identity prompt. Split this shot into fewer subjects.")
        prompt = compile_prompt(engine.creative,prompt,task_key=f"assets:{task.key}:prompt",max_units=limit)
    prompt += "\n"+suffix
    config.options["scene_reference_roles"] = entries
    config.options["reference_tags"] = ["style" if item["role"] == "style" else f"input{index}" for index,item in enumerate(entries,1)]
    return prompt,references
