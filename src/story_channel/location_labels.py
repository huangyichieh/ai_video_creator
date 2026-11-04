"""Human-readable location references; canonical JSON keeps its stable IDs."""
from .file_cache import read_json


def location_labels(project_path):
    def document(name):
        try:
            value = read_json(project_path/name)
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):
            return {}
    world = document("world.json")
    catalog = document("background_library.json")
    names = {item.get("locationId",item.get("location_id")):item.get("name","")
        for item in [*world.get("locations",[]),*catalog.get("added",[])]}
    for identity,patch in catalog.get("overrides",{}).items():
        if patch.get("name"):
            names[identity] = patch["name"]
    result = {identity:f"{identity}({name})" for identity,name in names.items() if identity and name}
    production = document("production.json")
    for lid,spec in production.get("locationReferenceSets",production.get("location_reference_sets",{})).items():
        for identity,reference in spec.get("detail_references",{}).items():
            stable_id = reference.get("id",identity)
            label = f"{lid}({names.get(lid,lid)} · {stable_id})"
            result[lid+"__detail_"+stable_id] = label
            if reference.get("key"):
                result[reference["key"]] = label
                result[reference["key"].removeprefix("backgrounds:")] = label
    return result


def location_label(identity,labels,target_area=""):
    return labels.get(identity+"__detail_"+target_area,labels.get(identity,identity)) if target_area else labels.get(identity,identity)


def display_locations(value,labels):
    if isinstance(value,list):
        return [display_locations(item,labels) for item in value]
    if isinstance(value,dict):
        return {key:location_label(item,labels,value.get("targetArea",value.get("target_area","")))
            if key in ("locationId","location_id") and isinstance(item,str) else display_locations(item,labels)
            for key,item in value.items()}
    return value
