"""Episode-scoped, file-backed prerequisites for storyboard production."""
import json
import re
import unicodedata

from .file_cache import read_json
from .files import input_hash,atomic_write
from .media_utils import file_hash

GROUPS = ("Background","Props","BGM","SFX")


def sound_id(text):
    return "sfx_"+input_hash(re.sub(r"\s+"," ",unicodedata.normalize("NFKC",text)).strip().casefold())[:16]


def document(path,name):
    return read_json(path/name) if (path/name).exists() else {}


def intact(path,item):
    from .bgm_library import ready
    return bool(item.get("file") and ready(path,item))


def sounds(manager,project_id):
    return document(manager.project_path(project_id),"storyboard_sfx.json").get("entries",{})


def bind_sound(manager,project_id,name,relative,*,bgm_id=None,sfx_id=None,expected=None,provenance=None):
    from .locking import project_lock
    path = manager.project_path(project_id)
    file = (path/relative).resolve()
    if not file.is_relative_to(path.resolve()) or not file.is_file():
        raise ValueError("Choose an audio file inside this project.")
    receipt = document(path,relative+".source.json")
    if not receipt.get("source","").strip() or (not bgm_id and receipt.get("kind") == "bgm"):
        raise ValueError("Import a sound effect with its source/license first.")
    with project_lock(path):
        identity = bgm_id or sfx_id or sound_id(name)
        from pydantic import TypeAdapter
        from .models import Identifier
        TypeAdapter(Identifier).validate_python(identity)
        filename = "storyboard_bgm.json" if bgm_id else "storyboard_sfx.json"
        entries = document(path,filename).get("entries",{})
        if expected is not None and input_hash(entries.get(identity)) != expected:
            raise ValueError("This audio asset changed during the request. The generated file was retained; reload before replacing it.")
        old = entries.get(identity)
        if old:
            atomic_write(path/"history"/"storyboard_audio"/("BGM" if bgm_id else "SFX")/identity/(input_hash(old)+".json"),json.dumps(old,ensure_ascii=False,indent=2))
        entries[identity] = {"name":name,"file":relative,"fileHash":file_hash(file),"source":receipt["source"]}
        if provenance:
            entries[identity]["provenance"] = provenance
        atomic_write(path/filename,json.dumps({"entries":entries},ensure_ascii=False,indent=2))


def upload_sound(manager,project_id,name,data,filename,source,*,bgm_id=None):
    from .rendering import RenderPipeline
    relative = RenderPipeline(manager,project_id).import_audio(data,filename,source)
    bind_sound(manager,project_id,name,relative,bgm_id=bgm_id)


def available(manager,project_id):
    """Asset preparation opens after current storyboard approval; prompts may wait."""
    from .creative import Storyboard
    from .modules import STEPS
    from .file_cache import read_model
    path = manager.project_path(project_id)
    try:
        manifest = manager.load(project_id)
        approval = manifest.approvals.get("storyboard")
        required = STEPS[:STEPS.index("storyboard")+1]
        if not approval or any(name not in manifest.steps or manifest.steps[name].status != "succeeded" for name in required):
            return False
        board = read_model(path/"scenes.json",Storyboard)
        return (manifest.steps["storyboard"].output_hash == input_hash(board.model_dump(mode="json",by_alias=True))
            and approval.content_hash == input_hash({name:manifest.steps[name].output_hash for name in required}))
    except (OSError,ValueError,KeyError):
        return False


def check(manager,project_id):
    from .assets import AssetPipeline
    from .background_library import BackgroundLibrary,locations
    from .bgm_library import entries,ready,mood_id
    engine = AssetPipeline(manager,project_id)
    if not (engine.path/"scenes.json").exists():
        return [{"Category":"Background","Asset":"Storyboard","Scenes":"","Status":"Create storyboard","Ready":False}]
    board = engine.creative.artifact("storyboard")
    state = engine.load()
    library = BackgroundLibrary(engine)
    tasks = engine.tasks(state,references_only=True)
    media = document(engine.path,"library_media.json")
    music = {**entries(manager,project_id),**document(engine.path,"storyboard_bgm.json").get("entries",{})}
    audio = sounds(manager,project_id)
    sfx_metadata = document(engine.path,"sfx_library.json").get("entries",{})
    names = {loc.location_id:loc.name for loc in locations(engine.creative)}
    owner = manager.load(project_id).world_project_id
    shared_sets = document(manager.project_path(owner),"production.json").get("locationReferenceSets",{}) if owner else {}
    declared = {item.prop_id:item for item in board.props}
    declared_details = {(item.location_id,item.detail_id) for item in board.detail_backgrounds}
    identity = document(engine.path,"identity_library.json")
    rows = {}
    def add(category,key,title,sid,exists,reason="Needs creation",*,asset_type=None,content=None,previews=(),file_exists=None):
        row = rows.setdefault((category,key),{"Category":category,**({"Type":asset_type} if asset_type else {}),"Asset":title,"ID":key,"Scenes":[],"Status":"Exists" if exists else reason,"Ready":bool(exists),
            "FileExists":bool(exists if file_exists is None else file_exists),"ContentHash":input_hash(content) if exists else None,"PreviewFiles":list(previews)})
        if sid not in row["Scenes"]:
            row["Scenes"].append(sid)
    for scene in board.scenes:
        lid,sid = scene.location_id,scene.scene_id
        spec = state.location_reference_sets.get(lid,{})
        masters = [state.records[t.key] for t in tasks if t.stage == "backgrounds" and library.subject_id(t.subject) == lid and t.key in state.records and state.records[t.key].asset]
        expected_masters = [task for task in tasks if task.stage == "backgrounds" and library.subject_id(task.subject) == lid]
        files_exist = bool(expected_masters) and all(engine._intact(state.records.get(task.key)) for task in expected_masters)
        exists = lid in names and files_exist
        add("Background",lid,lid+"("+names.get(lid,lid)+")",sid,exists,"Generate master images",asset_type="Background",
            content=[record.asset.asset_id for record in masters],previews=[record.asset.file_path for record in masters],file_exists=files_exist)
        if (lid,scene.target_area) in declared_details or scene.target_area in {**shared_sets.get(lid,{}).get("detail_references",{}),**spec.get("detail_references",{})}:
            ref = spec.get("detail_references",{}).get(scene.target_area)
            record = state.records.get(ref.get("key")) if ref else None
            add("Background",lid+"__detail_"+scene.target_area,lid+"("+names.get(lid,lid)+" · "+scene.target_area+")",sid,bool(ref and intact(engine.path,{"file":ref.get("file"),"fileHash":ref.get("hash")})),"Generate detail / refresh shared assets",asset_type="Detail background",
                content={"reference":ref,"assetId":record.asset.asset_id if record and record.asset else None},previews=[ref["file"]] if ref and ref.get("file") else [],
                file_exists=bool(ref and intact(engine.path,{"file":ref.get("file"),"fileHash":ref.get("hash")})))
        for pid in scene.prop_ids:
            item = media.get("props",{}).get(pid,identity.get("props",{}).get(pid,{}))
            modes = declared[pid].modes if pid in declared else item.get("modes",["default"])
            for mode in modes:
                key = "prop:"+pid+":"+mode
                versions = media.get("visuals",{}).get(key,[])
                chosen = media.get("activeVisuals",{}).get(key,{}).get("revision")
                version = next((v for v in versions if v["revision"] == chosen),versions[-1] if chosen is None and versions else {})
                asset = version.get("asset",{})
                exists = intact(engine.path,{"file":asset.get("filePath",asset.get("file_path","")),"fileHash":version.get("fileHash")})
                add("Props",key,item.get("title",pid)+" · "+mode,sid,exists,"Generate prop visual",content={"prop":item,"visual":version},previews=[asset.get("filePath",asset.get("file_path",""))] if asset else [])
        if scene.bgm_id or scene.bgm_mood.strip():
            key = scene.bgm_id or mood_id(scene.bgm_mood)
            item = music.get(key,{})
            add("BGM",key,item.get("title",item.get("name",scene.bgm_mood or key)),sid,ready(engine.path,item),"Import BGM audio",content=item,previews=[item["file"]] if item.get("file") else [])
        for text in scene.sfx:
            if text.strip():
                key = sound_id(text)
                item = audio.get(key,{})
                metadata = sfx_metadata.get(key,{})
                edited_metadata = metadata and (metadata.get("title",text) != text or metadata.get("description",text) != text)
                add("SFX",key,metadata.get("title",text),sid,intact(engine.path,item),"Import / link SFX audio",
                    content={"binding":item,"definition":metadata} if edited_metadata else item,previews=[item["file"]] if item.get("file") else [])
    result = []
    for row in rows.values():
        approved = bool(row["Ready"])
        result.append({**row,"Scenes":", ".join(row["Scenes"]),"Approved":approved,"Approval":"Automatically approved" if approved else "Needs creation"})
    return result


def approve(manager,project_id,category,identity,*,expected):
    """Compatibility check for older callers; intact created assets need no receipt."""
    from .locking import project_lock
    path = manager.project_path(project_id)
    with project_lock(path):
        row = next((r for r in check(manager,project_id) if r["Category"] == category and r.get("ID") == identity),None)
        if not row or not row["Ready"]:
            raise ValueError("Create this asset and refresh shared assets before approving it.")
        if row["ContentHash"] != expected:
            raise ValueError("This asset changed. Review its current version before approving.")
    return row


def require(manager,project_id):
    manifest = manager.load(project_id)
    if manifest.workflow_version != "world_episode" or not manifest.world_project_id:
        return
    missing = [row for row in check(manager,project_id) if not row["Ready"]]
    if missing:
        from .pipeline import ApprovalRequired
        raise ApprovalRequired("Complete Animations > Check assets before continuing: "+"; ".join(row["Category"]+": "+row["Asset"] for row in missing[:5]))
