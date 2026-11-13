"""Delete shared prop drafts/modes without rewriting saved episode storyboards."""
from pydantic import TypeAdapter
from .models import Identifier
from .files import input_hash


def selected_keys(state,pid,mode=None):
    prefix = "prop:"+pid+":"
    keys = set(state["visuals"]) | set(state["activeVisuals"])
    return {key for key in keys if key == prefix+mode} if mode is not None else {key for key in keys if key.startswith(prefix)}


def snapshot(state,pid,mode=None):
    keys = selected_keys(state,pid,mode)
    return input_hash({"prop":state["props"].get(pid),"visuals":{key:state["visuals"].get(key) for key in keys},
        "active":{key:state["activeVisuals"].get(key) for key in keys}})


def remove(media,pid,*,mode=None,confirmation=None,expected):
    TypeAdapter(Identifier).validate_python(pid)
    if media.manager.load(media.project_id).world_project_id:
        raise ValueError("Remove reusable props on the parent World's Assets page.")
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    from .identity import IdentityManager
    from .storyboard_assets import pin_legacy_asset_catalog
    with project_lock(media.path),ledger_lock(media.path):
        state = media.load()
        prop = state["props"].get(pid)
        if not prop or mode is not None and mode not in prop["modes"]:
            raise ValueError("This prop or mode has already been removed.")
        if snapshot(state,pid,mode) != expected:
            raise ValueError("This prop changed. Reopen Remove before confirming.")
        if mode is None and confirmation != prop["title"]:
            raise ValueError("Type the full prop name exactly to confirm deletion.")
        keys = selected_keys(state,pid,mode)
        retained = {version["asset"]["filePath"] for key,versions in state["visuals"].items() if key not in keys
            for version in versions if version.get("asset",{}).get("filePath")}
        files = set()
        owned = (media.path/"images"/"library").resolve()
        for key in keys:
            for version in state["visuals"].get(key,[]):
                relative = version.get("asset",{}).get("filePath")
                if not relative or relative in retained:
                    continue
                file = media.assets.local_path(relative).resolve()
                if not file.is_relative_to(owned):
                    raise ValueError("The prop visual is outside its generated image directory; deletion was stopped.")
                files.add(file)
        history = (media.path/"history"/"prop_drafts"/pid).resolve()
        if not history.is_relative_to((media.path/"history"/"prop_drafts").resolve()):
            raise ValueError("Invalid prop history directory")
        if mode is None and history.exists():
            for file in history.glob("*.json"):
                if not file.resolve().is_relative_to(history):
                    raise ValueError("Invalid prop history file")
                files.add(file)
        pin_legacy_asset_catalog(media.manager,media.project_id)
        for key in keys:
            state["visuals"].pop(key,None)
            state["activeVisuals"].pop(key,None)
        if mode is None:
            state["props"].pop(pid)
            state["removedProps"] = sorted(set(state.get("removedProps",[])) | {pid})
            state.setdefault("removedPropModes",{}).pop(pid,None)
        else:
            prop["modes"] = [item for item in prop["modes"] if item != mode]
            removed = state.setdefault("removedPropModes",{})
            removed[pid] = sorted(set(removed.get(pid,[])) | {mode})
        media.save(state)
        if mode is None:
            identity = IdentityManager(media.manager,media.project_id)
            library = identity.load()
            if library and pid in library.props:
                library.props.pop(pid)
                identity._write(library)
        for file in files:
            file.unlink(missing_ok=True)
    return state
