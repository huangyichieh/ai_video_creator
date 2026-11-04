"""Shared floor-plan coordinates for the camera picker and generation reference."""
import math

from PIL import Image,ImageDraw,ImageFont


def geometry(spec):
    from .floor_plan_guides import resolved_plan
    plan,kind = resolved_plan(spec)
    xs,ys = [p.x for p in plan.boundary],[p.y for p in plan.boundary]
    scale = min(700/(max(xs)-min(xs)),500/(max(ys)-min(ys)))
    return plan,kind,{"minX":min(xs),"minY":min(ys),"scale":scale,"width":800,"height":600,"pad":50}


def inside(position,polygon):
    x,y = position["x"],position["y"]
    result = False
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if (a.y > y) != (b.y > y) and x < (b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x:
            result = not result
    return result


def validate(spec,setup):
    plan,kind,_ = geometry(spec)
    try:
        position = {axis:float(setup["position"][axis]) for axis in ("x","y")}
        yaw = float(setup["yaw"])
        height = float(setup.get("heightMeters",plan.camera_height_m))
        pitch = float(setup.get("pitch",0))
    except (TypeError,KeyError,ValueError):
        raise ValueError("Place and aim the camera on the floor plan first.") from None
    if not all(math.isfinite(value) for value in (*position.values(),yaw)) or not inside(position,plan.boundary):
        raise ValueError("Place the camera inside the room boundary.")
    if not math.isfinite(height) or not 0 < height < plan.room_height_m:
        raise ValueError("Keep the camera height above the floor and below the ceiling.")
    if not math.isfinite(pitch) or not -60 <= pitch <= 60:
        raise ValueError("Keep camera tilt between -60 and 60 degrees.")
    return {"position":position,"yaw":yaw%360,"geometrySource":kind,"heightMeters":height,"pitch":pitch}


def perspective_camera(spec,setup,fov,size=(1536,864)):
    """Explicit pinhole camera intent for generation and saved provenance."""
    pose = validate(spec,setup)
    if not math.isfinite(fov) or not 20 <= fov <= 110:
        raise ValueError("Use a field of view between 20 and 110 degrees.")
    width,height = size
    yaw,pitch = math.radians(pose["yaw"]),math.radians(pose["pitch"])
    focal = width/(2*math.tan(math.radians(fov)/2))
    return {"projection":"rectilinear_perspective","position":{**pose["position"],"heightMeters":pose["heightMeters"]},
        "horizontalUnits":"metres" if pose["geometrySource"] == "metric" else "schematic_not_to_scale",
        "axes":"x=east, y=north, z=up; yaw clockwise from north; positive pitch looks up",
        "yawDegrees":pose["yaw"],"pitchDegrees":pose["pitch"],"rollDegrees":0,
        "horizontalFovDegrees":fov,"verticalFovDegrees":math.degrees(2*math.atan(height/width*math.tan(math.radians(fov)/2))),
        "referenceImageSize":[width,height],"focalLengthPixels":[focal,focal],"principalPointPixels":[(width-1)/2,(height-1)/2],
        "forwardWorld":[math.sin(yaw)*math.cos(pitch),math.cos(yaw)*math.cos(pitch),math.sin(pitch)],
        "rightWorld":[math.cos(yaw),-math.sin(yaw),0],
        "upWorld":[-math.sin(yaw)*math.sin(pitch),-math.cos(yaw)*math.sin(pitch),math.cos(pitch)]}


def guide(spec,setup,path,fov=70,*,object_view=None):
    """Persist the exact selected camera with room/object coordinates for the AI."""
    plan,kind,transform = geometry(spec)
    def xy(point):
        return (50+(point.x-transform["minX"])*transform["scale"],550-(point.y-transform["minY"])*transform["scale"])
    rows = [*object_view["visibleObjects"],*object_view["excludedObjects"]] if object_view else []
    image = Image.new("RGB",(1200,max(900,100+len(rows)*42)) if object_view else (800,900),"#f5f6f8")
    draw = ImageDraw.Draw(image)
    draw.polygon([xy(point) for point in plan.boundary],fill="white",outline="#243447",width=3)
    font = ImageFont.load_default(size=12)
    visible = {item["name"]:item for item in object_view["visibleObjects"]} if object_view else {}
    excluded = {item["name"]:item for item in object_view["excludedObjects"]} if object_view else {}
    for item in plan.objects:
        points = [xy(point) for point in item.footprint]
        draw.polygon(points,fill="#e0e2e5" if item.source_name in excluded else item.material_color,
            outline="#14804a" if item.source_name in visible else "#7c8796",width=2)
        center = (sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points))
        identity = (visible.get(item.source_name) or excluded.get(item.source_name) or {}).get("objectId") or item.object_id or item.source_name
        draw.text(center,identity,font=font,fill="#152536",anchor="mm",stroke_width=1,stroke_fill="white")
    from .floor_plan_guides import PlanPoint
    x,y = xy(PlanPoint(**setup["position"]))
    overlay = Image.new("RGBA",image.size,(0,0,0,0))
    sector = ImageDraw.Draw(overlay)
    sector.pieslice((x-120,y-120,x+120,y+120),start=setup["yaw"]-90-fov/2,
        end=setup["yaw"]-90+fov/2,fill=(240,43,60,40),outline=(240,43,60,255),width=2)
    image = Image.alpha_composite(image.convert("RGBA"),overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.ellipse((x-6,y-6,x+6,y+6),fill="#f02b3c",outline="white",width=2)
    from .object_orientation import arrow
    for item in plan.objects:
        if item.front_yaw is None:
            continue
        points = [xy(point) for point in item.footprint]
        cx,cy = sum(p[0] for p in points)/len(points),sum(p[1] for p in points)/len(points)
        identity = (visible.get(item.source_name) or excluded.get(item.source_name) or {}).get("objectId") or item.object_id or item.source_name
        arrow(draw,(min(783,cx+draw.textlength(identity,font=font)/2+12),cy),item.front_yaw)
    draw.text((12,12),f"NORTH ↑ | red dot = camera; red fan = field of view ({fov:g} degrees)",font=font,fill="#243447")
    draw.text((12,580),"Metric layout" if kind == "metric" else "Schematic layout — not to scale",font=font,fill="#243447")
    pose = validate(spec,setup)
    draw.rectangle((12,615,785,892),fill="white",outline="#aab6c4",width=2)
    draw.text((24,627),f"SIDE VIEW: camera height {pose['heightMeters']:g} m | tilt {pose['pitch']:g} deg (positive = up)",font=font,fill="#243447")
    floor_y,top_y,camera_x = 868,660,140
    camera_y = floor_y-pose["heightMeters"]/plan.room_height_m*(floor_y-top_y)
    draw.line([(45,floor_y),(760,floor_y)],fill="#7c8796",width=2)
    draw.line([(45,top_y),(760,top_y)],fill="#7c8796",width=2)
    draw.line([(camera_x,floor_y),(camera_x,camera_y)],fill="#285ca8",width=3)
    draw.rectangle((camera_x-8,camera_y-6,camera_x+8,camera_y+6),fill="#243447")
    radians = math.radians(pose["pitch"])
    end = (camera_x+150*math.cos(radians),camera_y-150*math.sin(radians))
    draw.line([(camera_x,camera_y),end],fill="#d97706",width=4)
    draw.text((350,740),"Pose diagram only. Render from this height/tilt; do not render guide lines.",font=font,fill="#243447")
    if object_view:
        draw.text((805,20),"CALCULATED CAMERA COMPOSITION",font=font,fill="#243447")
        draw.text((805,36),"Fan radius is illustrative, not viewing distance",font=font,fill="#243447")
        for index,item in enumerate(rows):
            y = 55+index*42
            included = "screenRegions" in item
            draw.text((805,y),item["objectId"],font=font,fill="#14804a" if included else "#7c8796")
            description = ("/".join(item["screenRegions"])+" | "+item["depthBand"].replace("_"," ")+
                (" | partial" if item["partiallyInFrame"] else "")) if included else "OUTSIDE VIEW — do not show"
            draw.text((805,y+16),description,font=font,fill="#243447")
    image.save(path)
    return path
