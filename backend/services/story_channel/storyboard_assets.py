"""Register committed storyboard requirements as reusable drafts, without provider calls."""
import json
import re
import unicodedata

from .files import atomic_write,input_hash
from .file_cache import read_json
from .locking import project_lock


def asset_catalog(manager,project_id,*,include_reusable=False):
    path = manager.project_path(project_id)/"identity_library.json"
    data = read_json(path) if path.exists() else {}
    from .bgm_library import entries
    result = {"props":{key:{"description":value.get("description","")} for key,value in data.get("props",{}).items()},
        "bgm":{key:{"title":value["title"],"description":value["description"]} for key,value in entries(manager,project_id).items()}}
    if include_reusable:
        media_path = manager.project_path(project_id)/"library_media.json"
        media = read_json(media_path) if media_path.exists() else {}
        for key,value in media.get("props",{}).items():
            if key not in media.get("removedProps",[]):
                result["props"][key] = {"title":value.get("title",key),"description":value.get("description",""),"modes":value.get("modes",[])}
        from .audio_assets import items
        from .storyboard_asset_check import sound_id,intact
        cues = {row["id"]:row["description"] for row in requests(manager,project_id,"sfx")}
        result["sfx"] = {}
        for key,value in items(manager,project_id,"SFX").items():
            cue = next((text for text in (cues.get(key),value.get("name"),value.get("title"),value.get("description")) if text and sound_id(text) == key),None)
            if cue:
                result["sfx"][key] = {"cue":cue,"description":value.get("description",cue),"hasAudio":intact(manager.project_path(project_id),value)}
    return result


def pin_legacy_asset_catalog(manager,project_id):
    """Freeze an intact storyboard's inputs BEFORE a library-only mutation.

    Caller owns its project write lock. Do not bless already changed inputs or
    restore approvals: a matching original fingerprint is required.
    """
    path = manager.project_path(project_id)
    snapshot = path/"storyboard_reference_context.json"
    saved = read_json(snapshot) if snapshot.exists() else {}
    if "assetCatalog" in saved:
        return
    manifest = manager.load(project_id)
    stage = manifest.steps.get("storyboard")
    if not stage or stage.status != "succeeded":
        return
    from .pipeline import Pipeline
    pipeline = Pipeline(manager,project_id)
    try:
        if input_hash(pipeline.artifact("storyboard").model_dump(mode="json",by_alias=True)) != stage.output_hash:
            return
        context = pipeline._context("storyboard")
        if pipeline._fingerprint("storyboard",context=context) != (stage.retained_input_hash or stage.input_hash):
            return
    except (OSError,ValueError):
        return
    atomic_write(snapshot,json.dumps({**saved,"assetCatalog":context["assetCatalog"]},ensure_ascii=False,indent=2))


def requirements(manager,world_id):
    from .creative import Storyboard
    projects = manager.list_projects()
    rows = {}
    for project in projects:
        if project.project_id != world_id and project.world_project_id != world_id:
            continue
        state = project.steps.get("storyboard")
        path = manager.project_path(project.project_id)/"scenes.json"
        if not state or state.status != "succeeded" or not path.exists():
            continue
        try:
            raw = read_json(path)
            board = Storyboard.model_validate(raw)
            if state.output_hash not in (input_hash(raw),input_hash(board.model_dump(mode="json",by_alias=True))):
                continue  # Ignore partial commits or unvalidated manual file edits.
        except (OSError,ValueError):
            continue
        def add(kind,identity,title,description,scene_ids,**extra):
            row = rows.setdefault(kind+":"+identity,{"kind":kind,"id":identity,"title":title,
                "description":description,"sources":[],**extra})
            if "modes" in extra:
                row["modes"] = list(dict.fromkeys([*row["modes"],*extra["modes"]]))
            if "duration" in extra:
                row["duration"] = max(row["duration"],extra["duration"])
                row["loop"] = row.get("loop",False) or extra.get("loop",False)
            row["sources"].append({"episodeId":project.project_id,"episodeTitle":project.title,"sceneIds":scene_ids})
        for location in board.backgrounds:
            used = [scene.scene_id for scene in board.scenes if scene.location_id == location.location_id]
            if used:
                add("background",location.location_id,location.name,location.description,used)
        for detail in board.detail_backgrounds:
            scenes = [scene.scene_id for scene in board.scenes if scene.location_id == detail.location_id and scene.target_area == detail.detail_id]
            if not scenes:
                continue  # Old declarations are not current asset requirements.
            add("detail_background",detail.location_id+"__detail_"+detail.detail_id,detail.area,detail.description,
                scenes,
                locationId=detail.location_id,detailId=detail.detail_id)
        for prop in board.props:
            used = [scene.scene_id for scene in board.scenes if prop.prop_id in scene.prop_ids]
            if used:
                add("prop",prop.prop_id,prop.title,prop.description,used,modes=prop.modes)
        for music in board.bgm:
            used = [scene.scene_id for scene in board.scenes if scene.bgm_id == music.bgm_id]
            if used:
                add("bgm",music.bgm_id,music.title,music.description,used)
        for scene in board.scenes:
            if not scene.bgm_id and scene.bgm_mood.strip():
                from .bgm_library import mood_id
                add("bgm",mood_id(scene.bgm_mood),scene.bgm_mood.strip(),scene.bgm_mood.strip(),[scene.scene_id])
            for cue in scene.sfx:
                text = re.sub(r"\s+"," ",unicodedata.normalize("NFKC",cue)).strip()
                if text:
                    from .sfx_duration import scene_settings
                    add("sfx","sfx_"+input_hash(text.casefold())[:16],text,text,[scene.scene_id],**scene_settings(scene,cue))
    for row in rows.values():
        combined = {}
        for source in row["sources"]:
            entry = combined.setdefault(source["episodeId"],{**source,"sceneIds":[]})
            entry["sceneIds"] = sorted(set(entry["sceneIds"]) | set(source["sceneIds"]))
        row["sources"] = list(combined.values())
    return rows


def sync(manager,world_id):
    """Read episode snapshots without taking their locks; write only under the World lock."""
    from .asset_models import Production
    from .creative import Location
    from .location_references import LocationReferenceSet
    from .identity import IdentityManager,Prop
    from .animation_models import LocationBible
    path = manager.project_path(world_id)
    with project_lock(path):
        manifest = manager.load(world_id)
        if manifest.world_project_id:
            raise ValueError("Storyboard asset drafts belong to the parent World.")
        rows = requirements(manager,world_id)
        def read(name,default):
            return read_json(path/name) if (path/name).exists() else default
        catalog = read("background_library.json",{"added":[],"removed":[]})
        library = read("library_media.json",{"props":{},"visuals":{},"samples":{}})
        library.setdefault("props",{})
        music = read("bgm_library.json",{"entries":{}})
        production = Production.model_validate(read("production.json",{}))
        world = read("world.json",{})
        known = {item["locationId"] for item in world.get("locations",[])} | {item["locationId"] for item in catalog["added"]}
        removed = set(catalog.get("removed",[]))
        identity = IdentityManager(manager,world_id)
        identity_data = identity.load()
        original = {"background_library.json":catalog,"library_media.json":library,"bgm_library.json":music,
                    "production.json":production.model_dump(mode="json",by_alias=True)}
        before = {name:input_hash(value) for name,value in original.items()}
        identity_before = identity_data.model_dump_json(by_alias=True) if identity_data else None
        added_background = False
        for row in rows.values():
            if row["kind"] == "bgm":
                music["entries"].setdefault(row["id"],{"id":row["id"],"title":row["title"],"description":row["description"],"revision":0})
            if row["kind"] == "background" and row["id"] in known and row["id"] not in removed and identity_data and row["id"] not in identity_data.locations:
                identity_data.locations[row["id"]] = LocationBible(location_id=row["id"],description=row["description"])
            if row["kind"] == "background" and row["id"] not in known | removed:
                location = Location(location_id=row["id"],name=row["title"],description=row["description"])
                catalog["added"].append(location.model_dump(mode="json",by_alias=True))
                known.add(row["id"])
                production.location_reference_sets[row["id"]] = LocationReferenceSet().model_dump(mode="json")
                if identity_data:
                    identity_data.locations[row["id"]] = LocationBible(location_id=row["id"],description=row["description"])
                added_background = True
            elif row["kind"] == "prop":
                if row["id"] in library.get("removedProps",[]):
                    continue
                modes = [mode for mode in row["modes"] if mode not in library.get("removedPropModes",{}).get(row["id"],[])]
                if not modes:
                    continue
                if row["id"] not in library["props"]:
                    library["props"][row["id"]] = {"title":row["title"],"description":row["description"],"modes":modes}
                else:
                    library["props"][row["id"]]["modes"] = list(dict.fromkeys([*library["props"][row["id"]]["modes"],*modes]))
                if identity_data and row["id"] not in identity_data.props:
                    identity_data.props[row["id"]] = Prop(prop_id=row["id"],description=row["description"])
            elif row["kind"] == "detail_background":
                lid,detail_id = row["locationId"],row["detailId"]
                if lid in known and lid not in removed:
                    spec = production.location_reference_sets.setdefault(lid,LocationReferenceSet().model_dump(mode="json"))
                    if detail_id in spec.get("removed_detail_ids",[]):
                        continue
                    spec.setdefault("detail_references",{}).setdefault(detail_id,{
                        "id":detail_id,"area":row["title"],"description":row["description"],
                        "key":"backgrounds:"+lid+"__detail_"+detail_id,"role":"local_detail_only","status":"draft","draftRevision":1})
        original["production.json"] = production.model_dump(mode="json",by_alias=True)
        for name,value in original.items():
            if before[name] != input_hash(value):
                atomic_write(path/name,json.dumps(value,ensure_ascii=False,indent=2))
        if identity_data and identity_data.model_dump_json(by_alias=True) != identity_before:
            identity_data.approval = None
            identity._write(identity_data)
        if added_background:
            for gate in ("backgrounds","assets"):
                manifest.asset_approvals.pop(gate,None)
            manager.save(manifest)
        requests = {"requests":list(rows.values())}
        request_path = path/"storyboard_asset_requests.json"
        if not request_path.exists() or read_json(request_path) != requests:
            atomic_write(request_path,json.dumps(requests,ensure_ascii=False,indent=2))
        return list(rows.values())


def requests(manager,world_id,kind=None):
    path = manager.project_path(world_id)/"storyboard_asset_requests.json"
    rows = read_json(path).get("requests",[]) if path.exists() else []
    return [row for row in rows if kind is None or row["kind"] == kind]
