"""Shared spatial data and deterministic, view-specific camera guide drawings."""
import json
import math
import re
import colorsys

from pydantic import Field,model_validator,model_serializer
from PIL import Image,ImageDraw,ImageFont,ImageChops,ImageFilter

from .models import Model
from .files import input_hash,atomic_write
from .file_cache import read_json


class PlanPoint(Model):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class PlanObject(Model):
    source_name: str = Field(min_length=1)
    object_id: str = ""
    footprint: list[PlanPoint] = Field(min_length=3)
    height_m: float | None = Field(default=None,gt=0,le=100,allow_inf_nan=False)
    elevation_m: float = Field(default=0,ge=0,le=100,allow_inf_nan=False)
    material_color: str = Field(default="#b7b2a8",pattern=r"^#[0-9a-fA-F]{6}$")
    front_yaw: float | None = Field(default=None,ge=0,lt=360,allow_inf_nan=False,
        description="Fixed object front direction clockwise from north: 0 north, 90 east, 180 south, 270 west. Null if unknown or not directional.")
    front_definition: str = Field(default="",description="Which physical side is front, e.g. seated person's gaze for a chair, seating/drawer side for a desk, door side for a cabinet.")

    @model_serializer(mode="wrap")
    def serialize(self,handler):
        data = handler(self)
        if self.front_yaw is None and not self.front_definition:
            for key in ("frontYaw","front_yaw","frontDefinition","front_definition"):
                data.pop(key,None)
        return data


class CameraPlan(Model):
    boundary: list[PlanPoint] = Field(min_length=3)
    camera: PlanPoint
    camera_height_m: float = Field(default=1.6,gt=0,allow_inf_nan=False)
    objects: list[PlanObject] = Field(default_factory=list)
    room_height_m: float = Field(default=3,gt=0,le=100,allow_inf_nan=False)
    floor_color: str = Field(default="#b9ad96",pattern=r"^#[0-9a-fA-F]{6}$")
    wall_color: str = Field(default="#e6e1d6",pattern=r"^#[0-9a-fA-F]{6}$")
    ceiling_color: str = Field(default="#f0eee8",pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def valid_geometry(self):
        def area(points):
            return abs(sum(a.x*b.y-b.x*a.y for a,b in zip(points,points[1:]+points[:1])))/2
        if area(self.boundary) < .0001 or any(area(item.footprint) < .0001 for item in self.objects):
            raise ValueError("Room boundary and object footprints must have nonzero area.")
        inside = False
        for a,b in zip(self.boundary,self.boundary[1:]+self.boundary[:1]):
            if (a.y > self.camera.y) != (b.y > self.camera.y):
                if self.camera.x < (b.x-a.x)*(self.camera.y-a.y)/(b.y-a.y)+a.x:
                    inside = not inside
        if not inside:
            raise ValueError("Camera center must be inside the room boundary.")
        names = [item.source_name for item in self.objects]
        if len(names) != len(set(names)):
            raise ValueError("Each physical object must occur once in the camera plan.")
        return self


def source_signature(floor,objects):
    return input_hash({"floorPlan":floor,"objectLayout":objects})


def _kind(name):
    for pattern,kind in ((r"printer|印表|打印","PRINTER"),(r"window|窗","WINDOW"),(r"door|門|门","DOOR"),
                         (r"desk|table|桌|檯","DESK"),(r"shelf|bookcase|架","SHELF"),(r"cabinet|櫃|柜","CABINET")):
        if re.search(pattern,name,re.I):
            return kind
    return "OBJECT"


def stable_ids(engine,lid,objects,plan=None):
    from .dual_panorama import root
    from .locking import project_lock
    file = root(engine,lid)/"object_ids.json"
    file.parent.mkdir(parents=True,exist_ok=True)
    with project_lock(file.parent):
        registry = read_json(file) if file.exists() else {}
        proposed = {item.source_name:item.object_id for item in plan.objects} if plan else {}
        result = {}
        for name,position in objects.items():
            supplied = proposed.get(name,"")
            try:
                data = json.loads(position)
                if isinstance(data,dict):
                    supplied = data.get("objectId",supplied)
            except (ValueError,TypeError):
                pass
            identity = next((value for key,value in registry.items() if key.casefold() == name.casefold()),None) or supplied or (name if re.fullmatch(r"[A-Z][A-Z0-9_]*_\d+",name) else "")
            if identity and not re.fullmatch(r"[A-Z][A-Z0-9_]*_\d+",identity):
                identity = ""
            if not identity:
                prefix = _kind(name)+"_"
                number = 1
                while prefix+f"{number:02d}" in set(registry.values())|set(result.values()):
                    number += 1
                identity = prefix+f"{number:02d}"
            if identity in result.values():
                raise ValueError("Two objects have the same stable ID; merge the duplicate or assign separate physical-object IDs.")
            registry[name] = identity
            result[name] = identity
        atomic_write(file,json.dumps(registry,ensure_ascii=False,indent=2))
    return result


def _schematic(spec):
    """Legacy text-only layouts remain usable as explicitly non-metric drafts."""
    from .location_view_context import object_bearings
    items = []
    for index,(name,position) in enumerate(spec.get("object_layout",{}).items()):
        bearings = object_bearings(position,name)
        if not bearings:
            continue
        angle = next((a for a in bearings if a in (90,270)),min(bearings))
        radians = math.radians(angle)
        x,y = 3*math.sin(radians),3*math.cos(radians)
        # Separate schematic markers at the same wall; no metric claim is made.
        offset = ((index%3)-1)*.45
        x += offset if angle in (0,180) else 0
        y += offset if angle in (90,270) else 0
        items.append(PlanObject(source_name=name,footprint=[PlanPoint(x=x-.22,y=y-.22),PlanPoint(x=x+.22,y=y-.22),
                                                          PlanPoint(x=x+.22,y=y+.22),PlanPoint(x=x-.22,y=y+.22)]))
    return CameraPlan(boundary=[PlanPoint(x=-4,y=-4),PlanPoint(x=4,y=-4),PlanPoint(x=4,y=4),PlanPoint(x=-4,y=4)],
                      camera=PlanPoint(x=0,y=0),objects=items)


def resolved_plan(spec):
    data = spec.get("camera_plan")
    signature = source_signature(spec.get("floor_plan",""),spec.get("object_layout",{}))
    if data and spec.get("camera_plan_signature") == signature:
        plan,kind = CameraPlan.model_validate(data),"metric"
    else:
        plan,kind = _schematic(spec),"schematic"
    from .object_orientation import orientation,has_orientation
    for item in plan.objects:
        yaw,definition = orientation(spec.get("object_layout",{}).get(item.source_name,""))
        if has_orientation(spec.get("object_layout",{}).get(item.source_name,"")):
            item.front_yaw,item.front_definition = yaw,definition
    return plan,kind


def visible_names(spec,name):
    """Metric footprints, not wall-name guesses, decide visibility when available."""
    plan,kind = resolved_plan(spec)
    if kind != "metric":
        return None
    from .location_bridges import CAMERAS
    center,fov = CAMERAS[name]
    visible = set()
    for item in plan.objects:
        points = item.footprint
        for a,b in zip(points,points[1:]+points[:1]):
            for step in range(17):
                t = step/16
                x,y = a.x+(b.x-a.x)*t-plan.camera.x,a.y+(b.y-a.y)*t-plan.camera.y
                if abs((math.degrees(math.atan2(x,y))-center+180)%360-180) <= fov/2:
                    visible.add(item.source_name)
                    break
    return visible


def reference_signature(spec):
    return input_hash({key:spec.get(key) for key in ("floor_plan","object_layout","camera_plan","camera_plan_signature")})


def publish_guides(engine,lid,spec):
    """Only the explicit image-generation action publishes master prerequisites."""
    from .dual_panorama import root
    if not spec.get("floor_plan","").strip() or not spec.get("object_layout"):
        raise ValueError("Provide a floor plan and object layout before generating guide images.")
    guides = generate_guides(engine,lid,spec)
    guides["referenceSignature"] = reference_signature(spec)
    atomic_write(root(engine,lid)/"camera_guides.json",json.dumps(guides,ensure_ascii=False,indent=2))
    from .floor_plan_revisions import capture
    capture(engine,lid,spec,source="Floor-plan images",guides=guides)
    return guides


def current_guides(engine,lid,spec):
    from .dual_panorama import root
    from .media_utils import file_hash
    try:
        guides = read_json(root(engine,lid)/"camera_guides.json")
        if not guides.get("floorPlan"):
            return None
        if guides.get("referenceSignature") != reference_signature(spec):
            return None
        from .dual_panorama import MASTERS
        for name in MASTERS:
            for field in ("floorPlan","cameraGuide","visibleFloorPlan"):
                view = guides["views"][name]
                if file_hash(engine.local_path(view[field])) != view[field+"Hash"]:
                    return None
        return guides
    except (OSError,ValueError,KeyError,TypeError):
        return None


def require_guides(engine,lid,spec):
    guides = current_guides(engine,lid,spec)
    if not guides:
        raise ValueError("Generate floor plan image for the current saved floor plan and object layout before creating a master image.")
    return guides


def generate_guides(engine,lid,spec):
    from .dual_panorama import root,MASTERS
    from .location_view_context import view_context
    from .media_utils import file_hash
    plan,kind = resolved_plan(spec)
    ids = stable_ids(engine,lid,spec.get("object_layout",{}),plan)
    for item in plan.objects:
        if item.source_name not in ids:
            raise ValueError("Camera guide contains an object absent from the object layout.")
        item.object_id = ids[item.source_name]
    signature = input_hash({"plan":plan.model_dump(mode="json"),"source":source_signature(spec.get("floor_plan",""),spec.get("object_layout",{})),"ids":ids,"version":"camera-guides-v8-object-fronts"})
    folder = root(engine,lid)/"guides"/signature[:16]
    folder.mkdir(parents=True,exist_ok=True)
    meta_file = folder/"guides.json"
    if meta_file.exists():
        cached = read_json(meta_file)
        if all(engine.local_path(entry[field]).is_file() and file_hash(engine.local_path(entry[field])) == entry[field+"Hash"]
               for entry in cached["views"].values() for field in ("floorPlan","cameraGuide","visibleFloorPlan")):
            return cached
    plot_size = 900
    width,height = 1260,max(900,150+len(plan.objects)*58)
    pad = 95
    xs,ys = [p.x for p in plan.boundary],[p.y for p in plan.boundary]
    if max(xs) <= min(xs) or max(ys) <= min(ys):
        raise ValueError("Floor-plan boundary needs nonzero width and depth.")
    scale = min((plot_size-pad*2)/(max(xs)-min(xs)),(plot_size-pad*2)/(max(ys)-min(ys)))
    def xy(point):
        return pad+(point.x-min(xs))*scale,plot_size-pad-(point.y-min(ys))*scale
    camera = xy(plan.camera)
    try:
        font = ImageFont.truetype("msjh.ttc",16)
        large = ImageFont.truetype("msjh.ttc",22)
    except OSError:
        font,large = ImageFont.load_default(size=16),ImageFont.load_default(size=22)
    base = Image.new("RGB",(width,height),"white")
    draw = ImageDraw.Draw(base)
    boundary = [xy(point) for point in plan.boundary]
    draw.polygon(boundary,fill="#f4f4f4",outline="#202020",width=4)
    meta = {"signature":signature,"geometrySource":kind,"units":"metres" if kind == "metric" else "schematic / not to scale",
            "objectIds":ids,"camera":plan.camera.model_dump(),"cameraHeightMeters":plan.camera_height_m,
            "axes":"x=east, y=north","objects":{item.object_id:item.model_dump(mode="json") for item in plan.objects},"views":{}}
    colors = {}
    for index,identity in enumerate(sorted(ids.values())):
        hue = (index*.61803398875)%1
        colors[identity] = tuple(round(v*255) for v in colorsys.hsv_to_rgb(hue,.38,.96))
        meta["objects"].get(identity,{}).update(diagramColor=colors[identity])
    from .location_bridges import BRIDGES,CAMERAS,context as bridge_context
    for name in ("full_layout",*MASTERS):
        full_layout = name == "full_layout"
        facing,fov = CAMERAS.get(name,(0,360))
        mask = Image.new("L",(width,height),0)
        mdraw = ImageDraw.Draw(mask)
        radius = math.hypot(width,height)*2
        cone = [camera]+[(camera[0]+radius*math.sin(math.radians(facing-fov/2+fov*i/160)),
                          camera[1]-radius*math.cos(math.radians(facing-fov/2+fov*i/160))) for i in range(161)]
        mdraw.polygon(cone,fill=255)
        room_mask = Image.new("L",(width,height),0)
        ImageDraw.Draw(room_mask).polygon(boundary,fill=255)
        mask = ImageChops.multiply(mask,room_mask)
        if full_layout:
            mask = room_mask
        floor = base.copy()
        entries = []
        render_spec = {**spec,"camera_plan":plan.model_dump(mode="json",by_alias=True)} if kind == "metric" else spec
        scope = ({"visibleObjects":{item.source_name for item in plan.objects}} if full_layout else
                 bridge_context(render_spec,name) if name in BRIDGES else view_context(render_spec,name))
        for item in plan.objects:
            if item.source_name not in scope["visibleObjects"]:
                continue
            layer = Image.new("L",(width,height),0)
            ImageDraw.Draw(layer).polygon([xy(point) for point in item.footprint],fill=255)
            clipped = ImageChops.multiply(layer,mask)
            box = clipped.getbbox()
            if box is None:
                continue
            entries.append({"item":item,"mask":clipped,"box":box,"color":colors[item.object_id]})
        # Large footprints go underneath smaller fixtures. All outlines and
        # labels are drawn after all fills, so later objects cannot erase IDs.
        entries.sort(key=lambda entry:sum(entry["mask"].histogram()[1:]),reverse=True)
        for entry in entries:
            floor.paste(entry["color"],mask=entry["mask"])
        for entry in entries:
            edge = ImageChops.subtract(entry["mask"],entry["mask"].filter(ImageFilter.MinFilter(5)))
            floor.paste("#273444",mask=edge)
        ImageDraw.Draw(floor).line(boundary+[boundary[0]],fill="#202020",width=3)
        entries.sort(key=lambda entry:entry["item"].object_id)
        occupied = [(camera[0]-18,camera[1]-85,camera[0]+18,camera[1]+85)]
        labels = []
        for entry in entries:
            label = entry["item"].object_id
            bounds = ImageDraw.Draw(floor).textbbox((0,0),label,font=font)
            lw,lh = min(plot_size-40,bounds[2]-bounds[0]+(44 if entry["item"].front_yaw is not None else 16)),30
            box = entry["box"]
            anchor = ((box[0]+box[2])/2,(box[1]+box[3])/2)
            candidates = [(box[0],box[1]-lh-5),(box[0],box[3]+5),(anchor[0]-lw/2,anchor[1]-lh/2)]
            candidates += [(x,y) for y in range(60,plot_size-95,35) for x in range(20,plot_size-int(lw)-15,40)]
            def overlaps(a,b):
                return a[0] < b[2]+5 and a[2]+5 > b[0] and a[1] < b[3]+5 and a[3]+5 > b[1]
            available = []
            for x,y in candidates:
                rect = (max(15,min(plot_size-lw-15,x)),max(55,min(plot_size-lh-80,y)))
                rect = (*rect,rect[0]+lw,rect[1]+lh)
                if not any(overlaps(rect,other) for other in occupied):
                    available.append(rect)
            def label_cost(rect):
                covered = sum(max(0,min(rect[2],other["box"][2])-max(rect[0],other["box"][0]))*
                              max(0,min(rect[3],other["box"][3])-max(rect[1],other["box"][1])) for other in entries)
                return covered*10000+(rect[0]+lw/2-anchor[0])**2+(rect[1]+lh/2-anchor[1])**2
            rect = min(available,key=label_cost) if available else (20,60,20+lw,60+lh)
            occupied.append(rect)
            labels.append({"objectId":label,"box":rect,"anchor":anchor,"color":entry["color"],
                "frontYaw":entry["item"].front_yaw,"frontDefinition":entry["item"].front_definition})
        def annotations(image):
            draw = ImageDraw.Draw(image)
            # Leaders first, opaque labels last; FOV tint never washes out text.
            for label in labels:
                box = label["box"]
                draw.line([label["anchor"],((box[0]+box[2])/2,(box[1]+box[3])/2)],fill="#495769",width=2)
            for label in labels:
                box = label["box"]
                draw.rounded_rectangle(box,radius=5,fill="white",outline="#273444",width=2)
                draw.rectangle((box[0]+2,box[1]+3,box[0]+7,box[3]-3),fill=label["color"])
                draw.text((box[0]+11,box[1]+3),label["objectId"],font=font,fill="#111111")
                if label["frontYaw"] is not None:
                    from .object_orientation import arrow
                    arrow(draw,(box[2]-15,(box[1]+box[3])/2),label["frontYaw"])
            draw.rounded_rectangle((920,55,width-15,height-85),radius=9,fill="#f1f5f9",outline="#ccd5df")
            draw.text((937,67),"ALL OBJECTS / STABLE IDs" if full_layout else "VISIBLE OBJECTS / STABLE IDs",font=font,fill="#111111")
            for index,entry in enumerate(entries):
                y = 105+index*58
                draw.rectangle((937,y+2,955,y+20),fill=entry["color"],outline="#273444")
                draw.text((965,y),entry["item"].object_id,font=font,fill="#111111")
                name = entry["item"].source_name
                while len(name)>1 and draw.textlength(name,font=font)>width-980:
                    name = name[:-2]+"…"
                draw.text((965,y+24),name,font=font,fill="#39465a")
        title = "Complete object layout" if full_layout else name.replace("_"," ").upper()
        ImageDraw.Draw(floor).text((20,15),f"{title} | {meta['units']}",font=large,fill="black")
        if full_layout:
            annotations(floor)
            floor_path = folder/"floor_plan.png"
            floor.save(floor_path)
            meta.update(floorPlan=floor_path.relative_to(engine.path).as_posix(),floorPlanHash=file_hash(floor_path),
                        floorPlanObjectIds=[entry["item"].object_id for entry in entries],floorPlanLabels=labels)
            continue
        guide = floor.copy()
        tint = Image.new("RGB",guide.size,"#d0f4d9")
        guide = Image.composite(Image.blend(guide,tint,.24),guide,mask)
        gdraw = ImageDraw.Draw(guide)
        rays = Image.new("L",guide.size,0)
        ray_draw = ImageDraw.Draw(rays)
        ray_draw.line(cone[:2],fill=255,width=3)
        ray_draw.line([camera,cone[-1]],fill=255,width=3)
        guide.paste("#2c8c46",mask=ImageChops.multiply(rays,room_mask))
        gdraw = ImageDraw.Draw(guide)
        r = math.radians(facing)
        end = (camera[0]+70*math.sin(r),camera[1]-70*math.cos(r))
        gdraw.line([camera,end],fill="#dc2525",width=6)
        gdraw.ellipse((camera[0]-7,camera[1]-7,camera[0]+7,camera[1]+7),fill="#dc2525")
        base_arrow = (end[0]-15*math.sin(r),end[1]+15*math.cos(r))
        gdraw.polygon([end,(base_arrow[0]+10*math.cos(r),base_arrow[1]+10*math.sin(r)),
                      (base_arrow[0]-10*math.cos(r),base_arrow[1]-10*math.sin(r))],fill="#dc2525")
        gdraw.text((20,height-58),f"Camera {facing} deg | effective horizontal FOV {fov} deg | same center/height",font=font,fill="black")
        gdraw.text((20,height-33),"GUIDE LABELS ONLY - never render IDs, arrows or this diagram in the panorama",font=font,fill="black")
        guide_path = folder/(name+"_camera.png")
        annotations(floor)
        visible_floor_path = folder/(name+"_visible_floor.png")
        floor.save(visible_floor_path)
        annotations(guide)
        guide.save(guide_path)
        meta["views"][name] = {"yaw":facing,"horizontalFov":fov,"visibleObjectIds":[entry["item"].object_id for entry in entries],
            "labels":labels,"renderLayers":["room","objectFillsLargeToSmall","objectOutlines","cameraOverlay","leaders","labels","legend"],
            "floorPlan":meta["floorPlan"],"cameraGuide":guide_path.relative_to(engine.path).as_posix(),
            "floorPlanHash":meta["floorPlanHash"],"cameraGuideHash":file_hash(guide_path)}
        meta["views"][name].update(visibleFloorPlan=visible_floor_path.relative_to(engine.path).as_posix(),
                                  visibleFloorPlanHash=file_hash(visible_floor_path))
    atomic_write(meta_file,json.dumps(meta,ensure_ascii=False,indent=2))
    return meta
