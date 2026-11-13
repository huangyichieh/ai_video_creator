"""Directional-master approvals and retained historical panorama utilities."""
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from .files import atomic_write, input_hash, read_text
from .media_utils import file_hash

from .directional_masters import NAMES as MASTERS
MODEL = "four-directional-masters-110-v1"
COVERAGE = {"front": [-100,100], "back": [80,280],"east":[60,120],"west":[240,300]}
FOV = 70
PANORAMA_MODEL = "gpt-image-2.5-flare"
SOUTH_REFERENCE_POLICY = MODEL


def is_key(spec):
    return (spec or {}).get("reference_type") in ("KEY", "KEY_360") and (spec or {}).get("generation_method") != "shared_3d"


def root(engine, lid):
    path = engine.path / "locations" / lid
    if path.resolve().parent != (engine.path / "locations").resolve():
        raise ValueError("Location output must stay inside the project.")
    return path


def digest(engine, state, lid, name):
    key = f"backgrounds:{lid}__{name}"
    record = state.records.get(key)
    if not record or not record.asset:
        return None
    task = _master_tasks(engine,state).get(key)
    if not task or not engine.record_current(record, task, state):
        return None
    return input_hash([record.asset.asset_id, record.file_hash, record.applied_input_hash or record.input_hash])


def _master_tasks(engine,state):
    """Cache only the immutable master task definitions, never validity/approvals."""
    from .file_cache import version
    from .location_view_context import VIEW_SCOPE_VERSION
    names = ("manifest.json","world.json","characters.json","storyboard.json","prompts.json",
             "identity_library.json","background_library.json","animation.json")
    stamps = []
    for name in names:
        try:
            stamps.append(version(engine.path/name))
        except FileNotFoundError:
            stamps.append(None)
    stamp = (tuple(stamps),input_hash(state.location_reference_sets),MODEL,VIEW_SCOPE_VERSION,SOUTH_REFERENCE_POLICY,
        getattr(engine,"_individual_location_master",None))
    cached = getattr(engine,"_panorama_master_tasks",None)
    if cached is None or cached[0] != stamp:
        cached = (stamp,{task.key:task for task in engine.tasks(state,references_only=True) if task.operation == "location_panorama"})
        engine._panorama_master_tasks = cached
    return cached[1]


def approved(engine, state, lid, name):
    from .location_bridges import BRIDGES
    group = BRIDGES if name in BRIDGES else MASTERS
    records = [state.records.get(f"backgrounds:{lid}__{member}") for member in group]
    pair_ids = {record.asset.generation_params.get("locationPairId") if record and record.asset else None for record in records}
    if len(pair_ids) != 1:
        return False
    approval = state.location_master_approvals.get(lid + ":" + name)
    return bool(approval and approval.content_hash == digest(engine, state, lid, name))


def pair_signature(engine, state, lid):
    values = [digest(engine, state, lid, name) for name in MASTERS]
    if not all(values):
        return None
    spec = state.location_reference_sets[lid]
    return input_hash({"masters": values, "layout": {key: spec.get(key) for key in ("floor_plan", "object_layout", "camera_height_m")}, "model": MODEL})


def seam_info(engine, state, lid):
    path = root(engine, lid) / "panorama" / "seam_metadata.json"
    try:
        from .file_cache import read_json
        info = read_json(path)
        return info if info.get("signature") == pair_signature(engine, state, lid) else None
    except (OSError, ValueError):
        return None


def pair_ready(engine, state, lid):
    """The active reference pack ends at four approved directional masters."""
    return all(approved(engine,state,lid,name) for name in MASTERS)


def next_subjects(engine, state, lid):
    """Stop at every master approval; never spend on a yaw image."""
    for group in (MASTERS,):
        if any(not digest(engine,state,lid,name) for name in group):
            return [lid+"__"+name for name in group]
        cohort_ids = {state.records[f"backgrounds:{lid}__{name}"].asset.generation_params.get("locationPairId") for name in group}
        if len(cohort_ids) != 1:
            return [lid+"__"+name for name in group]
        if not all(approved(engine,state,lid,name) for name in group):
            return []
    return []


def restore_master_sources(engine,state):
    """Historical projection caches are retained, never rebuilt by the active flow."""
    return


def published_current(engine, state, task, record, *, qa=None):
    if not record or not record.asset:
        return False

    lid, suffix = task.subject.rsplit("__", 1)
    path = root(engine, lid) / "yaw" / ("yaw_" + suffix.removeprefix("view_") + ".png")
    try:
        from .file_cache import read_json
        info = read_json(path.with_suffix(".json"))
        qa = qa if qa is not None else seam_info(engine, state, lid) or {}
        return (file_hash(path) == record.file_hash and info.get("panoramaSignature") == qa.get("signature")
                and info.get("atlasHash") == qa.get("atlasHash") and info.get("model") == MODEL
                and info == record.asset.generation_params.get("projection"))
    except (OSError, ValueError):
        return False


def view_digests(engine, state, lid):
    tasks = {task.subject: task for task in engine.tasks(state)}
    result = {}
    ready = pair_ready(engine,state,lid)
    qa = seam_info(engine,state,lid) if ready else None
    for angle in range(0, 360, 45):
        subject = f"{lid}__view_{angle:03d}"
        task = tasks.get(subject)
        record = state.records.get("backgrounds:"+subject)
        current = bool(ready and task and record and record.asset and record.status == "succeeded"
                       and (record.applied_input_hash or record.input_hash) == engine._hash(task,state)
                       and engine._intact(record) and published_current(engine,state,task,record,qa=qa))
        result[angle] = input_hash([record.asset.asset_id,record.file_hash,record.applied_input_hash or record.input_hash]) if current else None
    return result


def approved_views(engine, state, lid):
    values = view_digests(engine, state, lid)
    return {angle: bool(value and (approval := state.location_yaw_approvals.get(f"{lid}:{angle}")) and approval.content_hash == value)
            for angle, value in values.items()}


def all_views_approved(engine, state, lid):
    return all(approved_views(engine, state, lid).values())


def set_view_approvals(engine, state, lid, angles=None, *, expected=None):
    from .models import ApprovalRecord
    values = view_digests(engine, state, lid)
    chosen = list(values) if angles is None else list(angles)
    if not chosen or any(angle not in values or not values[angle] for angle in chosen):
        raise ValueError("Generate current yaw projections before approving them.")
    if expected and any(values[angle] != expected.get(angle) for angle in chosen):
        raise ValueError("The yaw images changed. Review the current projections before approving.")
    for angle in chosen:
        state.location_yaw_approvals[f"{lid}:{angle}"] = ApprovalRecord(content_hash=values[angle])


def approve_views(engine, lid, angles=None, *, expected=None):
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    from .background_library import BackgroundLibrary
    with project_lock(engine.path), ledger_lock(engine.path):
        state, tasks = engine._refresh()
        manifest = engine.manager.load(engine.project_id)
        engine._require_stage("backgrounds", manifest)
        set_view_approvals(engine, state, lid, angles, expected=expected)
        engine._save(state)
    if all_views_approved(engine, state, lid):
        return BackgroundLibrary(engine).approve(lid)
    return engine.inspect()


def invalidate_world(engine, lid):
    """Remove only mutable published projections; immutable revision media remains."""
    folder = root(engine, lid)
    (folder / "panorama" / "atlas.png").unlink(missing_ok=True)
    (folder / "panorama" / "seam_metadata.json").unlink(missing_ok=True)
    for angle in range(0, 360, 45):
        for suffix in (".png", ".json"):
            (folder / "yaw" / (f"yaw_{angle:03d}" + suffix)).unlink(missing_ok=True)
    atomic_write(folder / "location_layout.json", json.dumps({"status": "needs_panorama_approval_and_seam_qa", "locationId": lid}))


def recheck(engine, lid):
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    with project_lock(engine.path), ledger_lock(engine.path):
        build_pair(engine, engine.load(), lid)
        return engine._refresh()[0]


def panorama_prompt(name="front_panorama"):
    from .directional_masters import prompt
    return prompt(name)


def _sample(pixels,longitude,latitude,side):
    height,width = pixels.shape[:2]
    angle = (longitude+180)%360-180 if side == "front" else longitude%360
    x = np.clip((angle-COVERAGE[side][0])/(COVERAGE[side][1]-COVERAGE[side][0])*width-.5,0,width-1)
    y = np.clip((90-latitude)/180*height-.5,0,height-1)
    x,y = np.broadcast_arrays(x,y)
    x0,y0 = np.floor(x).astype(int),np.floor(y).astype(int)
    x1,y1 = np.minimum(x0+1,width-1),np.minimum(y0+1,height-1)
    wx,wy = (x-x0)[...,None],(y-y0)[...,None]
    return (pixels[y0,x0]*(1-wx)+pixels[y0,x1]*wx)*(1-wy)+(pixels[y1,x0]*(1-wx)+pixels[y1,x1]*wx)*wy


def assess(front,back,bridges=None,bridge_warp=None):
    zones = []
    for side,start in (("east",80),("west",260)):
        lon = np.linspace(start+.05,start+19.95,128)[None,:]
        lat = np.linspace(80,-80,192)[:,None]
        a,b = _sample(front,lon,lat,"front")/255,_sample(back,lon,lat,"back")/255
        mae = float(np.abs(a-b).mean())
        edges = [np.concatenate([np.diff(p,axis=0).ravel(),np.diff(p,axis=1).ravel()]) for p in (a,b)]
        strength = [np.linalg.norm(edge) for edge in edges]
        agreement = float(np.dot(*edges)/max(float(strength[0]*strength[1]),1e-12)) if min(strength) > .5 else (1.0 if max(strength) <= .5 else 0.0)
        missing = bool(max(float(a.max()),float(b.max())) < .01)
        zones.append({"side":side,"yawRange":[start,start+20],"pixelMAE":mae,"edgeAgreement":agreement,
                      "geometryOrObjectConflictSuspected":agreement < .65,"missingRegion":missing,
                      "passed":mae <= .10 and agreement >= .65 and not missing})
        if bridges:
            from .bridge_warp import inverse_warp,options as warp_options
            settings = warp_options({"bridge_warp":bridge_warp or {}})
            anchor,anchor_weights = inverse_warp(bridges[side],lon,lat,
                center_yaw=90 if side == "east" else 270,horizontal_fov=60,
                vertical_fov=settings["bridgeVerticalFov"],model=settings["model"],
                feather_degrees=settings["featherDegrees"])
            anchor = anchor/255
            valid = anchor_weights >= 1-1e-6
            comparisons = []
            for label,pixels in (("north",a),("south",b)):
                delta = float(np.abs(pixels-anchor)[valid].mean()) if valid.any() else 1.0
                vertical = valid[1:]&valid[:-1]
                horizontal = valid[:,1:]&valid[:,:-1]
                pixel_edges = np.concatenate([np.diff(pixels,axis=0)[vertical].ravel(),np.diff(pixels,axis=1)[horizontal].ravel()])
                anchor_edges = np.concatenate([np.diff(anchor,axis=0)[vertical].ravel(),np.diff(anchor,axis=1)[horizontal].ravel()])
                strengths = [np.linalg.norm(value) for value in (pixel_edges,anchor_edges)]
                edge = float(np.dot(pixel_edges,anchor_edges)/max(float(strengths[0]*strengths[1]),1e-12)) if min(strengths) > .5 else (1.0 if max(strengths) <= .5 else 0.0)
                comparisons.append({"master":label,"pixelMAE":delta,"edgeAgreement":edge,
                    "comparedPixels":int(valid.sum()),"cameraModel":settings["model"],"passed":delta <= .10 and edge >= .65})
            zones[-1]["bridgeComparisons"] = comparisons
            zones[-1]["masterPairPixelMAE"] = zones[-1]["pixelMAE"]
            zones[-1]["pixelMAE"] = max([mae,*(value["pixelMAE"] for value in comparisons)])
            zones[-1]["edgeAgreement"] = min([agreement,*(value["edgeAgreement"] for value in comparisons)])
            zones[-1]["passed"] &= all(value["passed"] for value in comparisons)
            zones[-1]["geometryOrObjectConflictSuspected"] |= not all(value["passed"] for value in comparisons)
    return {"passed":all(zone["passed"] for zone in zones),
            "score":min(max(0,1-zone["pixelMAE"])*max(0,zone["edgeAgreement"]) for zone in zones),
            "overlaps":zones,"method":"aligned-overlap-pixel-and-edge-v3",
            "semanticGeometryVerified":False,"requiresHumanLayoutReview":True,
            "edgeMapping":{"east":"front right -> back left","west":"front left -> back right","mirroring":False,
                           "eastFractions":{"front":[.9,1],"back":[0,.1]},"westFractions":{"front":[0,.1],"back":[.9,1]}}}


def build_pair(engine, state, lid):
    signature = pair_signature(engine, state, lid)
    if not signature or not all(approved(engine, state, lid, name) for name in MASTERS):
        raise ValueError("Generate and approve both north/south panorama masters before overlap QA/projection.")
    folder = root(engine, lid) / "panorama"
    folder.mkdir(parents=True, exist_ok=True)
    pixels,sources = [],[]
    for name,side in zip(MASTERS,("front","back")):
        record = state.records[f"backgrounds:{lid}__{name}"]
        path = engine.local_path(record.asset.file_path)
        with Image.open(path) as image:
            pixels.append(np.asarray(image.convert("RGB")))
        published = folder/(side+".png")
        if path.resolve() != published.resolve():
            shutil.copyfile(path,published)
        sources.append({"name":side,"assetId":record.asset.asset_id,"file":record.asset.file_path,
                        "fileHash":record.file_hash,"coverage":COVERAGE[side]})
    from .location_bridges import BRIDGES,require_approved
    require_approved(engine,state,lid)
    bridge_pixels = {}
    anchors = {}
    for name in BRIDGES:
        record = state.records[f"backgrounds:{lid}__{name}"]
        with Image.open(engine.local_path(record.asset.file_path)) as image:
            bridge_pixels[name.split("_")[0]] = np.asarray(image.convert("RGB"))
        anchors[name] = {"assetId":record.asset.asset_id,"fileHash":record.file_hash}
    qa = assess(*pixels,bridges=bridge_pixels,bridge_warp=state.location_reference_sets[lid].get("bridge_warp"))
    qa["bridgeAnchors"] = anchors
    qa.update(signature=signature,model=MODEL,sources=sources,verticalCoverage=[-90,90],
              featherDegrees=20,repairRequired=not qa["passed"])
    layout = {**state.location_reference_sets[lid],"reference_type":"KEY_360","cameraCenter":"location_center",
              "cameraHeight":"fixed as approved in both masters","pitch":0,"fov":FOV,
              "styleRules":state.project_style_rules,"styleGuide":state.project_style_image,
              "panoramaSignature":signature,"status":"approved" if qa["passed"] else "requires_regeneration"}
    atomic_write(root(engine,lid)/"location_layout.json",json.dumps(layout,ensure_ascii=False,indent=2))
    if qa["passed"]:
        compose(*pixels).save(folder/"atlas.png")
        qa["atlasHash"] = file_hash(folder/"atlas.png")
    else:
        (folder/"atlas.png").unlink(missing_ok=True)
        for angle in range(0,360,45):
            for suffix in (".png",".json"):
                (root(engine,lid)/"yaw"/(f"yaw_{angle:03d}"+suffix)).unlink(missing_ok=True)
    atomic_write(folder/"seam_metadata.json",json.dumps(qa,ensure_ascii=False,indent=2))
    return qa


def compose(front,back):
    """Internal pixel cache for deterministic projections from the two master images."""
    width = min(4096,max(2048,round(max(front.shape[1],back.shape[1])*360/200/2)*2))
    height = width//2
    atlas = np.empty((height,width,3),np.uint8)
    lon = (-180+(np.arange(width)+.5)*360/width)[None,:]
    weight = np.clip((100-np.abs(lon))/20,0,1)[...,None]
    for start in range(0,height,64):
        end = min(height,start+64)
        lat = (90-(np.arange(start,end)+.5)*180/height)[:,None]
        a,b = _sample(front,lon,lat,"front"),_sample(back,lon,lat,"back")
        atlas[start:end] = np.rint(a*weight+b*(1-weight)).astype(np.uint8)
    return Image.fromarray(atlas)


def approve(engine, lid, name, expected_asset_id):
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    from .models import ApprovalRecord
    from .location_bridges import BRIDGES,require_approved
    if name not in (*BRIDGES,*MASTERS):
        raise ValueError("Choose an East/West bridge or North/South master.")
    with project_lock(engine.path), ledger_lock(engine.path):
        state = engine.load()
        record = state.records.get(f"backgrounds:{lid}__{name}")
        value = digest(engine, state, lid, name)
        if not value or record.asset.asset_id != expected_asset_id:
            raise ValueError("Generate/apply and review the current panorama before approval.")
        state.location_master_approvals[lid+":"+name] = ApprovalRecord(content_hash=value)
        from .directional_masters import alias
        published = root(engine,lid)/(name+".png" if name in BRIDGES else alias(name)+".png")
        shutil.copyfile(engine.local_path(record.asset.file_path),published)
        engine._save(state)
        return engine._refresh()[0]


def generate_view(engine, state, task, config):
    from .background_panorama import project_view
    from .models import GeneratedAsset
    lid, suffix = task.subject.rsplit("__", 1)
    if not pair_ready(engine, state, lid):
        raise ValueError("Both north/south masters need approval and passing overlap QA before projection.")
    yaw = int(suffix.removeprefix("view_"))
    folder = root(engine, lid)
    width, height = map(int, config.options["size"].split("x"))
    project_view(folder / "panorama" / "atlas.png", config.options["output_path"], yaw=yaw, size=(width, height), fov=FOV)
    dest = folder / "yaw" / f"yaw_{yaw:03d}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(config.options["output_path"], dest)
    info = {"yaw": yaw, "pitch": 0, "fov": FOV, "sourcePanorama": "front" if min(yaw,360-yaw)+FOV/2 <= 80 else "back" if min(yaw,360-yaw)-FOV/2 >= 100 else "front+back",
            "cameraCenter": "location_center", "cameraHeight": "fixed as approved in both masters",
            "canonicalAtlas": "panorama/atlas.png", "panoramaSignature": pair_signature(engine, state, lid),
            "atlasHash": file_hash(folder / "panorama" / "atlas.png"), "model": MODEL,
            "aiYawGeneration": False, "sourceFootprintDegrees": [yaw-FOV/2, yaw+FOV/2]}
    atomic_write(dest.with_suffix(".json"), json.dumps(info, ensure_ascii=False, indent=2))
    atomic_write(Path(config.options["output_path"]).with_suffix(".projection.json"), json.dumps(info, indent=2))
    return GeneratedAsset(type="image", provider="local", model=MODEL, file_path=config.options["output_path"],
                          prompt_hash=input_hash(task.prompt), generation_params={"projection": info})


def verify_protected(original, repaired, mask):
    """A repair can be committed only if every protected pixel is unchanged."""
    with Image.open(original) as a, Image.open(repaired) as b, Image.open(mask) as m:
        if a.size != b.size or a.size != m.size:
            raise ValueError("Repair and mask dimensions must match the original panorama.")
        protected = np.asarray(m.convert("L")) == 0
        if not protected.any():
            raise ValueError("A local repair must retain protected panorama pixels; full-image replacement is not a repair.")
        if not np.array_equal(np.asarray(a.convert("RGB"))[protected], np.asarray(b.convert("RGB"))[protected]):
            raise ValueError("Repair changed protected/common panorama pixels; reject this repair.")
    return True
