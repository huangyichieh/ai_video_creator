"""Local pinhole blockout: camera pose becomes pixels before the AI request."""
import math
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from .detail_camera import perspective_camera,validate
from .room_geometry import scene_from_spec,_inside

VERSION = "detail-pose-raytrace-v1"


def render(spec,setup,fov,destination,*,size=(768,432)):
    setup = validate(spec,setup)
    camera = perspective_camera(spec,setup,fov,size)
    origin = np.array([setup["position"]["x"],setup["position"]["y"],setup["heightMeters"]],dtype=float)
    scene = scene_from_spec(spec,camera=origin.tolist(),allow_schematic=True,allow_incomplete=True)
    width,height = size
    focal = camera["focalLengthPixels"][0]
    right,up,forward = (np.array(camera[key]) for key in ("rightWorld","upWorld","forwardWorld"))
    prepared = []
    light = np.array(scene["lighting"]["direction"],dtype=float)
    light /= np.linalg.norm(light)
    for surface in scene["surfaces"]:
        vertices = np.array(surface["vertices"],dtype=float)
        normal = np.zeros(3)
        for vertex in vertices[2:]:
            normal = np.cross(vertices[1]-vertices[0],vertex-vertices[0])
            if np.linalg.norm(normal)>1e-10:
                break
        if np.linalg.norm(normal)<1e-10:
            continue
        normal /= np.linalg.norm(normal)
        color = np.array([int(surface["color"][i:i+2],16) for i in (1,3,5)],dtype=float)
        shade = .4+.6*abs(float(normal@light))
        prepared.append((vertices,normal,int(np.argmax(abs(normal))),color*shade,surface["objectId"]))
    output = np.zeros((height,width,3),dtype=np.uint8)
    counts = {}
    screen_x = (np.arange(width)-(width-1)/2)/focal
    for start in range(0,height,16):
        end = min(height,start+16)
        screen_y = ((height-1)/2-np.arange(start,end))/focal
        rays = (forward + screen_x[None,:,None]*right + screen_y[:,None,None]*up).reshape(-1,3)
        nearest = np.full(len(rays),np.inf)
        colors = np.zeros((len(rays),3))
        hits_ids = np.full(len(rays),-1,dtype=int)
        for index,(vertices,normal,axis,color,identity) in enumerate(prepared):
            denominator = rays@normal
            distance = np.divide((vertices[0]-origin)@normal,denominator,
                out=np.full(len(rays),np.inf),where=abs(denominator)>1e-9)
            candidate = np.flatnonzero((distance>1e-5)&(distance<nearest))
            if not len(candidate):
                continue
            hits = origin+rays[candidate]*distance[candidate,None]
            accepted = candidate[_inside(np.delete(hits,axis,axis=1),np.delete(vertices,axis,axis=1))]
            nearest[accepted],colors[accepted],hits_ids[accepted] = distance[accepted],color,index
        for index,count in zip(*np.unique(hits_ids[hits_ids>=0],return_counts=True)):
            identity = prepared[index][4]
            counts[identity] = counts.get(identity,0)+int(count)
        output[start:end] = np.clip(np.rint(colors.reshape(end-start,width,3)),0,255).astype(np.uint8)
    from .media_utils import write_bytes
    image = BytesIO()
    Image.fromarray(output).save(image,format="PNG")
    write_bytes(Path(destination),image.getvalue())
    # Positive pitch looks up, so the true world horizon moves down the frame.
    horizon = .5+focal/height*math.tan(math.radians(camera["pitchDegrees"]))
    return {"version":VERSION,"file":str(destination),"camera":camera,"horizonYNormalized":horizon,
        "surfacePixelCounts":counts,"geometrySource":setup["geometrySource"],
        "objectVisibility":{obj["source_name"]:counts.get(obj["object_id"] or obj["source_name"],0)>0 for obj in scene["objects"]},
        "heightInferred":[obj["source_name"] for obj in scene["objects"] if obj["heightInferred"]],
        "purpose":"camera composition only; solid proxies are not appearance references"}


def instructions(metadata):
    camera = metadata["camera"]
    pitch = camera["pitchDegrees"]
    horizon = metadata["horizonYNormalized"]
    location = f"{horizon*100:.1f}% down from the top" if 0<=horizon<=1 else "below the frame" if horizon>1 else "above the frame"
    cues = ("Look upward: show more upper wall/ceiling and less floor; vertical lines converge upward."
        if pitch>0 else "Look downward: show more floor and top surfaces, less ceiling; vertical lines converge downward."
        if pitch<0 else "Level optical axis: keep verticals upright and the world horizon centered.")
    return ("PRIMARY CAMERA COMPOSITION: Image 1 is a locally ray-traced rectilinear perspective blockout from the EXACT selected camera. "
        "Match its viewpoint, height, tilt, perspective, crop and floor/wall/ceiling proportions. "
        "Replace simple solid proxies with the corresponding approved objects and materials; do not copy the blockout's flat colors or box-like object shapes. "
        f"Camera height {camera['position']['heightMeters']:g} m; pitch {pitch:g} degrees; "
        f"horizontal FOV {camera['horizontalFovDegrees']:g} degrees. World horizon is {location}. {cues} "
        "Do not level the camera, recenter the horizon, straighten perspective or reuse an eye-level master framing. "
        "A low camera must see objects from below; a high camera must reveal their upper surfaces according to the selected tilt. "
        "The approved master images supply appearance only. Spatial plans may be schematic and proxy heights inferred; preserve known object identities and dimensions.")
