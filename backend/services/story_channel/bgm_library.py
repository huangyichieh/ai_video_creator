"""Reusable music requirements and licensed audio, independent of scene timing."""
import json
import re
import unicodedata

from .files import atomic_write,input_hash
from .file_cache import read_json
from .locking import project_lock
from .media_utils import file_hash
from .creative import StoryboardBGM


def mood_id(text):
    normalized = re.sub(r"\s+"," ",unicodedata.normalize("NFKC",text)).strip().casefold()
    return "bgm_"+input_hash(normalized)[:16]


def entries(manager,project_id):
    file = manager.project_path(project_id)/"bgm_library.json"
    return read_json(file).get("entries",{}) if file.exists() else {}


def add(manager,project_id,identity,title,description):
    item = StoryboardBGM(bgm_id=identity.strip(),title=title.strip(),description=description.strip())
    path = manager.project_path(project_id)
    with project_lock(path):
        items = entries(manager,project_id)
        if item.bgm_id in items:
            raise ValueError("This BGM ID already exists.")
        items[item.bgm_id] = {"id":item.bgm_id,"title":item.title,"description":item.description,"revision":0}
        atomic_write(path/"bgm_library.json",json.dumps({"entries":items},ensure_ascii=False,indent=2))


def attach(manager,project_id,identity,data,filename,source):
    from .rendering import RenderPipeline
    if identity not in entries(manager,project_id):
        raise ValueError("Choose an existing BGM draft.")
    # import_audio validates the audio stream and records its license/source.
    relative = RenderPipeline(manager,project_id).import_audio(data,filename,source)
    path = manager.project_path(project_id)
    with project_lock(path):
        items = entries(manager,project_id)
        item = items[identity]
        if item.get("file"):
            atomic_write(path/"history"/"bgm"/identity/(str(item["revision"])+".json"),json.dumps(item,ensure_ascii=False,indent=2))
        item.update(file=relative,source=source.strip(),fileHash=file_hash(path/relative),revision=item.get("revision",0)+1)
        receipt = path/(relative+".source.json")
        atomic_write(receipt,json.dumps({"file":relative,"source":source.strip(),"kind":"bgm","bgmId":identity},ensure_ascii=False))
        atomic_write(path/"bgm_library.json",json.dumps({"entries":items},ensure_ascii=False,indent=2))
    return item


def ready(path,item):
    try:
        file = (path/item["file"]).resolve()
        if not (file.is_relative_to(path.resolve()) and file.is_file() and file_hash(file) == item["fileHash"]):
            return False
        provenance = item.get("provenance",{})
        if provenance.get("provider") == "pixabay":
            from .pixabay_music import track_id,LICENSE_URL
            track = provenance["track"]
            evidence = (path/track["licenseEvidence"]).resolve()
            return (track_id(track["url"]) == track["id"] and track.get("commercialUseConfirmed") is True
                and track.get("contentIdStatus") == "not_registered" and track.get("licenseUrl") == LICENSE_URL
                and evidence.is_relative_to(path.resolve()) and evidence.is_file() and file_hash(evidence) == track["licenseEvidenceHash"])
        return True
    except (KeyError,OSError,ValueError):
        return False


def scene_music(manager,project_id,scene):
    items = entries(manager,project_id)
    file = manager.project_path(project_id)/"storyboard_bgm.json"
    if file.exists():
        items = {**items,**read_json(file).get("entries",{})}
    item = items.get(scene.bgm_id or mood_id(scene.bgm_mood),{})
    return item if ready(manager.project_path(project_id),item) else None


def import_selected(manager,project_id,identities):
    """An explicit storyboard selection imports only its chosen music tracks."""
    manifest = manager.load(project_id)
    if not manifest.world_project_id:
        return
    from .files import atomic_copy
    source = manager.project_path(manifest.world_project_id)
    target = manager.project_path(project_id)
    shared = entries(manager,manifest.world_project_id)
    with project_lock(target):
        local = entries(manager,project_id)
        changed = False
        for identity in identities:
            # Keep an existing frozen revision. Refresh shared references to adopt a replacement.
            if local.get(identity,{}).get("file"):
                continue
            item = shared.get(identity)
            if not item or not ready(source,item):
                continue
            destination = target/item["file"]
            atomic_copy(source/item["file"],destination)
            if file_hash(destination) != item["fileHash"]:
                raise ValueError("BGM audio changed while importing; try saving again.")
            local[identity] = dict(item)
            atomic_write(target/(item["file"]+".source.json"),json.dumps({"file":item["file"],"source":item["source"],"kind":"bgm","bgmId":identity},ensure_ascii=False))
            changed = True
        if changed:
            atomic_write(target/"bgm_library.json",json.dumps({"entries":local},ensure_ascii=False,indent=2))
