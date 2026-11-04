"""Raw fixed-camera projections and reviewed AI appearance references for shared rooms."""
import json

from .files import input_hash
from .models import GeneratedAsset

ANGLES = tuple(range(0, 360, 45))
MODEL = "room-canonical-v1"


def tasks(lid, spec, task_type):
    from .shared_room import task
    panorama = task(lid, spec, task_type)
    raw, enhanced = [], []
    for yaw in ANGLES:
        subject = f"{lid}__raw_{yaw:03d}"
        raw.append(task_type("backgrounds", subject,
            f"{MODEL}: fixed camera projection yaw={yaw}, pitch=0, FOV=70, size=1536x1024.",
            refs=[panorama.key], operation="room_canonical_raw"))
        enhanced.append(task_type("backgrounds", f"{lid}__enhanced_{yaw:03d}",
            f"{MODEL}: enhance appearance only at yaw {yaw}; spatial truth: " + json.dumps({
                "floorPlan": spec.get("floor_plan"), "objectLayout": spec.get("object_layout"),
                "cameraPlan": spec.get("camera_plan")}, ensure_ascii=False),
            refs=[raw[-1].key, panorama.key], operation="room_canonical_enhanced"))
    return [panorama, *raw, *enhanced]


def raw_generate(engine, state, job, config):
    from .background_panorama import project_view
    source = state.records[job.refs[0]]
    yaw = int(job.subject.rsplit("_", 1)[1])
    destination = config.options["output_path"]
    project_view(engine.local_path(source.asset.file_path), destination, yaw=yaw, pitch=0,
                 fov=70, size=(1536,1024))
    return GeneratedAsset(type="image", provider="local", model=MODEL, file_path=destination,
        prompt_hash=input_hash(job.prompt), generation_params={"canonicalProjection": {
            "yaw":yaw, "pitch":0, "fov":70, "size":[1536,1024],
            "sourceAssetId":source.asset.asset_id,
            "cameraCenter":source.asset.generation_params["sharedRoom"]["cameraCenter"]}})


def enhancement_request(engine, state, job):
    from .style_rules import apply_rules, style_reference
    references = [engine.local_path(state.records[key].asset.file_path) for key in job.refs]
    prompt = ("Image 1 is the authoritative raw 3D camera render; image 2 is the full 360-degree "
        "low-detail geometry/context panorama, NOT the output framing. Enhance ONLY image 1. "
        "Keep its exact perspective, aspect ratio, camera center, height, pitch, FOV and framing. "
        "Shared 3D room and object coordinates are spatial truth. Add visual detail, materials, textures, "
        "lighting and realistic/stylized object appearance. NEVER move, duplicate, remove or resize major "
        "fixed objects, doors, windows or walls. Preserve silhouettes, occlusion, object IDs and footprints. "
        "Objects hidden from this camera must remain hidden. Keep lighting coherent across all views. "
        "No people, reflections, labels or visible text. Do not copy panorama framing.\n" + job.prompt)
    prompt = apply_rules(prompt, state.project_style_rules)
    lid = job.subject.rsplit("__",1)[0]
    if lid in state.background_images:
        from .background_library import BackgroundLibrary
        references.append(BackgroundLibrary(engine).image(state,lid))
        prompt += f"\nImage {len(references)} is STYLE ONLY; copy no objects or layout."
    return style_reference(engine,state,prompt,references)


def records(engine,state,lid,*,enhanced=True):
    expected = [t for t in tasks(lid,state.location_reference_sets[lid],type_task())
                if t.operation == ("room_canonical_enhanced" if enhanced else "room_canonical_raw")]
    return [state.records[t.key] for t in expected if engine.record_current(state.records.get(t.key),t,state)]


def type_task():
    from .assets import Task
    return Task


def nearest(engine,state,lid,start,end,limit):
    available = records(engine,state,lid)
    chosen = []
    for angle in (start,end,(start+end)/2):
        ranked = sorted(available,key=lambda r:abs((int(r.subject.rsplit('_',1)[1])-angle+180)%360-180))
        for record in ranked:
            key = "backgrounds:"+record.subject
            if key not in chosen:
                chosen.append(key)
                break
        if len(chosen) >= limit:
            break
    return chosen[:limit]


def shot_references(engine,state,lid,start,end,*,pitch=0,fov=70,max_references=3):
    from .background_library import BackgroundLibrary
    from .shared_room import perspective_keys
    if not -89 <= pitch <= 89 or not 1 <= fov < 170:
        raise ValueError("Shot pitch must be -89..89 degrees and FOV 1..169 degrees.")
    if not BackgroundLibrary(engine).approved(lid,state,tasks(lid,state.location_reference_sets[lid],type_task())):
        return []
    # Exact framing from geometry, appearance from the nearest reviewed canonical images.
    visual = nearest(engine,state,lid,start,end,max(1,max_references-1))
    if max_references == 1:
        return visual
    raw = perspective_keys(engine,state,lid,start,start,pitch=pitch,fov=fov,max_references=1)
    return [*raw,*visual][:max_references]


def publish_pack(engine,state,lid,signature):
    from .dual_panorama import root
    from .files import atomic_write
    panorama = state.records[f"backgrounds:{lid}__room_panorama"]
    pack = {"signature":signature,"spatialTruth":"shared_3d_room",
            "geometryContext":"room_panorama","visualTruth":"canonical_enhanced",
            "panorama":{"file":panorama.asset.file_path,"sha256":panorama.file_hash,
                        "assetId":panorama.asset.asset_id},
            "sharedRoom":panorama.asset.generation_params["sharedRoom"],
            "geometryReview":"human_confirmed","views":[]}
    for yaw in ANGLES:
        pair = {"yaw":yaw,"pitch":0,"fov":70}
        for kind in ("raw","enhanced"):
            record = state.records[f"backgrounds:{lid}__{kind}_{yaw:03d}"]
            pair[kind] = {"file":record.asset.file_path,"sha256":record.file_hash,
                          "assetId":record.asset.asset_id,"revision":record.revision}
        pack["views"].append(pair)
    atomic_write(root(engine,lid)/"location_reference_pack.json",json.dumps(pack,ensure_ascii=False,indent=2))
