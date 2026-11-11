"""Structured storyboard edits with stable asset IDs and optimistic saves."""
from .file_cache import read_json
from .creative import Storyboard
from .bgm_library import entries,ready


def inventory(pipeline):
    manifest = pipeline.manager.load(pipeline.project_id)
    owner = manifest.world_project_id or pipeline.project_id
    path = pipeline.manager.project_path(owner)
    def read(name):
        return read_json(path/name) if (path/name).exists() else {}
    world,catalog,media,identity,production = [read(name) for name in (
        "world.json","background_library.json","library_media.json","identity_library.json","production.json")]
    backgrounds = {item["locationId"]:item for item in [*world.get("locations",[]),*catalog.get("added",[])]
                   if item["locationId"] not in catalog.get("removed",[])}
    for key,patch in catalog.get("overrides",{}).items():
        if key in backgrounds:
            backgrounds[key] = {**backgrounds[key],**patch}
    props = {key:{"propId":key,"title":value.get("title",key),"description":value["description"],"modes":value.get("modes",["default"])}
             for key,value in {**identity.get("props",{}),**media.get("props",{})}.items()}
    bgm = entries(pipeline.manager,owner)
    available = {"backgrounds":set(),"props":set(),"bgm":{key for key,item in bgm.items() if ready(path,item)}}
    for record in production.get("records",{}).values():
        asset = record.get("asset")
        if record.get("stage") == "backgrounds" and record.get("status") == "succeeded" and asset:
            relative = asset.get("filePath",asset.get("file_path",""))
            if relative and (path/relative).is_file():
                available["backgrounds"].add(record["subject"].split("__",1)[0])
    for key,versions in media.get("visuals",{}).items():
        if key.startswith("prop:") and any((path/item["asset"]["filePath"]).is_file() for item in versions if item.get("asset")):
            available["props"].add(key.split(":")[1])
    from .storyboard_assets import requests
    sfx = {row["description"]:row["title"] for row in requests(pipeline.manager,owner,"sfx")}
    details = {}
    sets = production.get("locationReferenceSets",{})
    if any(spec.get("detail_references") for spec in sets.values()):
        from .assets import AssetPipeline
        from .location_details import current
        engine = AssetPipeline(pipeline.manager,owner)
        state = engine.load()
        for lid,spec in sets.items():
            if lid not in backgrounds:
                continue
            for identity,reference in spec.get("detail_references",{}).items():
                key = lid+"__detail_"+identity
                details[key] = {"locationId":lid,"detailId":identity,
                    "area":reference.get("area",identity),
                    "name":backgrounds[lid].get("name",lid)+" · "+identity,
                    "description":reference.get("description",reference.get("area",identity))}
                if current(engine,state,lid,reference):
                    available["backgrounds"].add(key)
    return {"backgrounds":backgrounds,"props":props,"bgm":{key:{"bgmId":key,"title":value["title"],"description":value["description"]} for key,value in bgm.items()},
        "detail_backgrounds":details,
        "available":available,"sfx":sfx,"locations":production.get("locationReferenceSets",{}),
        "characters":{item.character_id:item.name for item in pipeline.artifact("characters").characters}}


def background_choice(scene,details):
    key = scene["locationId"]+"__detail_"+scene.get("targetArea","")
    return key if key in details else scene["locationId"]


def select_background(scene,choice,details):
    """UI detail choices remain a parent location ID plus targetArea on disk."""
    detail = details.get(choice)
    if detail:
        scene["locationId"],scene["targetArea"] = detail["locationId"],detail["detailId"]
    else:
        if background_choice(scene,details) in details:
            scene["targetArea"] = ""
        scene["locationId"] = choice


def reconcile_detail_requirements(data,catalog):
    """Keep declarations for selected parent/detail pairs, without deleting assets."""
    declared = {(item.get("locationId"),item.get("detailId")):item for item in data.get("detailBackgrounds",[])}
    known = catalog.get("detail_backgrounds",{})
    selected = dict.fromkeys((scene.get("locationId"),scene.get("targetArea")) for scene in data["scenes"] if scene.get("targetArea"))
    result = []
    for lid,identity in selected:
        item = declared.get((lid,identity))
        if item is None and (reference := known.get(lid+"__detail_"+identity)):
            item = {"locationId":lid,"detailId":identity,"area":reference.get("area",identity),
                "description":reference.get("description") or reference.get("area") or identity}
        if item is not None:
            result.append(item.copy())
    return result


def materialize(data,catalog):
    """Include metadata for selected assets not yet present in the frozen episode."""
    import copy
    data = copy.deepcopy(data)
    ordered = sorted(enumerate(data["scenes"],1),key=lambda pair:(pair[1]["order"],
        1 if pair[1]["order"] > pair[0] else -1 if pair[1]["order"] < pair[0] else 0))
    data["scenes"] = [scene for _,scene in ordered]
    for index,scene in enumerate(data["scenes"],1):
        scene["order"] = index
        if "sfxTiming" in scene:
            scene["sfxTiming"] = [item for item in scene["sfxTiming"] if item["description"] in scene.get("sfx",[])]
    data["detailBackgrounds"] = reconcile_detail_requirements(data,catalog)
    board = Storyboard.model_validate(data)
    for field,id_field,selected in (
        ("backgrounds","locationId",{scene.location_id for scene in board.scenes}),
        ("props","propId",{key for scene in board.scenes for key in scene.prop_ids}),
        ("bgm","bgmId",{scene.bgm_id for scene in board.scenes if scene.bgm_id})):
        declared = {item[id_field]:item for item in data.get(field,[])}
        for identity in selected:
            if identity not in declared and identity in catalog[field]:
                declared[identity] = catalog[field][identity]
        data[field] = list(declared.values())
    return Storyboard.model_validate(data)


def save_scene(pipeline,scene_id,data,expected,*,auto_approve=False):
    """Merge one scene into the latest board; preserve unrelated saved edits."""
    from .models import Scene
    baseline = Storyboard.model_validate_json(expected).model_dump(mode="json",by_alias=True)
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    current = Storyboard.model_validate_json(source).model_dump(mode="json",by_alias=True)
    original = next((item for item in baseline["scenes"] if item["sceneId"] == scene_id),None)
    index = next((i for i,item in enumerate(current["scenes"]) if item["sceneId"] == scene_id),None)
    edited = next((item for item in data["scenes"] if item["sceneId"] == scene_id),None)
    if original is None or index is None or edited is None:
        raise ValueError("This storyboard scene no longer exists. Reload the scene editor.")
    if current["scenes"][index] != original:
        raise ValueError("This scene changed in another editor. Reload or revert before saving.")
    edited = Scene.model_validate(edited).model_dump(mode="json",by_alias=True)
    if edited["order"] != original["order"]:
        raise ValueError("Reorder scenes on Story/Episode; this editor updates one scene only.")
    current["scenes"][index] = edited
    for field,keys in (("backgrounds",("locationId",)),("props",("propId",)),("bgm",("bgmId",)),
                       ("detailBackgrounds",("locationId","detailId"))):
        identity = lambda item:tuple(item[key] for key in keys)
        prior = {identity(item):item for item in baseline.get(field,[])}
        known = {identity(item):item for item in current.get(field,[])}
        for item in data.get(field,[]):
            key = identity(item)
            if key in prior:
                if item != prior[key]:
                    raise ValueError("Edit existing reusable requirements on Story/Episode or Assets.")
                continue
            if key in known and known[key] != item:
                raise ValueError("This asset requirement changed in another editor. Reload before saving.")
            if key not in known:
                current.setdefault(field,[]).append(item)
                known[key] = item
    return save(pipeline,current,source,auto_approve=auto_approve)


def save(pipeline,data,expected,*,preserve_storyboard_approval=False,auto_approve=False):
    output = materialize(data,inventory(pipeline))
    if (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig") != expected:
        raise ValueError("Content changed in another session. Reload the storyboard before saving.")
    from .bgm_library import import_selected
    import_selected(pipeline.manager,pipeline.project_id,{scene.bgm_id for scene in output.scenes if scene.bgm_id})
    pipeline.edit_bundle({"storyboard":output.model_dump_json(by_alias=True,indent=2)},expected={"storyboard":expected},
        preserve_storyboard_approval=preserve_storyboard_approval,approve_storyboard_edit=auto_approve)
    manifest = pipeline.manager.load(pipeline.project_id)
    from .storyboard_assets import sync
    sync(pipeline.manager,manifest.world_project_id or pipeline.project_id)
    return output
