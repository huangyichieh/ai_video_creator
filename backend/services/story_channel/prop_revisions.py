"""Select intact saved prop visuals without calling a generation provider."""
from .prop_removal import snapshot


def intact(media,version):
    from .storyboard_asset_check import intact as file_intact
    return bool(version and file_intact(media.path,{"file":version.get("asset",{}).get("filePath"),"fileHash":version.get("fileHash")}))


def apply(media,pid,mode,revision,*,expected,episode_id=None,source_mode=None,source_expected=None):
    from copy import deepcopy
    from .files import input_hash
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    if media.manager.load(media.project_id).world_project_id:
        raise ValueError("Apply prop revisions in the shared World's library.")
    if episode_id and episode_id != media.project_id and media.manager.load(episode_id).world_project_id != media.project_id:
        raise ValueError("This episode belongs to a different World.")
    with project_lock(media.path),ledger_lock(media.path):
        state = media.load()
        prop = state["props"].get(pid)
        if not prop or mode not in prop["modes"]:
            raise ValueError("This prop or mode no longer exists.")
        source_mode = mode if source_mode is None else source_mode
        if source_mode not in prop["modes"]:
            raise ValueError("The source mode no longer exists.")
        if snapshot(state,pid,mode) != expected:
            raise ValueError("This prop changed. Reopen saved revisions before applying.")
        key = "prop:"+pid+":"+mode
        source_key = "prop:"+pid+":"+source_mode
        version = next((item for item in state["visuals"].get(source_key,[]) if item["revision"] == revision),None)
        if not intact(media,version):
            raise ValueError("This revision's image is missing or modified. Choose an intact revision.")
        if source_expected is not None and input_hash(version) != source_expected:
            raise ValueError("The source revision changed. Preview it again before applying.")
        if source_mode != mode:
            from .check_prop_assets import selected
            origin = {"mode":source_mode,"revision":revision}
            current = selected(state,pid,mode)
            if (current.get("reusedFrom") == origin and current.get("fileHash") == version.get("fileHash")
                    and current.get("asset",{}).get("filePath") == version["asset"]["filePath"]):
                version = current
            else:
                versions = state["visuals"].setdefault(key,[])
                version = {**deepcopy(version),"revision":max((item["revision"] for item in versions),default=0)+1,"reusedFrom":origin}
                versions.append(version)
            revision = version["revision"]
        state["activeVisuals"][key] = {"revision":revision,"sourceHash":version.get("sourceHash","")}
        media.save(state)
    if episode_id and episode_id != media.project_id:
        from .check_prop_assets import sync
        sync(media,episode_id,pid)
    return version
