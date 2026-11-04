"""Bind only the reviewed prop into an episode; approval remains episode-owned."""
import json

from .files import input_hash,atomic_write,atomic_copy
from .media_utils import file_hash


def selected(state,pid,mode):
    key = f"prop:{pid}:{mode}"
    versions = state["visuals"].get(key,[])
    revision = state["activeVisuals"].get(key,{}).get("revision")
    return next((v for v in versions if v["revision"] == revision),versions[-1] if revision is None and versions else {})


def digest(state,pid,modes):
    return input_hash([state["props"].get(pid),{mode:selected(state,pid,mode) for mode in modes}])


def sync(media,episode_id,pid):
    if episode_id == media.project_id:
        return
    manifest = media.manager.load(episode_id)
    if manifest.world_project_id != media.project_id:
        raise ValueError("This prop belongs to a different World.")
    source = media.load()
    prop = source["props"].get(pid)
    if not prop:
        raise ValueError("Save this prop draft before using it in the episode.")
    from .library_media import LibraryMedia
    from .identity import IdentityManager,Prop
    from .locking import project_lock
    from .ledger_lock import ledger_lock
    target = LibraryMedia(media.manager,episode_id)
    root_identity = IdentityManager(media.manager,media.project_id).load()
    definition = root_identity.props.get(pid) if root_identity else None
    with project_lock(target.path),ledger_lock(target.path):
        state = target.load()
        state["props"][pid] = dict(prop)
        for mode in prop["modes"]:
            version = selected(source,pid,mode)
            if not version:
                continue
            relative = version["asset"]["filePath"]
            original = media.assets.local_path(relative)
            destination = target.assets.local_path(relative)
            expected = version.get("fileHash")
            if not original.is_file() or expected and file_hash(original) != expected:
                raise ValueError("The prop image is missing or changed. Regenerate it before approving.")
            atomic_copy(original,destination)
            key = f"prop:{pid}:{mode}"
            versions = state["visuals"].setdefault(key,[])
            versions[:] = [v for v in versions if v["revision"] != version["revision"]]
            versions.append(version)
            state["activeVisuals"][key] = {"revision":version["revision"],"sourceHash":version.get("sourceHash","")}
        atomic_write(target.file,json.dumps(state,ensure_ascii=False,indent=2))
        identity_manager = IdentityManager(media.manager,episode_id)
        identity = identity_manager.load()
        if identity:
            identity.props[pid] = definition.model_copy(deep=True) if definition else Prop(prop_id=pid,description=prop["description"])
            identity_manager._write(identity)


def approve(media,episode_id,pid,modes,*,expected):
    from .locking import project_lock
    with project_lock(media.path):
        return _approve(media,episode_id,pid,modes,expected=expected)


def _approve(media,episode_id,pid,modes,*,expected):
    if digest(media.load(),pid,modes) != expected:
        raise ValueError("This prop changed. Review its latest images before approving.")
    sync(media,episode_id,pid)
    from .storyboard_asset_check import check,approve as approve_asset
    required = {f"prop:{pid}:{mode}" for mode in modes}
    rows = [row for row in check(media.manager,episode_id) if row["Category"] == "Props" and row["ID"] in required]
    if {row["ID"] for row in rows} != required or not all(row["Ready"] for row in rows):
        raise ValueError("Generate every required prop mode before approving.")
    for row in rows:
        approve_asset(media.manager,episode_id,"Props",row["ID"],expected=row["ContentHash"])
