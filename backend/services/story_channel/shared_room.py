"""Versioned 3D room panoramas and cached shot-specific perspective references."""
import json
from pathlib import Path

from .files import atomic_write,input_hash
from .media_utils import file_hash
from .models import GeneratedAsset,utc_now
from .asset_models import AssetRecord
from .room_geometry import MODEL,scene_from_spec,render_panorama


def enabled(spec):
    return (spec or {}).get("generation_method") == "shared_3d" and (spec or {}).get("reference_type") in ("KEY","KEY_360")


def layout_signature(spec):
    return input_hash({key:spec.get(key) for key in ("generation_method","floor_plan","object_layout","camera_plan","camera_plan_signature")})


def task(lid,spec,task_type=None):
    if task_type is None:
        from .assets import Task
        task_type = Task
    return task_type("backgrounds",lid+"__room_panorama",
        MODEL+": shared room, one true 360x180 panorama. "+layout_signature(spec),operation="room_panorama")


def current(engine,state,lid):
    spec = state.location_reference_sets.get(lid,{})
    record = state.records.get("backgrounds:"+lid+"__room_panorama")
    if not enabled(spec) or not engine.record_current(record,task(lid,spec),state):
        return None
    return record if valid_asset(engine,record,spec) else None


def valid_asset(engine,record,spec):
    if not record or not record.asset:
        return False
    try:
        params = record.asset.generation_params["sharedRoom"]
        if params["layoutSignature"] != layout_signature(spec):
            return False
        if file_hash(engine.local_path(params["sceneFile"])) != params["sceneHash"]:
            return False
    except (OSError,ValueError,KeyError):
        return False
    return True


def generate(engine,state,job,config):
    lid = job.subject.rsplit("__",1)[0]
    spec = state.location_reference_sets[lid]
    scene = scene_from_spec(spec)
    scene["styleRules"] = state.project_style_rules
    destination = Path(config.options["output_path"])
    scene_file = destination.with_suffix(".scene.json")
    atomic_write(scene_file,json.dumps(scene,ensure_ascii=False,indent=2))
    render_panorama(scene,destination,width=1536,stop_requested=engine.stop_requested,
                    progress=lambda fraction:config.options["job_update"]("local-room","running",fraction))
    params = {"model":MODEL,"sceneFile":scene_file.relative_to(engine.path).as_posix(),"sceneHash":file_hash(scene_file),
        "layoutSignature":layout_signature(spec),"coverageDegrees":[360,180],"cameraCenter":scene["camera"],
        "representation":scene["representation"],"inferredHeights":[item["source_name"] for item in scene["objects"] if item["heightInferred"]]}
    return GeneratedAsset(type="image",provider="local",model=MODEL,file_path=str(destination),
                          prompt_hash=input_hash(job.prompt),generation_params={"sharedRoom":params})


def perspective_keys(engine,state,lid,start,end,*,pitch=0,fov=70,max_references=3):
    """Exact shot geometry projected from immutable panorama pixels; appearance is separate."""
    from .background_panorama import project_view
    from .dual_panorama import root
    source = current(engine,state,lid)
    if not source or not state.background_approvals.get(lid):
        return []
    if not -89 <= pitch <= 89 or not 1 <= fov < 170:
        raise ValueError("Shot pitch must be -89..89 degrees and FOV 1..169 degrees.")
    selected = []
    # Start/end and, for longer turns, a midpoint; callers segment long orbits.
    angles = [start] if abs(end-start)<1e-6 else [start,end]
    if abs(end-start)>45 and max_references>2:
        angles.insert(1,(start+end)/2)
    for yaw in angles[:max_references]:
        metadata = {"sourceAssetId":source.asset.asset_id,"sourceHash":source.file_hash,"yaw":float(yaw)%360,
            "pitch":float(pitch),"fov":float(fov),"size":[1024,576],"model":MODEL,
            "cameraCenter":source.asset.generation_params["sharedRoom"]["cameraCenter"]}
        digest = input_hash(metadata)
        subject = lid+"__shot_"+digest[:20]
        key = "backgrounds:"+subject
        if key in selected:
            continue
        path = root(engine,lid)/"shots"/source.asset.asset_id/(digest[:24]+".png")
        sidecar = path.with_suffix(".json")
        intact = False
        if sidecar.exists() and path.exists():
            from .file_cache import read_json
            try:
                saved = read_json(sidecar)
                intact = saved.get("projection") == metadata and saved.get("fileHash") == file_hash(path)
            except (OSError,ValueError,AttributeError):
                pass
        if not intact:
            project_view(engine.local_path(source.asset.file_path),path,yaw=metadata["yaw"],pitch=pitch,fov=fov,size=(1024,576))
            atomic_write(sidecar,json.dumps({"projection":metadata,"fileHash":file_hash(path)},indent=2))
        checksum = file_hash(path)
        asset = GeneratedAsset(asset_id="roomshot_"+digest[:32],type="image",provider="local",model=MODEL,
            file_path=path.relative_to(engine.path).as_posix(),prompt_hash=digest,generation_params={"roomProjection":metadata,
                "referenceHashes":{"backgrounds:"+lid+"__room_panorama":source.file_hash}})
        state.records[key] = AssetRecord(stage="backgrounds",subject=subject,status="succeeded",asset=asset,
            file_hash=checksum,input_hash=digest,revision=1,updated_at=utc_now())
        selected.append(key)
    return selected


def merge_references(engine,state,keys,pack):
    """Make on-demand animation segment references available to the renderer."""
    for key in keys:
        record = state.records.get(key)
        if record and record.asset and (record.asset.generation_params.get("roomProjection") or
                record.asset.generation_params.get("canonicalEnhancement")) and engine._intact(record):
            pack[key] = {"file":record.asset.file_path,"sha256":record.file_hash,"assetId":record.asset.asset_id,
                         "referenceHashes":record.asset.generation_params.get("referenceHashes",{})}
