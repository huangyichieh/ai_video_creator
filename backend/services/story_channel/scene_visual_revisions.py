"""Immutable scene-video history, explicit selection and integrity checks."""
import json
from copy import deepcopy

from .files import atomic_write,input_hash,read_text
from .locking import project_lock
from .media_utils import file_hash
from .models import utc_now,ApprovalRecord


def folder(service,sid):
    from pydantic import TypeAdapter
    from .models import Identifier
    TypeAdapter(Identifier).validate_python(sid)
    return service.assets.local_path("history/visual_scenes/"+sid)


def archive(service,sid,record):
    if record.get("status") != "succeeded" or not record.get("asset"):
        return
    from .models import GeneratedAsset
    asset = GeneratedAsset.model_validate(record["asset"])
    if asset.type != "video" or asset.source_scene_id not in (None,sid):
        raise ValueError("This revision does not belong to the selected scene video.")
    path = folder(service,sid)/(asset.asset_id+".json")
    if not path.exists():
        atomic_write(path,json.dumps(record,ensure_ascii=False,indent=2,default=str))


def revisions(service,sid):
    values = {}
    for path in sorted(folder(service,sid).glob("*.json")):
        try:
            record = json.loads(read_text(path))
            if record.get("status") == "succeeded" and record.get("asset"):
                from .models import GeneratedAsset
                asset = GeneratedAsset.model_validate(record["asset"])
                if asset.type != "video" or asset.source_scene_id not in (None,sid):
                    continue
                values.setdefault(record["asset"]["assetId"],record)
        except (OSError,ValueError,KeyError):
            continue
    current = service.visuals().get(sid,{})
    if current.get("asset") and current.get("status") == "succeeded":
        values.setdefault(current["asset"]["assetId"],current)
    return sorted(values.values(),key=lambda r:(r.get("revision",0),r["asset"].get("createdAt","")),reverse=True)


def next_revision(service,sid,previous):
    return max([previous.get("revision",0),*[r.get("revision",0) for r in revisions(service,sid)]])+1


def intact(service,record,formats=()):
    try:
        if not record.get("asset") or file_hash(service.assets.local_path(record["asset"]["filePath"])) != record["fileHash"]:
            return False
        versions = record.get("formats",{})
        if formats and not versions:
            known = record["asset"].get("generationParams",{}).get("deliveryFormats",["landscape_16_9"])
            if not set(formats) <= set(known):
                return False
        if formats and versions and not set(formats) <= versions.keys():
            return False
        if formats and not versions and len(formats)>1:
            return False
        return all(version.get("status") == "succeeded" and version.get("asset") and
            file_hash(service.assets.local_path(version["asset"]["filePath"])) == version["fileHash"]
            for version in versions.values())
    except (OSError,KeyError,ValueError):
        return False


def apply(service,sid,asset_id,options,*,expected):
    from .animation_formats import delivery_formats
    with project_lock(service.path):
        values = service.visuals()
        previous = values.get(sid,{})
        if input_hash(previous) != expected:
            raise ValueError("The scene video changed. Reopen saved revisions before applying.")
        selected = next((r for r in revisions(service,sid) if r["asset"]["assetId"] == asset_id),None)
        if not selected or not intact(service,selected,delivery_formats(options)):
            raise ValueError("This revision is missing, modified or lacks the current delivery formats.")
        archive(service,sid,previous)
        record = deepcopy(selected)
        record.setdefault("generationSourceHash",selected.get("sourceHash"))
        source_hash=service.visual_hash(sid,options)
        record.update(sourceHash=source_hash,approval=ApprovalRecord(content_hash=source_hash).model_dump(mode="json",by_alias=True),error="",status="succeeded",
            appliedAt=utc_now().isoformat(),appliedFromAssetId=asset_id)
        values[sid] = record
        atomic_write(service.path/"visual_scenes.json",json.dumps(values,ensure_ascii=False,indent=2,default=str))
        return record
