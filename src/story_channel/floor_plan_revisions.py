"""Immutable floor-plan/object-layout/camera snapshots, with local-only restore."""
import json
import re

from .files import atomic_write,input_hash
from .file_cache import read_json
from .floor_plan_guides import source_signature,CameraPlan
from .models import utc_now
from .locking import project_lock


def layout(spec):
    floor,objects = spec.get("floor_plan",""),spec.get("object_layout",{})
    signature = source_signature(floor,objects)
    plan = spec.get("camera_plan") if spec.get("camera_plan_signature") == signature else None
    if plan:
        plan = CameraPlan.model_validate(plan).model_dump(mode="json",by_alias=True)
    return {"floor_plan":floor,"object_layout":objects,"camera_plan":plan,
        "camera_plan_signature":signature if plan else ""}


def signature(spec):
    return input_hash(layout(spec))


def folder(engine,lid):
    from .dual_panorama import root
    return root(engine,lid)/"floor_plan_revisions"


def revisions(engine,lid):
    return sorted((read_json(file) for file in folder(engine,lid).glob("*.json")),
                  key=lambda item:item["revision"],reverse=True)


def capture(engine,lid,spec,*,source="Saved settings",guides=None):
    data = layout(spec)
    if not data["floor_plan"].strip() and not data["object_layout"]:
        return None
    identity = signature(data)
    directory = folder(engine,lid)
    directory.mkdir(parents=True,exist_ok=True)
    with project_lock(directory):
        file = directory/(identity+".json")
        if file.exists():
            item = read_json(file)
        else:
            item = {"id":identity,"revision":max((row["revision"] for row in revisions(engine,lid)),default=0)+1,
                "createdAt":utc_now().isoformat(),"source":source,"layout":data}
        if guides is None:
            from .floor_plan_guides import current_guides
            guides = current_guides(engine,lid,spec)
        if guides:
            item["guides"] = guides
        if not file.exists() or read_json(file) != item:
            atomic_write(file,json.dumps(item,ensure_ascii=False,indent=2))
        return item


def selected(engine,lid,identity):
    if not re.fullmatch(r"[a-f0-9]{64}",identity):
        raise ValueError("Choose a saved floor-plan revision.")
    item = read_json(folder(engine,lid)/(identity+".json"))
    if item["id"] != identity or signature(item["layout"]) != identity:
        raise ValueError("The saved floor-plan revision is inconsistent.")
    return item


def preview(engine,lid,identity):
    item = selected(engine,lid,identity)
    data = item["layout"]
    if data["floor_plan"].strip() and data["object_layout"]:
        from .floor_plan_guides import generate_guides
        # Local drawings only. Preview never changes the active settings or
        # publishes the guide marker used to authorize master generation.
        guides = generate_guides(engine,lid,data)
        item = capture(engine,lid,data,source=item["source"],guides=guides)
    return item


def apply(engine,lid,identity,*,expected=None):
    from .background_library import BackgroundLibrary
    from .floor_plan_guides import publish_guides
    item = selected(engine,lid,identity)
    data = item["layout"]
    state = BackgroundLibrary(engine).configure_references(lid,floor_plan=data["floor_plan"],object_layout=data["object_layout"],
        camera_plan=data["camera_plan"],camera_plan_signature=data["camera_plan_signature"],expected_layout=expected)
    guides = publish_guides(engine,lid,state.location_reference_sets[lid]) if data["floor_plan"].strip() and data["object_layout"] else None
    with project_lock(engine.path):
        state = engine.load()
        if signature(state.location_reference_sets[lid]) != identity:
            raise ValueError("Reference settings changed while applying this revision. Reload the current layout.")
        for approvals in (state.location_master_approvals,state.location_yaw_approvals):
            for key in list(approvals):
                if key.startswith(lid+":"):
                    approvals.pop(key)
        state.background_approvals.pop(lid,None)
        engine._save(state)
    return {"revision":item,"guides":guides}
