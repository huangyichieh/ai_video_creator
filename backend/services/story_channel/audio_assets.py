"""Reusable audio drafts and bindings shared with episode audio cards."""
import json
from pathlib import Path

from .files import atomic_write,input_hash
from .storyboard_asset_check import document,sounds,sound_id,intact,bind_sound


def items(manager,project_id,kind):
    path = manager.project_path(project_id)
    if kind == "BGM":
        from .bgm_library import entries
        return entries(manager,project_id)
    values = document(path,"sfx_library.json").get("entries",{})
    from .storyboard_assets import requests
    for row in requests(manager,project_id,"sfx"):
        values.setdefault(row["id"],{"title":row["title"],"description":row["description"]})
    for identity,binding in sounds(manager,project_id).items():
        values[identity] = {**values.get(identity,{"title":binding["name"],"description":binding["name"]}),**binding}
    bound = {item.get("file") for item in values.values()}
    from .bgm_library import entries
    music_files = {item.get("file") for item in entries(manager,project_id).values()}
    music_files.update(item.get("file") for item in document(path,"pixabay_music.json").get("tracks",{}).values())
    from .media_utils import file_hash
    for receipt in (path/"audio"/"library").glob("*.source.json"):
        data = document(path,receipt.relative_to(path))
        relative = data.get("file","")
        if data.get("kind") == "bgm" or not relative or relative in bound or relative in music_files:
            continue
        file = (path/relative).resolve()
        if file.is_relative_to(path.resolve()) and file.is_file():
            title = data.get("title") or Path(relative).stem
            values.setdefault(sound_id(title),{"title":title,"description":title,**data,"fileHash":file_hash(file)})
    return values


def add_sfx(manager,project_id,title,description):
    from .locking import project_lock
    title,description = title.strip(),description.strip()
    if not title or not description:
        raise ValueError("Enter an SFX title and description.")
    path = manager.project_path(project_id)
    with project_lock(path):
        values = document(path,"sfx_library.json").get("entries",{})
        identity = sound_id(title)
        if identity in items(manager,project_id,"SFX"):
            raise ValueError("An SFX with this title already exists.")
        values[identity] = {"title":title,"description":description}
        atomic_write(path/"sfx_library.json",json.dumps({"entries":values},ensure_ascii=False,indent=2))
    return identity


def requirement(manager,project_id,kind,identity):
    manifest = manager.load(project_id)
    if manifest.world_project_id or manifest.workflow_version != "world_episode" and (manager.project_path(project_id)/"scenes.json").exists():
        from .storyboard_asset_check import check
        return next((row for row in check(manager,project_id) if row["Category"] == kind and row.get("ID") == identity),None)
    item = items(manager,project_id,kind).get(identity)
    return {"Category":kind,"ID":identity,"Asset":item["title"],"Description":item.get("description",item["title"])} if item else None


def binding(manager,project_id,kind,identity):
    path = manager.project_path(project_id)
    if kind == "BGM" and not manager.load(project_id).world_project_id:
        current = items(manager,project_id,kind).get(identity,{})
        if intact(path,current):
            return current
    value = document(path,"storyboard_bgm.json" if kind == "BGM" else "storyboard_sfx.json").get("entries",{}).get(identity,{})
    return value if intact(path,value) else items(manager,project_id,kind).get(identity,{})


def publish(manager,project_id,kind,identity,title,relative,*,expected=None,provenance=None,_share=True,shared_expected=None):
    bind_sound(manager,project_id,title,relative,bgm_id=identity if kind == "BGM" else None,
        sfx_id=identity if kind == "SFX" else None,expected=expected,provenance=provenance)
    if _share and manager.load(project_id).world_project_id:
        from .check_asset_sync import share_audio
        share_audio(manager,project_id,kind,identity,expected=shared_expected)
    if kind != "BGM" or manager.load(project_id).world_project_id:
        return
    from .locking import project_lock
    from .bgm_library import entries
    path = manager.project_path(project_id)
    with project_lock(path):
        values = entries(manager,project_id)
        old = values.get(identity)
        if old is None:
            return
        if old.get("file"):
            atomic_write(path/"history"/"bgm"/identity/(str(old.get("revision",0))+".json"),json.dumps(old,ensure_ascii=False,indent=2))
        current = document(path,"storyboard_bgm.json")["entries"][identity]
        values[identity] = {**old,**current,"revision":old.get("revision",0)+1}
        atomic_write(path/"bgm_library.json",json.dumps({"entries":values},ensure_ascii=False,indent=2))
        receipt = document(path,relative+".source.json")
        receipt.update(kind="bgm",bgmId=identity)
        atomic_write(path/(relative+".source.json"),json.dumps(receipt,ensure_ascii=False,indent=2))


def upload(manager,project_id,kind,identity,data,filename,source):
    from .rendering import RenderPipeline
    row = requirement(manager,project_id,kind,identity)
    if not row:
        raise ValueError("This audio draft no longer exists.")
    from .check_asset_sync import shared_audio_hash
    shared_expected = shared_audio_hash(manager,project_id,kind,identity)
    relative = RenderPipeline(manager,project_id).import_audio(data,filename,source)
    publish(manager,project_id,kind,identity,row["Asset"],relative,shared_expected=shared_expected)
    return relative
