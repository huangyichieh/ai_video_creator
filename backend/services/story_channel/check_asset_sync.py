"""Shared Assets are canonical; Check assets edits publish to the same library."""
import json

from .files import atomic_write,atomic_copy,input_hash
from .file_cache import version
from .storyboard_asset_check import document,intact


def owner(manager,project_id):
    return manager.load(project_id).world_project_id or project_id


def ensure_audio(manager,project_id,kind,identity,item):
    """Register metadata under the existing stable ID, never rewrite storyboard cues."""
    from .locking import project_lock
    from .storyboard_assets import pin_legacy_asset_catalog
    from pydantic import TypeAdapter
    from .models import Identifier
    TypeAdapter(Identifier).validate_python(identity)
    if kind not in ("BGM","SFX"):
        raise ValueError("Choose BGM or SFX.")
    path = manager.project_path(project_id)
    filename = "bgm_library.json" if kind == "BGM" else "sfx_library.json"
    with project_lock(path):
        values = document(path,filename).get("entries",{})
        if identity not in values:
            pin_legacy_asset_catalog(manager,project_id)
            values[identity] = {"title":item.get("title") or item.get("name") or identity,
                "description":item.get("description") or item.get("title") or item.get("name") or identity,
                **({"id":identity,"revision":0} if kind == "BGM" else {})}
            atomic_write(path/filename,json.dumps({"entries":values},ensure_ascii=False,indent=2))


def copy_audio(manager,source_id,target_id,item,kind,identity):
    from .assets import AssetPipeline
    from .media_utils import file_hash
    from .locking import project_lock
    source = AssetPipeline(manager,source_id)
    target = AssetPipeline(manager,target_id)
    if not intact(source.path,item):
        raise ValueError("The saved audio changed or is missing; synchronize its latest valid revision.")
    relative = item["file"]
    original,destination = source.local_path(relative),target.local_path(relative)
    with project_lock(target.path):
        if not destination.is_file() or file_hash(destination) != item["fileHash"]:
            atomic_copy(original,destination)
        if file_hash(destination) != item["fileHash"]:
            raise ValueError("Audio changed during synchronization. Try again.")
        receipt = document(source.path,relative+".source.json")
        track = item.get("provenance",{}).get("track",{})
        if track.get("licenseEvidence"):
            proof = source.local_path(track["licenseEvidence"])
            expected = track.get("licenseEvidenceHash")
            if not expected or not proof.is_file() or file_hash(proof) != expected:
                raise ValueError("The music license evidence is missing or changed. No shared binding was applied.")
            target_proof = target.local_path(track["licenseEvidence"])
            if not target_proof.is_file() or file_hash(target_proof) != expected:
                atomic_copy(proof,target_proof)
            if file_hash(target_proof) != expected:
                raise ValueError("Music license evidence changed during synchronization. Try again.")
        receipt.update(file=relative,source=item["source"],kind="bgm" if kind == "BGM" else "sfx")
        if kind == "BGM":
            receipt["bgmId"] = identity
        atomic_write(target.path/(relative+".source.json"),json.dumps(receipt,ensure_ascii=False,indent=2))


def shared_audio_hash(manager,project_id,kind,identity):
    root = owner(manager,project_id)
    if root == project_id:
        return None
    filename = "storyboard_bgm.json" if kind == "BGM" else "storyboard_sfx.json"
    return input_hash(document(manager.project_path(root),filename).get("entries",{}).get(identity))


def share_audio(manager,project_id,kind,identity,*,expected=None):
    """Publish generated/uploaded/selected episode audio to its World's Assets."""
    root = owner(manager,project_id)
    if root == project_id:
        return
    from .audio_assets import items,binding,publish
    item = binding(manager,project_id,kind,identity)
    ensure_audio(manager,root,kind,identity,{**item,**items(manager,project_id,kind).get(identity,{})})
    copy_audio(manager,project_id,root,item,kind,identity)
    publish(manager,root,kind,identity,item.get("name") or identity,item["file"],
        provenance={**item.get("provenance",{}),"sharedFromEpisodeId":project_id},expected=expected,_share=False)


def signature(manager,project_id):
    path = manager.project_path(project_id)
    result = []
    for name in ("production.json","identity_library.json","background_library.json","library_media.json",
        "bgm_library.json","sfx_library.json","storyboard_sfx.json","storyboard_bgm.json","audio_asset_settings.json"):
        try:
            result.append((name,version(path/name)))
        except FileNotFoundError:
            result.append((name,None))
    return input_hash(result)


def audio_settings(manager,project_id,kind,identity):
    saved = document(manager.project_path(owner(manager,project_id)),"audio_asset_settings.json").get(kind+":"+identity,{})
    if kind == "SFX" and "duration" not in saved:
        from .sfx_duration import recommended
        return {**recommended(manager,project_id,identity),**saved}
    return saved


def save_audio_settings(manager,project_id,kind,identity,prompt,duration,loop=False):
    import math
    from .locking import project_lock
    if kind not in ("BGM","SFX") or isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not (
            .5 <= duration <= 30 if kind == "SFX" else 3 <= duration <= 600):
        raise ValueError("Choose a valid audio kind and duration.")
    value = {"prompt":prompt.strip(),"duration":duration,"loop":bool(loop) if kind == "SFX" else False}
    for target in dict.fromkeys([owner(manager,project_id),project_id]):
        path = manager.project_path(target)
        with project_lock(path):
            data = document(path,"audio_asset_settings.json")
            data[kind+":"+identity] = value
            atomic_write(path/"audio_asset_settings.json",json.dumps(data,ensure_ascii=False,indent=2))


def save_audio_details(manager,project_id,kind,identity,title,description):
    from .locking import project_lock
    from .storyboard_assets import pin_legacy_asset_catalog
    if not title.strip() or not description.strip():
        raise ValueError("Enter an audio title and description.")
    value = {"title":title.strip(),"description":description.strip()}
    filename = "bgm_library.json" if kind == "BGM" else "sfx_library.json"
    for target in dict.fromkeys([owner(manager,project_id),project_id]):
        ensure_audio(manager,target,kind,identity,value)
        path = manager.project_path(target)
        with project_lock(path):
            pin_legacy_asset_catalog(manager,target)
            data = document(path,filename)
            data["entries"][identity].update(value)
            atomic_write(path/filename,json.dumps(data,ensure_ascii=False,indent=2))


def synchronize(manager,project_id):
    """Adopt shared edits without regenerating or approving narrative content."""
    root = owner(manager,project_id)
    if root == project_id:
        return
    from .audio_assets import binding,publish
    from .storyboard_asset_check import check
    from .locking import project_lock
    from .storyboard_assets import pin_legacy_asset_catalog
    path = manager.project_path(project_id)
    # Capture legacy planning inputs before any catalog is replaced.
    with project_lock(path):
        pin_legacy_asset_catalog(manager,project_id)
    rows = check(manager,project_id)
    for row in rows:
        kind,identity = row["Category"],row["ID"]
        if kind not in ("SFX","BGM"):
            continue
        local,shared = binding(manager,project_id,kind,identity),binding(manager,root,kind,identity)
        # Migrate existing episode-only sounds rather than losing them on refresh.
        if intact(path,local) and not intact(manager.project_path(root),shared):
            share_audio(manager,project_id,kind,identity,expected=shared_audio_hash(manager,project_id,kind,identity))
    from .studio import Episodes
    Episodes(manager,root).refresh_assets(project_id,preserve_inputs=True)
    for row in rows:
        kind,identity = row["Category"],row["ID"]
        if kind not in ("SFX","BGM"):
            continue
        shared = binding(manager,root,kind,identity)
        if not intact(manager.project_path(root),shared):
            continue
        current = binding(manager,project_id,kind,identity)
        if current.get("fileHash") == shared.get("fileHash") and current.get("source") == shared.get("source"):
            continue
        copy_audio(manager,root,project_id,shared,kind,identity)
        publish(manager,project_id,kind,identity,shared.get("name") or row["Asset"],shared["file"],
            provenance={**shared.get("provenance",{}),"sharedWorldProjectId":root},_share=False)
    settings = document(manager.project_path(root),"audio_asset_settings.json")
    if settings:
        atomic_write(path/"audio_asset_settings.json",json.dumps(settings,ensure_ascii=False,indent=2))
    shared_sfx = document(manager.project_path(root),"sfx_library.json").get("entries",{})
    if shared_sfx:
        with project_lock(path):
            local_sfx = document(path,"sfx_library.json").get("entries",{})
            for row in rows:
                if row["Category"] == "SFX" and row["ID"] in shared_sfx:
                    local_sfx[row["ID"]] = shared_sfx[row["ID"]]
            atomic_write(path/"sfx_library.json",json.dumps({"entries":local_sfx},ensure_ascii=False,indent=2))
