"""Four camera-scoped location sectors and shared angular overlap anchors."""
import json
import math

import numpy as np
from PIL import Image

from .files import atomic_write,input_hash

NAMES = ("front_panorama","east_panorama","back_panorama","west_panorama")
YAWS = dict(zip(NAMES,(0,90,180,270)))
LABELS = dict(zip(NAMES,("North","East","South","West")))
HORIZONTAL_FOV = 110
VERSION = "four-directional-masters-110-v1"


def alias(name):
    return "master_"+LABELS[name].lower()


def distance(a,b):
    return abs((a-b+180)%360-180)


def select(yaw,fov=70):
    ranked = sorted(NAMES,key=lambda name:distance(YAWS[name],yaw))
    primary = ranked[0]
    # Include a neighbor only when the shot extends outside the primary sector.
    return [primary] if distance(YAWS[primary],yaw)+fov/2 <= HORIZONTAL_FOV/2 else ranked[:2]


def context(spec,name,notes="",visible_objects=None):
    from .floor_plan_guides import resolved_plan
    from .detail_object_guidance import view_layout
    from .location_view_context import scoped_text
    plan,kind = resolved_plan(spec)
    layout = view_layout(spec,{"position":plan.camera.model_dump(),"yaw":YAWS[name],
        "heightMeters":plan.camera_height_m},HORIZONTAL_FOV)
    # Masters remain level. Keep their established fingerprint unchanged when
    # adding adjustable pitch to detail-camera metadata.
    layout["camera"].pop("pitch",None)
    visible = {item["name"] for item in layout["visibleObjects"]}
    if visible_objects is not None:
        visible &= set(visible_objects)
    hidden = set(spec.get("object_layout",{}))-visible
    return {"scopeVersion":VERSION,"facing":LABELS[name].lower(),"cameraYaw":YAWS[name],
        "horizontalFov":HORIZONTAL_FOV,"camera":plan.camera.model_dump(),"cameraHeight":plan.camera_height_m,
        "geometrySource":kind,"floorPlan":"Use image camera guide and visible object footprints; never add off-camera objects.",
        "visibleObjects":{key:value for key,value in spec.get("object_layout",{}).items() if key in visible},
        "objectGeometry":{item.source_name:item.model_dump(mode="json") for item in plan.objects if item.source_name in visible},
        "viewObjectLayout":layout,"appearanceNotes":scoped_text(notes,name,excluded=hidden),
        "sideWallSegments":{},"sharedBoundaryObjects":[item for item in layout["visibleObjects"] if item["partiallyInFrame"]]}


def prompt(name):
    yaw = YAWS[name]
    return (f"Create ONE {HORIZONTAL_FOV}-degree equirectangular location sector facing {LABELS[name].upper()}, "
        f"center yaw {yaw}, coverage {yaw-55}..{yaw+55} degrees clockwise from north. "
        "Longitude maps linearly left-to-right; latitude +90..-90 top-to-bottom; horizon at half height. "
        "Same fixed camera center, height, physical scale, materials, illumination and rendering style in all four sectors. "
        "The camera guide defines framing and visible geometry. Show only objects inside this camera region. "
        "One stable object ID means one physical object in the same coordinates, never a new copy. "
        "Adjacent sectors overlap by 20 degrees. Shared desks, windows, shelves and wall surfaces must retain identical "
        "appearance, dimensions and placement; continue edge-cut objects naturally without caps, mirroring or duplication. "
        "Overlap reference crops show the SAME world-space angular region, not a new composition or style reference. "
        "Style references are STYLE ONLY. No people, reflections, labels, arrows or visible text.")


def orientation_contract(layout):
    lines = ["FINAL SPATIAL PRIORITY: saved object geometry/frontYaw and the camera guides override overlap crops, style images and generic office composition. "
        "Blue arrows next to labels indicate object fronts in world space (0=N, 90=E, 180=S, 270=W); they are not camera arrows. "
        "The red camera arrow/fan sets the viewpoint. Never rotate desks/chairs to face the camera or arrange them symmetrically for composition. "
        "Chair front means seated gaze direction; desk front means its registered seating/drawer side, not the long tabletop axis. "
        "These two fronts may legitimately point in opposite directions. Correct conflicting neighbor furniture instead of propagating its mistake."]
    for item in layout["visibleObjects"]:
        if front := item.get("orientation"):
            lines.append(f"{item['objectId']}: fixed frontYaw={front['frontYaw']:g}; expected camera-visible face={front['visibleFace']}.")
    lines.append("Preserve all registered positions, front directions and the empty/unoccupied environment. Render no arrows or labels.")
    return "\n".join(lines)


def overlap_range(a,b):
    delta = (YAWS[b]-YAWS[a]+180)%360-180
    low,high = max(-55,delta-55),min(55,delta+55)
    return (YAWS[a]+low,YAWS[a]+high) if low < high else None


def crop(image,name,region,size=(160,256)):
    """Sample a common angular region, including the north/west wrap boundary."""
    pixels = np.asarray(image.convert("RGB"),dtype=float)
    angles = np.linspace(region[0],region[1],size[0],endpoint=False)+(region[1]-region[0])/size[0]/2
    offsets = (angles-YAWS[name]+180)%360-180
    x = np.clip((offsets+55)/110*pixels.shape[1]-.5,0,pixels.shape[1]-1)
    y = np.linspace(0,pixels.shape[0]-1,size[1])
    x0,y0 = np.floor(x).astype(int),np.floor(y).astype(int)
    x1,y1 = np.minimum(x0+1,pixels.shape[1]-1),np.minimum(y0+1,pixels.shape[0]-1)
    wx,wy = (x-x0)[None,:,None],(y-y0)[:,None,None]
    return ((pixels[y0[:,None],x0]*(1-wx)+pixels[y0[:,None],x1]*wx)*(1-wy)
        +(pixels[y1[:,None],x0]*(1-wx)+pixels[y1[:,None],x1]*wx)*wy)


def anchors(engine,state,lid,name):
    """Earlier members only: sequential generation without cyclic dependencies."""
    result = []
    candidates = NAMES if getattr(engine,"_individual_location_master",None) == lid+"__"+name else NAMES[:NAMES.index(name)]
    for source in candidates:
        if source == name:
            continue
        region = overlap_range(name,source)
        record = state.records.get(f"backgrounds:{lid}__{source}")
        if region is None or not engine._intact(record):
            continue
        identity = input_hash([record.file_hash,name,region,VERSION])[:20]
        path = engine.path/"locations"/lid/"overlap_anchors"/(identity+".png")
        path.parent.mkdir(parents=True,exist_ok=True)
        with Image.open(engine.local_path(record.asset.file_path)) as image:
            pixels = crop(image,source,region,size=(384,768))
        Image.fromarray(np.rint(pixels).clip(0,255).astype("uint8")).save(path)
        result.append((path,{"source":source,"sourceAssetId":record.asset.asset_id,"sourceHash":record.file_hash,
            "yawRange":list(region),"target":name,"file":path.relative_to(engine.path).as_posix()}))
    return result


def assess(engine,state,lid):
    """Local image diagnostics; human review still decides semantic consistency."""
    report_path = engine.path/"locations"/lid/"overlap_qa.json"
    signature = input_hash([VERSION,*[state.records[f"backgrounds:{lid}__{name}"].file_hash for name in NAMES]])
    try:
        saved = json.loads(report_path.read_text(encoding="utf-8"))
        if saved.get("signature") == signature:
            return saved
    except (OSError,ValueError):
        pass
    rows = []
    for a,b in zip(NAMES,NAMES[1:]+NAMES[:1]):
        region = overlap_range(a,b)
        records = [state.records[f"backgrounds:{lid}__{name}"] for name in (a,b)]
        values = []
        for name,record in zip((a,b),records):
            with Image.open(engine.local_path(record.asset.file_path)) as image:
                values.append(crop(image,name,region)/255)
        mae = float(np.abs(values[0]-values[1]).mean())
        edges = [np.concatenate([np.diff(value,axis=0).ravel(),np.diff(value,axis=1).ravel()]) for value in values]
        norms = [float(np.linalg.norm(edge)) for edge in edges]
        agreement = float(np.dot(*edges)/max(math.prod(norms),1e-12)) if min(norms) > .5 else (1. if max(norms) <= .5 else 0.)
        rows.append({"neighbors":[a,b],"yawRange":list(region),"pixelMAE":mae,"edgeAgreement":agreement,
            "requiresVisualReview":mae > .10 or agreement < .65,
            "sourceHashes":[record.file_hash for record in records]})
    report = {"version":VERSION,"signature":signature,"overlaps":rows,"semanticGeometryVerified":False,
        "reviewRequired":True,"note":"Pixel/edge diagnostics cannot certify object identity or geometry. Review all four overlaps."}
    atomic_write(report_path,json.dumps(report,indent=2))
    return report


def review_ui(engine,state,lid):
    import streamlit as st
    report = assess(engine,state,lid)
    with st.expander("Overlap checks · four shared boundaries",expanded=False):
        st.caption("Local pixel/edge checks flag differences; visually confirm object identity, edge continuation and geometry before approval.")
        for row in report["overlaps"]:
            a,b = row["neighbors"]
            st.markdown(f"**{LABELS[a]} / {LABELS[b]} · 20° overlap**")
            st.caption(f"Pixel difference: {row['pixelMAE']:.3f} · Edge agreement: {row['edgeAgreement']:.2f} · "
                + ("Differences flagged — review required" if row["requiresVisualReview"] else "No large pixel/edge mismatch detected"))
            for column,name in zip(st.columns(2),(a,b)):
                with column:
                    record = state.records[f"backgrounds:{lid}__{name}"]
                    with Image.open(engine.local_path(record.asset.file_path)) as image:
                        pixels = crop(image,name,row["yawRange"])
                    st.image(np.rint(pixels).clip(0,255).astype("uint8"),caption=LABELS[name]+" shared region")
