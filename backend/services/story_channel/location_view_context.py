"""View-local layout notes and identical east/west boundary object contracts."""
import json
import re
import math

VIEW_SCOPE_VERSION = "four-directional-masters-110-v1"

_DIRECTIONS = (
    ("northeast",45,r"north[ -]?east|東北|东北"), ("southeast",135,r"south[ -]?east|東南|东南"),
    ("southwest",225,r"south[ -]?west|西南"), ("northwest",315,r"north[ -]?west|西北"),
    ("north",0,r"\bnorth\b|\bN\b|\bfront\b|北|前方|前牆|前墙"),
    ("east",90,r"\beast\b|\bE\b|東|东"), ("south",180,r"\bsouth\b|\bS\b|\brear\b|\bback\b|南|後方|后方|後牆|后墙"),
    ("west",270,r"\bwest\b|\bW\b|西"))


def bearings(text):
    values = set()
    remaining = re.sub(r"(?<=[a-z])(?=[A-Z])"," ",str(text)).replace("_"," ")
    for name,angle,pattern in _DIRECTIONS:
        if re.search(pattern,remaining,re.I):
            values.add(angle)
            remaining = re.sub(pattern," ",remaining,flags=re.I)
    for match in re.finditer(r"(?:yaw|azimuth|方位角)\s*[:=]?\s*(-?\d+(?:\.\d+)?)",str(text),re.I):
        values.add(float(match[1])%360)
    return values


def visible(angles, name):
    from .location_bridges import CAMERAS
    center,fov = CAMERAS[name]
    return not angles or any(abs((angle-center+180)%360-180) <= fov/2 for angle in angles)


def object_bearings(position,key=""):
    from .object_orientation import position_only
    position = position_only(position)
    direction = bearings(position) or bearings(key)
    # A west-wall object near the north corner is northwest, not a shared
    # object centered on the west overlap. Spanning objects keep both ends.
    spanning = re.search(r"spans?|extends?|across|between|from.+to|延伸|橫跨|横跨|貫穿|贯穿",str(position),re.I)
    if not spanning:
        diagonal = {frozenset((0,90)):45,frozenset((90,180)):135,
                    frozenset((180,270)):225,frozenset((0,270)):315}
        if frozenset(direction) in diagonal:
            return {diagonal[frozenset(direction)]}
    return direction


def _norm(text):
    return re.sub(r"[\W_]+","",str(text).casefold())


def scoped_text(text, name, *, excluded=()):
    """Filter clauses/heading groups, including English/Chinese wall notes."""
    if not text:
        return ""
    if isinstance(text,(dict,list)):
        return json.dumps(_structured(text,name,excluded=excluded),ensure_ascii=False)
    raw = str(text).strip()
    try:
        structured = json.loads(raw)
        if isinstance(structured,(dict,list)):
            return json.dumps(_structured(structured,name,excluded=excluded),ensure_ascii=False)
    except ValueError:
        pass
    output = []
    heading = set()
    for line in raw.splitlines():
        if prefix := re.match(r"\s*([^:：]{1,40})[:：]",line):
            heading = bearings(prefix[1])
        for clause in re.split(r"[;；,，。\n]+|(?<!\d)\.(?!\d)",line):
            clause = clause.strip()
            if not clause:
                continue
            direction = bearings(clause) or heading
            if not all(visible({angle},name) for angle in direction) or any(_norm(item) in _norm(clause) for item in excluded if _norm(item)):
                continue
            output.append(clause)
    return "; ".join(output)


def _structured(value,name,*,excluded=()):
    if isinstance(value,dict):
        result = {}
        for key,item in value.items():
            if not visible(bearings(key),name) or any(_norm(word) in _norm(key) for word in excluded if _norm(word)):
                continue
            filtered = _structured(item,name,excluded=excluded)
            if filtered not in ("",{},[]):
                result[key] = filtered
        return result
    if isinstance(value,list):
        return [item for child in value if (item := _structured(child,name,excluded=excluded)) not in ("",{},[])]
    return scoped_text(value,name,excluded=excluded) if isinstance(value,str) else value


def _side_segment(key,position,side,name):
    """Describe one visible segment without inventing lengths for vague text."""
    north = name == "front_panorama"
    data = {}
    try:
        parsed = json.loads(str(position))
        if isinstance(parsed,dict):
            data = parsed
    except ValueError:
        pass
    extent = data.get("northSouthRange")
    if extent is None and re.search(r"(?:full|entire|whole)\s+(?:length\s+of\s+(?:the\s+)?)?(?:east\s+|west\s+)?wall|整[面條条段].*(?:牆|墙)|(?:牆|墙).*(?:全長|全长)",str(position),re.I):
        extent = [0,1]
    if extent is not None:
        if not isinstance(extent,list) or len(extent) != 2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in extent) or not 0 <= extent[0] < extent[1] <= 1:
            raise ValueError(f"{key}: northSouthRange must be [start,end] between 0 (north end) and 1 (south end).")
        core = [max(extent[0],0 if north else .5),min(extent[1],.5 if north else 1)]
        if core[0] > core[1]:
            return None
    else:
        core = None
    rule = ("Draw only the north-visible portion" if north else "Draw only the south-visible portion")
    rule += "; include the same boundary slice only where it falls inside this view's angular overlap. Never draw the whole object's full length again."
    result = {"objectId":key,"sideWall":side,"segment":"north" if north else "south",
              "connectionId":side+":"+key,"renderInstruction":rule,
              "visibleDetails":data.get("northSegment" if north else "southSegment","")}
    if core is not None:
        result.update(wallFractionConvention="0=north wall end, 1=south wall end; nominal split=0.5",
                      coreWallFractionRange=core,objectWallFractionRange=extent,
                      crossesViewSplit=extent[0] < .5 < extent[1],touchesViewSplit=extent[0] <= .5 <= extent[1])
        if core[0] == core[1]:
            result["overlapOnly"] = True
            result["renderInstruction"] = "Only the shared angular-overlap slice is visible here; no object core lies in this half. Preserve the documented natural endpoint; do not extend the object past it."
        length = data.get("totalLengthMeters")
        if isinstance(length,(int,float)) and not isinstance(length,bool) and math.isfinite(length) and length > 0:
            result["coreLengthMeters"] = round(length*(core[1]-core[0])/(extent[1]-extent[0]),6)
    else:
        result["extentRule"] = "Use saved placement and the approved reference; the exact endpoint is unspecified. Do not invent a half-length or move the object."
    if data:
        result["placement"] = scoped_text(data.get("position",data.get("wall",side+" wall")),name) or side+" wall"
    else:
        result["placement"] = scoped_text(position,name)
    return result


def _side_plan(name,selected,objects):
    """Keep the selected side-wall content, explicitly bound to this half."""
    result = {}
    north = name == "front_panorama"
    for side in ("east","west"):
        result[side] = {"coreRegion":"north half" if north else "south half",
                       "nominalWallFractionRange":[0,.5] if north else [.5,1],
                       "screenSide":("right" if side == "east" else "left") if north else ("left" if side == "east" else "right"),
                       "overlapYawRange":[80,100] if side == "east" else [260,280],
                       "objectRefs":[key for key,value in selected.items() if
                           (isinstance(value,dict) and value.get("sideWall") == side) or
                           any(abs((angle-(90 if side == "east" else 270)+180)%360-180) <= 45 for angle in object_bearings(objects.get(key,""),key))],
                       "rule":"Render this view's side-wall portion, not the entire side wall. The opposite core half is off-screen except the shared overlap. Keep original placement and perspective."}
    return result


def _segmented_floor_plan(text,name,hidden,side_plan):
    filtered = scoped_text(text,name,excluded=hidden)
    def qualify(value):
        if isinstance(value,dict):
            result = {}
            for key,item in value.items():
                side = next((wall for wall,angle in (("east",90),("west",270)) if bearings(key) == {angle}),None)
                if side:
                    result[key] = {"coreRegion":side_plan[side]["coreRegion"],"layout":qualify(item),
                        "rule":"Only this half plus shared angular overlap is visible; full-wall dimensions describe the original wall, not a new full wall to draw in this view."}
                else:
                    result[key] = qualify(item)
            return result
        if isinstance(value,list):
            return [qualify(item) for item in value]
        return value
    try:
        parsed = json.loads(filtered)
        if isinstance(parsed,(dict,list)):
            return json.dumps(qualify(parsed),ensure_ascii=False)
    except ValueError:
        pass
    half = "north" if name == "front_panorama" else "south"
    return filtered+f"; EAST and WEST layout: show only the {half} portion plus its shared angular overlap; do not draw the full side walls again. Crossing furniture continues at the same join."


def view_context(spec,name,*,notes="",visible_objects=None):
    from .directional_masters import NAMES,context
    if name in NAMES:
        return context(spec,name,notes,visible_objects)
    objects = spec.get("object_layout",{})
    from .floor_plan_guides import visible_names,resolved_plan
    metric_visible = visible_names(spec,name)
    if visible_objects is not None:
        metric_visible = set(visible_objects)
    plan,kind = resolved_plan(spec)
    geometry = {item.source_name:item for item in plan.objects} if kind == "metric" else {}
    def directions(key,position):
        if key not in geometry:
            return object_bearings(position,key)
        item = geometry[key]
        result = {math.degrees(math.atan2(point.x-plan.camera.x,point.y-plan.camera.y))%360 for point in item.footprint}
        if min(p.y for p in item.footprint) <= plan.camera.y <= max(p.y for p in item.footprint):
            result.add(90 if sum(p.x for p in item.footprint)/len(item.footprint) >= plan.camera.x else 270)
        return result
    hidden = [key for key,position in objects.items() if (key not in metric_visible if metric_visible is not None else
               not (direction := directions(key,position)) or not visible(direction,name))]
    selected,unplaced = {},[]
    shared = {"east":{},"west":{}}
    for key,position in objects.items():
        direction = directions(key,position)
        if not direction:
            unplaced.append(key)
            continue
        if key in metric_visible if metric_visible is not None else visible(direction,name):
            selected[key] = scoped_text(position,name,excluded=hidden) or ("east wall" if 90 in direction else "west wall" if 270 in direction else "fixed visible anchor")
        elif metric_visible is not None:
            continue
        # Both prompts share the exact same side-wall anchors; coordinates,
        # dimensions and materials stay attached to the same object identity.
        for side,angle in (("east",90),("west",270)):
            if any(abs((bearing-angle+180)%360-180) <= 10 for bearing in direction):
                spanning = bool(re.search(r"long|spans?|extends?|across|between|entire|full.wall|desk|table|shel[fv]|bookcase|counter|bench|長|长|延伸|橫跨|横跨|整面|桌|書架|书架|櫃|柜",key+" "+str(position),re.I))
                data = {}
                try:
                    raw = json.loads(str(position))
                    if isinstance(raw,dict):
                        data = raw
                except ValueError:
                    pass
                spanning = spanning or bool(data.get("northSouthRange") or data.get("northSegment") or data.get("southSegment"))
                if spanning:
                    segment = _side_segment(key,position,side,name)
                    if segment is None:
                        selected.pop(key,None)
                        continue
                    selected[key] = segment
                    if segment.get("touchesViewSplit") is False:
                        selected[key].pop("connectionId",None)
                        continue
                common = "; ".join(part.strip() for part in re.split(r"[;；,，]",str(position))
                                   if not bearings(part) or all(min(abs((bearing-target+180)%360-180) for target in (90,270)) <= 10 for bearing in bearings(part)))
                if data:
                    common = json.dumps({field:value for field,value in data.items() if field not in ("northSegment","southSegment","northSouthRange")},ensure_ascii=False)
                shared[side][key] = {"anchor":side+" angular overlap", "placement":common,
                    "rule":"Same object, scale, elevation, material and fixed position in both views; render only its visible portion."}
                if spanning:
                    shared[side][key].update(connectionId=side+":"+key,
                        connectionRule="One continuous object, not two separate copies. Match tabletop/shelf planes, edge profile, height, depth, material/grain and lighting at the join. Continue existing shelf levels and supports; no artificial end cap, partition, extra leg, gap, seam step or doubled section at the split.")
                    if selected[key].get("objectWallFractionRange"):
                        shared[side][key]["objectWallFractionRange"] = selected[key]["objectWallFractionRange"]
                    if selected[key].get("crossesViewSplit") is False:
                        shared[side][key]["connectionRule"] = "The same documented natural object endpoint is visible in the shared overlap. Match its endpoint, cap/support, height, depth and material. Do not extend it into the other core half or invent another end because of camera framing."
    # Ranges may establish that an east/west item belongs only to the other
    # half. Exclude its name from loose prose as well as the object registry.
    hidden.extend(key for key in objects if key not in selected)
    hidden.extend(item.object_id for key,item in geometry.items() if key not in selected and item.object_id)
    for value in selected.values():
        if isinstance(value,dict):
            value["visibleDetails"] = scoped_text(value["visibleDetails"],name,excluded=hidden)
    for key,value in list(selected.items()):
        if key in geometry:
            selected[key] = {**value,"objectId":geometry[key].object_id or key,"guideFootprintAuthoritative":True} if isinstance(value,dict) else {
                "objectId":geometry[key].object_id or key,"position":value,"guideFootprintAuthoritative":True}
    side_plan = _side_plan(name,selected,objects)
    from .files import input_hash
    return {"scopeVersion":VIEW_SCOPE_VERSION,"cameraPlanSignature":input_hash(spec.get("camera_plan")),"facing":"north" if name == "front_panorama" else "south",
            "visibleWalls":["north","west","east"] if name == "front_panorama" else ["south","east","west"],
            "floorPlan":_segmented_floor_plan(spec.get("floor_plan",""),name,hidden,side_plan),
            "visibleObjects":selected,"appearanceNotes":scoped_text(notes,name,excluded=hidden),
            "sideWallSegments":side_plan,
            "sharedBoundaryObjects":shared,
            "unplacedObjectCount":len(unplaced),
            "unplacedObjectRule":"Do not invent placements or duplicate unpositioned items; use only registered visible anchors."}

