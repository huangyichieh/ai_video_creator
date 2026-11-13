"""Fixed world-space object fronts, independent of camera facing."""
import json
import math


def has_orientation(value):
    try:
        data = json.loads(value) if isinstance(value,str) else value
    except (TypeError,ValueError):
        return False
    return isinstance(data,dict) and ("frontYaw" in data or "front_yaw" in data)


def orientation(value):
    try:
        data = json.loads(value) if isinstance(value,str) else value
    except (TypeError,ValueError):
        return None,""
    if not isinstance(data,dict):
        return None,""
    yaw = data.get("frontYaw",data.get("front_yaw"))
    if yaw is None:
        return None,str(data.get("frontDefinition",data.get("front_definition","")))
    if isinstance(yaw,bool) or not isinstance(yaw,(int,float)) or not math.isfinite(yaw):
        raise ValueError("Object front direction must be a finite number of degrees, or left unspecified.")
    return yaw % 360,str(data.get("frontDefinition",data.get("front_definition","")))


def position_only(value):
    """Facing north is not evidence that the object is located on the north wall."""
    try:
        data = json.loads(value)
    except (TypeError,ValueError):
        return value
    if not isinstance(data,dict):
        return value
    return json.dumps({k:v for k,v in data.items() if k not in ("frontYaw","front_yaw","frontDefinition","front_definition")},ensure_ascii=False)


def with_orientation(value,yaw,definition=""):
    orientation({"frontYaw":yaw})
    try:
        data = json.loads(value)
    except (TypeError,ValueError):
        data = None
    if not isinstance(data,dict):
        if yaw is None:
            return value
        data = {"position":value}
    for key in ("frontYaw","front_yaw","frontDefinition","front_definition"):
        data.pop(key,None)
    if yaw is not None:
        data.update(frontYaw=yaw % 360,frontDefinition=definition.strip())
    return json.dumps(data,ensure_ascii=False)


def visible_face(item,camera):
    if item.front_yaw is None:
        return None
    x = sum(p.x for p in item.footprint)/len(item.footprint)
    y = sum(p.y for p in item.footprint)/len(item.footprint)
    dx,dy = camera["x"]-x,camera["y"]-y
    if math.hypot(dx,dy) < 1e-6:
        return {"frontYaw":item.front_yaw,"frontDefinition":item.front_definition,"visibleFace":"undetermined_camera_at_object_center"}
    toward_camera = math.degrees(math.atan2(dx,dy)) % 360
    relative = (toward_camera-item.front_yaw+180)%360-180
    faces = ("front","front-right three-quarter","right side","back-right three-quarter",
             "back","back-left three-quarter","left side","front-left three-quarter")
    face = faces[int((relative % 360+22.5)//45)%8]
    return {"frontYaw":item.front_yaw,"frontDefinition":item.front_definition,
        "objectToCameraYaw":round(toward_camera,3),"relativeViewingAngle":round(relative,3),"visibleFace":face}


def arrow(draw,center,yaw,*,length=14,color="#285ca8"):
    angle = math.radians(yaw)
    dx,dy = math.sin(angle),-math.cos(angle)
    a = (center[0]-length*dx/2,center[1]-length*dy/2)
    b = (center[0]+length*dx/2,center[1]+length*dy/2)
    draw.line([a,b],fill=color,width=3)
    base = (b[0]-6*dx,b[1]-6*dy)
    draw.polygon([b,(base[0]-4*dy,base[1]+4*dx),(base[0]+4*dy,base[1]-4*dx)],fill=color)
