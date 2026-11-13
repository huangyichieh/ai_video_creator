"""Planning context and declarations for optional local backgrounds."""
from .file_cache import read_json


def reference_context(manager,project_id):
    manifest = manager.load(project_id)
    owner = manifest.world_project_id or project_id
    path = manager.project_path(owner)
    def read(name):
        return read_json(path/name) if (path/name).exists() else {}
    world,library,production = read("world.json"),read("background_library.json"),read("production.json")
    removed = set(library.get("removed",[]))
    backgrounds = {item["locationId"]:item for item in [*world.get("locations",[]),*library.get("added",[])] if item["locationId"] not in removed}
    for lid,patch in library.get("overrides",{}).items():
        if lid in backgrounds:
            backgrounds[lid] = {**backgrounds[lid],**patch}
    details = {}
    for lid,spec in production.get("locationReferenceSets",{}).items():
        if lid not in backgrounds or spec.get("reference_type") == "SIMPLE":
            continue
        details[lid] = {identity:{"detailId":identity,"area":ref.get("area",identity),"description":ref.get("description",""),
            "hasImage":bool(ref.get("file")),"status":ref.get("status","generated" if ref.get("file") else "draft")}
            for identity,ref in spec.get("detail_references",{}).items()}
    return {"sharedBackgrounds":list(backgrounds.values()),"detailBackgrounds":details,
        "locationTypes":{lid:"SIMPLE" if spec.get("reference_type") == "SIMPLE" else "DETAIL" for lid,spec in production.get("locationReferenceSets",{}).items() if lid in backgrounds}}


def validate(board,context):
    locations = {loc["locationId"] for loc in context["world"]["locations"]} | {loc.location_id for loc in board.backgrounds}
    existing = context.get("detailBackgrounds",{})
    for detail in board.detail_backgrounds:
        if detail.location_id not in locations:
            raise ValueError("Detail backgrounds require an existing or declared parent location: "+detail.location_id)
        if context.get("locationTypes",{}).get(detail.location_id) == "SIMPLE":
            raise ValueError("SIMPLE locations use one master; use a DETAIL location for new detail-background references.")
        previous = existing.get(detail.location_id,{}).get(detail.detail_id)
        if previous:
            continue  # A prior declaration may remain after manual asset edits; sync never overwrites it.
        if not any(scene.location_id == detail.location_id and scene.target_area == detail.detail_id for scene in board.scenes):
            raise ValueError("New detail backgrounds must be selected by a scene's locationId and targetArea.")
