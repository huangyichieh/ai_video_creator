"""Deterministic horizontal camera composition from saved object footprints."""
import math

from .detail_camera import validate
from .floor_plan_guides import resolved_plan


def _clip(points,distance):
    """Clip a footprint against one camera-frustum half-plane."""
    output = []
    for a,b in zip(points,points[1:]+points[:1]):
        da,db = distance(a),distance(b)
        if da >= 0:
            output.append(a)
        if (da >= 0) != (db >= 0):
            t = da/(da-db)
            output.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
    return output


def view_layout(spec,setup,fov=70,object_ids=None):
    if not math.isfinite(fov) or not 20 <= fov <= 110:
        raise ValueError("Use a field of view between 20 and 110 degrees.")
    camera = validate(spec,setup)
    plan,kind = resolved_plan(spec)
    ids = object_ids or {}
    angle = math.radians(camera["yaw"])
    sine,cosine = math.sin(angle),math.cos(angle)
    tangent = math.tan(math.radians(fov/2))
    def local(point):
        dx,dy = point.x-camera["position"]["x"],point.y-camera["position"]["y"]
        return dx*cosine-dy*sine,dx*sine+dy*cosine
    room_depth = max(1e-6,max(local(point)[1] for point in plan.boundary))
    visible,excluded = [],[]
    for item in plan.objects:
        identity = ids.get(item.source_name) or item.object_id or item.source_name
        from .object_orientation import visible_face
        front = visible_face(item,camera["position"])
        base = {"objectId":identity,"name":item.source_name,**({"orientation":front} if front else {})}
        original = [local(point) for point in item.footprint]
        clipped = original
        for distance in (lambda p:p[1]-1e-6,lambda p:p[0]+p[1]*tangent,lambda p:p[1]*tangent-p[0]):
            clipped = _clip(clipped,distance) if clipped else []
        area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(clipped,clipped[1:]+clipped[:1])))/2 if clipped else 0
        if area < 1e-9:
            excluded.append({**base,"reason":"behind_camera" if max(p[1] for p in original) <= 0 else "outside_horizontal_fov"})
            continue
        projected = [max(-1.,min(1.,x/(z*tangent))) for x,z in clipped]
        low,high = min(projected),max(projected)
        regions = [name for name,start,end in (("left",-1,-1/3),("center",-1/3,1/3),("right",1/3,1)) if high >= start and low <= end]
        near,far = min(p[1] for p in clipped),max(p[1] for p in clipped)
        depth = (near+far)/2/room_depth
        band = "foreground" if depth < 1/3 else "middle_ground" if depth < 2/3 else "background"
        partial = any(z <= 0 or abs(x) > z*tangent+1e-8 for x,z in original)
        visible.append({**base,"screenRegions":regions,"horizontalRange":[round(low,4),round(high,4)],
            "depthBand":band,"forwardDepth":round((near+far)/2,4),"depthRange":[round(near,4),round(far,4)],
            "partiallyInFrame":partial,"footprint":[point.model_dump() for point in item.footprint]})
    placed = {item.source_name for item in plan.objects}
    unresolved = [{"objectId":ids.get(name,name),"name":name,"reason":"no_spatial_footprint"}
                  for name in spec.get("object_layout",{}) if name not in placed]
    return {"camera":camera,"horizontalFov":fov,"geometrySource":kind,
        "units":"metres" if kind == "metric" else "schematic_not_to_scale",
        "method":"horizontal_footprint_frustum_clipping","occlusion":"not_computed","verticalVisibility":"not_computed",
        "depthBands":"thirds of the room's forward extent from this camera",
        "visibleObjects":sorted(visible,key=lambda row:(row["forwardDepth"],row["objectId"])),
        "excludedObjects":excluded,"unresolvedObjects":unresolved}


def camera_orientation(yaw):
    """Concrete wall orientation, independent of either master's framing."""
    facing = (
        "north wall ahead; west side to screen-left and east side to screen-right",
        "northeast corner ahead; north wall to screen-left and east wall to screen-right",
        "east wall ahead; north side to screen-left and south side to screen-right",
        "southeast corner ahead; east wall to screen-left and south wall to screen-right",
        "south wall ahead; east side to screen-left and west side to screen-right",
        "southwest corner ahead; south wall to screen-left and west wall to screen-right",
        "west wall ahead; south side to screen-left and north side to screen-right",
        "northwest corner ahead; west wall to screen-left and north wall to screen-right",
    )[int((yaw % 360 + 22.5)//45) % 8]
    return (f"CAMERA ORIENTATION: exact yaw {yaw % 360:g} degrees clockwise from north. "
        f"Orientation cue: {facing}. These are world directions, not labels to draw. "
        "Show side walls only where they enter the requested FOV; do not widen the lens to include them. "
        "An east/west-facing view must face the side of the room, not reuse a north/south master's composition.")


def instructions(layout):
    lines = ["CAMERA-DERIVED OBJECT PLACEMENT (preserve each object's physical location and count):"]
    for item in layout["visibleObjects"]:
        if layout.get("poseVisibility",{}).get(item["name"]) is False:
            lines.append(f"{item['objectId']} ({item['name']}) is horizontally nearby but outside/occluded in the selected height/tilt blockout. Do not force it into frame or change the camera to show it.")
            continue
        line = f"Show {item['objectId']} ({item['name']}) in the {'/'.join(item['screenRegions'])} {item['depthBand'].replace('_',' ')}. Render only its camera-visible parts at the selected height/tilt."
        if item["partiallyInFrame"]:
            line += " Only part of its footprint is in frame; crop naturally at the edge, never move it fully into view."
        if front := item.get("orientation"):
            line += (f" Its fixed front faces yaw {front['frontYaw']:g} degrees clockwise from north; "
                f"front means: {front['frontDefinition'] or 'the physical front face'}. "
                f"From this camera position show its {front['visibleFace']}. Never rotate the object to face the camera.")
        lines.append(line)
    for item in layout["excludedObjects"]:
        lines.append(f"Do not show {item['objectId']} ({item['name']}): {item['reason'].replace('_',' ')}.")
    if layout["unresolvedObjects"]:
        lines.append("Objects without spatial coordinates have no calculated screen placement; use the approved masters, not invented coordinates.")
    if layout["geometrySource"] != "metric":
        lines.append("These are approximate schematic positions, not measured distances. Preserve the approved masters' physical layout.")
    lines.append("The fan radius illustrates angle only, not maximum viewing distance; include listed objects beyond the drawn arc. "
        "The horizontal fan identifies potential visibility, not guaranteed line of sight. Respect occlusion by nearer walls/furniture; never show objects through walls. Do not copy floor-plan labels, colors or markings into the scene.")
    lines.append("Object fronts are fixed in world space. Rotating a camera in place changes framing, not which physical side of a shared object is seen; moving the camera can change the visible face. Blue label arrows are planning symbols, never part of the image.")
    return "\n".join(lines)
