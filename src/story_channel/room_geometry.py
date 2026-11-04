"""A deterministic extruded-polygon room and CPU panoramic ray renderer."""
import numpy as np
from PIL import Image

from .floor_plan_guides import resolved_plan,_kind

MODEL = "shared-room-raytrace-v1"


def scene_from_spec(spec,*,camera=None,allow_schematic=False,allow_incomplete=False):
    plan,kind = resolved_plan(spec)
    if kind != "metric" and not allow_schematic:
        raise ValueError("Generate/check and save a spatial floor plan before building the 3D room.")
    if not allow_incomplete and {item.source_name for item in plan.objects} != set(spec.get("object_layout",{})):
        raise ValueError("Check reference: the spatial plan must contain every object exactly once.")
    origin = camera if camera is not None else [plan.camera.x,plan.camera.y,plan.camera_height_m]
    if not 0 < origin[2] < plan.room_height_m:
        raise ValueError("Camera height must be below the ceiling.")
    identities = [item.object_id or item.source_name for item in plan.objects]
    if len(identities) != len(set(identities)):
        raise ValueError("Each physical 3D object needs a unique stable ID.")
    meshes = []
    def surface(identity,vertices,color):
        meshes.append({"objectId":identity,"vertices":vertices,"color":color})
    def walls(identity,points,bottom,top,color):
        for a,b in zip(points,points[1:]+points[:1]):
            surface(identity,[[a.x,a.y,bottom],[b.x,b.y,bottom],[b.x,b.y,top],[a.x,a.y,top]],color)
    boundary = plan.boundary
    surface("ROOM_FLOOR",[[p.x,p.y,0] for p in boundary],plan.floor_color)
    surface("ROOM_CEILING",[[p.x,p.y,plan.room_height_m] for p in boundary],plan.ceiling_color)
    walls("ROOM_WALL",boundary,0,plan.room_height_m,plan.wall_color)
    objects = []
    for item in plan.objects:
        if not _inside(np.array([[p.x,p.y] for p in item.footprint]),np.array([[p.x,p.y] for p in plan.boundary])).any():
            raise ValueError(f"{item.source_name}: footprint lies outside the room boundary.")
        category = _kind(item.source_name)
        inferred = item.height_m is None
        height = item.height_m or {"DESK":.75,"SHELF":2,"CABINET":1.8,"DOOR":2.1,"WINDOW":1.2,"PRINTER":.45}.get(category,1)
        bottom = item.elevation_m
        if bottom+height > plan.room_height_m:
            raise ValueError(f"{item.source_name}: object height exceeds the room ceiling.")
        if bottom < origin[2] < bottom+height and _inside(np.array([origin[:2]]),
                np.array([[p.x,p.y] for p in item.footprint]))[0]:
            raise ValueError(f"Move the camera outside {item.source_name}'s solid footprint.")
        # A real extruded footprint, not independently interpreted image views.
        walls(item.object_id or item.source_name,item.footprint,bottom,bottom+height,item.material_color)
        surface(item.object_id or item.source_name,[[p.x,p.y,bottom+height] for p in item.footprint],item.material_color)
        surface(item.object_id or item.source_name,[[p.x,p.y,bottom] for p in item.footprint],item.material_color)
        objects.append({**item.model_dump(mode="json"),"effectiveHeightMeters":height,"heightInferred":inferred})
    return {"model":MODEL,"axes":"x=east, y=north, z=up; yaw clockwise from north",
            "camera":list(origin),"objects":objects,"surfaces":meshes,
            "lighting":{"ambient":.4,"direction":[-.4,-.5,1],"diffuse":.6},
            "representation":"3D blockout: extruded footprints and solid materials; no people or generated text",
            "styleRules":spec.get("style_rules","")}


def _inside(points,vertices):
    """Even/odd polygon containment in a plane, including concave footprints."""
    inside = np.zeros(len(points),dtype=bool)
    x,y = points[:,0],points[:,1]
    for a,b in zip(vertices,np.roll(vertices,-1,axis=0)):
        if abs(b[1]-a[1]) < 1e-10:
            continue
        inside ^= ((a[1]>y)!=(b[1]>y)) & (x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0])
    return inside


def render_panorama(scene,destination,*,width=1536,stop_requested=lambda:False,progress=None):
    """Trace one set of world-space rays, with no generated-image stitching."""
    if not 64 <= width <= 4096 or width%2:
        raise ValueError("Panorama width must be an even number from 64 to 4096.")
    height = width//2
    origin = np.asarray(scene["camera"],dtype=np.float64)
    light = np.asarray(scene["lighting"]["direction"],dtype=float)
    light /= np.linalg.norm(light)
    prepared = []
    for surface in scene["surfaces"]:
        vertices = np.asarray(surface["vertices"],dtype=float)
        normal = np.zeros(3)
        for vertex in vertices[2:]:
            normal = np.cross(vertices[1]-vertices[0],vertex-vertices[0])
            if np.linalg.norm(normal)>1e-10:
                break
        length = np.linalg.norm(normal)
        if length < 1e-10:
            continue
        normal /= length
        axis = int(np.argmax(abs(normal)))
        color = np.array([int(surface["color"][i:i+2],16) for i in (1,3,5)],dtype=float)
        # Two-sided diffuse material; deterministic light and room geometry.
        shade = scene["lighting"]["ambient"]+scene["lighting"]["diffuse"]*abs(float(np.dot(normal,light)))
        prepared.append((vertices,normal,axis,color*shade))
    output = np.zeros((height,width,3),dtype=np.uint8)
    longitude = np.deg2rad(-180+(np.arange(width)+.5)*360/width)
    for start in range(0,height,16):
        if stop_requested():
            raise InterruptedError("Stopped local room rendering; previous panorama revisions were retained.")
        end = min(height,start+16)
        latitude = np.deg2rad(90-(np.arange(start,end)+.5)*180/height)[:,None]
        directions = np.stack(np.broadcast_arrays(np.cos(latitude)*np.sin(longitude),
                                np.cos(latitude)*np.cos(longitude),np.sin(latitude)),axis=-1).reshape(-1,3)
        nearest = np.full(len(directions),np.inf)
        colors = np.zeros((len(directions),3))
        for vertices,normal,axis,color in prepared:
            denominator = directions@normal
            distance = np.divide(np.dot(vertices[0]-origin,normal),denominator,
                                 out=np.full(len(directions),np.inf),where=abs(denominator)>1e-9)
            candidate = np.flatnonzero((distance>1e-5)&(distance<nearest))
            if not len(candidate):
                continue
            hits = origin+directions[candidate]*distance[candidate,None]
            contained = _inside(np.delete(hits,axis,axis=1),np.delete(vertices,axis,axis=1))
            accepted = candidate[contained]
            nearest[accepted] = distance[accepted]
            colors[accepted] = color
        output[start:end] = np.clip(np.rint(colors.reshape(end-start,width,3)),0,255).astype(np.uint8)
        if progress:
            progress(end/height)
    from pathlib import Path
    destination = Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    Image.fromarray(output).save(destination)
    return output
