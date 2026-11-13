"""Immutable bridge pixels prefill master edges; AI fills only remaining room content."""
import json

import numpy as np
from PIL import Image

from .files import atomic_write,input_hash
from .media_utils import file_hash

SIZES = {"bridges":(1536,1024),"masters":(2560,1440)}


def prepare(engine,state,lid,name):
    from .dual_panorama import root,COVERAGE,MASTERS
    from .bridge_warp import options,VERSION,master_angles,inverse_warp
    from .location_bridges import BRIDGES,require_approved
    if name not in MASTERS:
        raise ValueError("Choose the North or South master.")
    require_approved(engine,state,lid)
    source = {bridge:state.records[f"backgrounds:{lid}__{bridge}"] for bridge in BRIDGES}
    side = "front" if name == "front_panorama" else "back"
    settings = options(state.location_reference_sets[lid])
    metadata = {"version":VERSION,"warp":settings,"master":name,"size":list(SIZES["masters"]),
        "targetCoverage":list(COVERAGE[side]),
        "bridgeCoverage":{bridge:list(COVERAGE[bridge.split('_')[0]]) for bridge in BRIDGES},
        "sources":{key:{"assetId":value.asset.asset_id,"hash":value.file_hash} for key,value in source.items()}}
    digest = input_hash(metadata)
    folder = root(engine,lid)/"prefill"/digest[:16]
    folder.mkdir(parents=True,exist_ok=True)
    canvas_path,mask_path = folder/"canvas.png",folder/"mask.png"
    meta_path = folder/"metadata.json"
    if meta_path.exists():
        try:
            saved = json.loads(meta_path.read_text(encoding="utf-8"))
            if "overlaps" in saved and "unknownRegions" in saved and file_hash(canvas_path) == saved["canvasHash"] and file_hash(mask_path) == saved["maskHash"]:
                return saved
        except (OSError,ValueError,KeyError):
            pass
    width,height = SIZES["masters"]
    start,end = COVERAGE[side]
    metadata["masterCamera"] = {"centerYaw":(start+end)/2,"pitch":0,
        "horizontalFov":end-start,"verticalFov":settings["masterVerticalFov"],
        "model":"angular_equirectangular","pixelConvention":"centers at integer coordinates"}
    metadata["interpolation"] = "bilinear"
    lon,lat = master_angles((width,height),(start+end)/2,end-start,settings["masterVerticalFov"])
    color_sum = np.zeros((height,width,3),dtype=np.float32)
    weight_sum = np.zeros((height,width),dtype=np.float32)
    normalized = lon%360
    overlaps = []
    for bridge in BRIDGES:
        direction = bridge.split("_")[0]
        low,high = COVERAGE[direction]
        columns = ((normalized >= low) & (normalized <= high))[0]
        with Image.open(engine.local_path(source[bridge].asset.file_path)) as image:
            pixels = np.asarray(image.convert("RGB"))
        values,weights = inverse_warp(pixels,lon[:,columns],lat,center_yaw=(low+high)/2,
            horizontal_fov=high-low,vertical_fov=settings["bridgeVerticalFov"],
            model=settings["model"],feather_degrees=settings["featherDegrees"])
        color_sum[:,columns] += values*weights[...,None]
        weight_sum[:,columns] += weights
        selected = np.flatnonzero(columns)
        if len(selected):
            # Save angular and pixel-edge intersections, not just the sampled pixel centers.
            x0,x1 = int(selected[0]),int(selected[-1])+1
            yaw0,yaw1 = start+x0*(end-start)/width,start+x1*(end-start)/width
            source_yaw0 = yaw0%360
            source_yaw1 = source_yaw0+(yaw1-yaw0)
            overlaps.append({"bridge":bridge,"masterYawRange":[yaw0,yaw1],
                "bridgeYawRange":[source_yaw0,source_yaw1],"angularWidth":yaw1-yaw0,
                "targetPixelRect":[x0,0,x1,height],"sourceImageSize":[pixels.shape[1],pixels.shape[0]],
                "sourcePixelRect":([(source_yaw0-low)/(high-low)*pixels.shape[1],0,
                                   (source_yaw1-low)/(high-low)*pixels.shape[1],pixels.shape[0]]
                                   if settings["model"] == "angular_equirectangular" else None),
                "cameraModel":settings["model"],"centerYaw":(low+high)/2,
                "intrinsics":({"fx":pixels.shape[1]/(2*np.tan(np.deg2rad(high-low)/2)),
                    "fy":pixels.shape[1]/(2*np.tan(np.deg2rad(high-low)/2)),
                    "cx":(pixels.shape[1]-1)/2,"cy":(pixels.shape[0]-1)/2}
                    if settings["model"] == "perspective" else None),
                "horizontalFov":high-low,"verticalFov":(settings["bridgeVerticalFov"]
                    if settings["model"] == "angular_equirectangular" else
                    float(np.rad2deg(2*np.arctan(np.tan(np.deg2rad(high-low)/2)*pixels.shape[0]/pixels.shape[1]))))})
    known = weight_sum > 0
    canvas = np.rint(color_sum/np.maximum(weight_sum[...,None],1e-12)).clip(0,255).astype(np.uint8)
    # Keep sampled colors unattenuated in the input. Blend against the generated
    # master only on output, avoiding dark halos from fading known RGB into black.
    blend_alpha = np.where(known,np.maximum(1,np.rint(np.clip(weight_sum,0,1)*255)),0).astype(np.uint8)
    Image.fromarray(canvas).save(canvas_path)
    # Image-edit masks use transparent pixels for editable areas, opaque for protected anchors.
    mask = np.zeros((height,width,4),dtype=np.uint8)
    mask[:,:,:3] = 255
    mask[:,:,3] = blend_alpha
    Image.fromarray(mask).save(mask_path)
    unknown_regions = []
    previous = None
    for row in range(height):
        changes = np.flatnonzero(np.diff(np.pad((~known[row]).astype(np.int8),(1,1))))
        spans = tuple(zip(changes[::2],changes[1::2]))
        if spans == previous:
            for rectangle in unknown_regions[-len(spans):] if spans else []:
                rectangle[3] = row+1
        else:
            unknown_regions.extend([int(a),row,int(b),row+1] for a,b in spans)
        previous = spans
    metadata.update(canvas=canvas_path.relative_to(engine.path).as_posix(),canvasHash=file_hash(canvas_path),
                    mask=mask_path.relative_to(engine.path).as_posix(),maskHash=file_hash(mask_path),
                    knownPixelCount=int(known.sum()),unknownPixelCount=int((~known).sum()),
                    masterYawRange=[start,end],overlaps=overlaps,unknownRegions=unknown_regions,
                    protectedPixelCount=int((blend_alpha == 255).sum()),
                    featherPixelCount=int(((blend_alpha > 0)&(blend_alpha < 255)).sum()),
                    knownRegionRule="Opaque bridge cores are protected exactly; angular edge falloff blends into the generated master. Transparent space is unknown.")
    atomic_write(folder/"metadata.json",json.dumps(metadata,indent=2))
    return metadata


def finish(engine,state,task,asset):
    from .location_bridges import BRIDGES
    from .dual_panorama import verify_protected
    name = task.subject.rsplit("__",1)[1]
    target = SIZES["bridges" if name in BRIDGES else "masters"]
    with Image.open(asset.file_path) as image:
        actual = image.size
        pixels = image.convert("RGB")
        if pixels.size != target:
            pixels = pixels.resize(target,Image.Resampling.LANCZOS)
    asset.generation_params["outputResolution"] = {"requested":list(target),"providerReturned":list(actual),"resized":actual != target}
    if name not in BRIDGES and any(reference.endswith(tuple(BRIDGES)) for reference in task.refs):
        metadata = prepare(engine,state,task.subject.rsplit("__",1)[0],name)
        with Image.open(engine.local_path(metadata["canvas"])) as canvas, Image.open(engine.local_path(metadata["mask"])) as mask:
            alpha = mask.getchannel("A")
            pixels = Image.composite(canvas.convert("RGB"),pixels,alpha)
            pixels.save(asset.file_path)
            # verify_protected expects zero=protected, so invert the edit-mask alpha.
            protected_mask = engine.local_path(metadata["mask"]).with_name("verification_mask.png")
            Image.fromarray(255-np.asarray(alpha)).save(protected_mask)
        verify_protected(engine.local_path(metadata["canvas"]),asset.file_path,protected_mask)
        metadata["protectedPixelsVerified"] = True
        asset.generation_params["masterPrefill"] = metadata
    else:
        pixels.save(asset.file_path)
