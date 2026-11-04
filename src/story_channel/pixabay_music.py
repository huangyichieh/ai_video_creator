"""AI choice from imported Pixabay tracks with retained license evidence.

Pixabay's public API has no music search. Do not invent track URLs or treat
an unverified search result as cleared for monetized production.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .models import Model
from .files import atomic_write,input_hash,atomic_copy
from .media_utils import write_bytes,file_hash
from .storyboard_asset_check import document,intact,bind_sound

LICENSE_URL = "https://pixabay.com/service/license-summary/"


def track_id(url):
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in ("pixabay.com","www.pixabay.com") or parsed.username or parsed.port:
        raise ValueError("Use the official HTTPS Pixabay music track page URL.")
    match = re.fullmatch(r"/music/[a-zA-Z0-9_-]+-(\d+)/?",parsed.path)
    if not match:
        raise ValueError("Use an individual Pixabay music track URL, not a search page.")
    return match[1]


def catalog(manager,owner):
    return document(manager.project_path(owner),"pixabay_music.json").get("tracks",{})


def eligible(manager,owner):
    path = manager.project_path(owner)
    result = {}
    for identity,item in catalog(manager,owner).items():
        try:
            valid = track_id(item["url"]) == identity
        except (ValueError,KeyError):
            continue
        if (valid and item.get("commercialUseConfirmed") is True and item.get("contentIdStatus") == "not_registered"
                and item.get("licenseUrl") == LICENSE_URL and intact(path,item)
                and intact(path,{"file":item.get("licenseEvidence"),"fileHash":item.get("licenseEvidenceHash")})):
            result[identity] = item
    return result


def register(manager,owner,title,author,url,data,filename,evidence,evidence_name,*,commercial_confirmed=False,content_id_status="unknown",description=""):
    if not title.strip() or not author.strip() or not commercial_confirmed or content_id_status != "not_registered":
        raise ValueError("Record the title/author, verify free commercial use, and confirm the track is not registered with Content ID.")
    identity = track_id(url)
    suffix = Path(evidence_name).suffix.lower()
    if not evidence or len(evidence) > 5*1024*1024 or suffix not in (".pdf",".txt",".png",".jpg",".jpeg"):
        raise ValueError("Attach license/download evidence (PDF, TXT or image, up to 5 MB).")
    from .rendering import RenderPipeline
    source = f"Pixabay Music: {title.strip()} by {author.strip()}; {url.strip()}; {LICENSE_URL}. Embedded video use; license evidence retained. Content ID checked by uploader."
    relative = RenderPipeline(manager,owner).import_audio(data,filename,source)
    path = manager.project_path(owner)
    from .locking import project_lock
    from .models import utc_now
    import hashlib
    digest = hashlib.sha256(evidence).hexdigest()
    receipt = f"licenses/pixabay/{identity}/{digest}{suffix}"
    with project_lock(path):
        tracks = catalog(manager,owner)
        if identity in tracks:
            atomic_write(path/"history"/"pixabay"/identity/(input_hash(tracks[identity])+".json"),json.dumps(tracks[identity],ensure_ascii=False,indent=2))
        write_bytes(path/receipt,evidence)
        tracks[identity] = {"id":identity,"title":title.strip(),"author":author.strip(),"description":description.strip(),"url":url.strip(),"licenseUrl":LICENSE_URL,
            "commercialUseConfirmed":True,"contentIdStatus":"not_registered","checkedAt":utc_now().isoformat(),
            "file":relative,"fileHash":file_hash(path/relative),"source":source,"licenseEvidence":receipt,"licenseEvidenceHash":digest}
        atomic_write(path/"pixabay_music.json",json.dumps({"tracks":tracks},ensure_ascii=False,indent=2))
        audio_receipt = document(path,relative+".source.json")
        audio_receipt.update(kind="bgm",pixabayTrackId=identity)
        atomic_write(path/(relative+".source.json"),json.dumps(audio_receipt,ensure_ascii=False,indent=2))
    return identity


class Selection(Model):
    track_id: str = ""
    reason: str = ""


def choose(manager,project_id,music_id,*,provider=None):
    from .bgm_library import mood_id
    from .audio_assets import requirement as audio_requirement
    requirement = audio_requirement(manager,project_id,"BGM",music_id)
    if not requirement:
        raise ValueError("This BGM requirement is no longer used by the storyboard.")
    owner = manager.load(project_id).world_project_id or project_id
    tracks = eligible(manager,owner)
    if not tracks:
        raise ValueError("Import Pixabay tracks with license evidence and confirmed non-Content-ID status first. No eligible tracks are available.")
    path = manager.project_path(project_id)
    expected = input_hash(document(path,"storyboard_bgm.json").get("entries",{}).get(music_id))
    from .pipeline import Pipeline
    pipeline = Pipeline(manager,project_id)
    manifest = manager.load(project_id)
    scenes = [scene.model_dump(mode="json",by_alias=True) for scene in pipeline.artifact("storyboard").scenes
        if scene.bgm_id == music_id or not scene.bgm_id and mood_id(scene.bgm_mood) == music_id] if (manifest.world_project_id or manifest.workflow_version != "world_episode") and (path/"scenes.json").exists() else []
    from .modification import modify_content
    def validate(result):
        if result.track_id not in tracks:
            raise ValueError("AI did not select an eligible imported Pixabay track. No music was applied.")
        return result
    selected = modify_content(pipeline,Selection(),
        "Choose the most suitable instrumental background music for these scenes from the eligible Pixabay candidates. "
        "Return exactly one candidate track_id and a short musical reason. Do not invent a track or URL. "
        "License checks are application-owned; do not claim guaranteed copyright or Content ID safety.",
        "metadata",provider=provider,allow_new_ids=True,creation=True,task_key="pixabay_choice:"+music_id,
        context={"requirement":requirement.get("Description",requirement["Asset"]),"scenes":scenes,"candidates":[{"id":key,"title":value["title"],"author":value["author"],"description":value.get("description","")} for key,value in tracks.items()]},validate_output=validate)
    use_track(manager,project_id,music_id,selected.track_id,reason=selected.reason,expected=expected,expected_track=tracks[selected.track_id])
    return selected


def use_track(manager,project_id,music_id,track_id,*,reason="Manual library selection",expected=None,expected_track=None):
    from .audio_assets import requirement as audio_requirement
    requirement = audio_requirement(manager,project_id,"BGM",music_id)
    if not requirement:
        raise ValueError("This BGM draft or requirement no longer exists.")
    owner = manager.load(project_id).world_project_id or project_id
    path = manager.project_path(project_id)
    from .check_asset_sync import shared_audio_hash
    shared_expected = shared_audio_hash(manager,project_id,"BGM",music_id)
    tracks = eligible(manager,owner)
    if expected is None:
        expected = input_hash(document(path,"storyboard_bgm.json").get("entries",{}).get(music_id))
    item = tracks.get(track_id)
    if item is None:
        raise ValueError("Choose an eligible Pixabay library track.")
    if expected_track is not None and input_hash(item) != input_hash(expected_track):
        raise ValueError("The selected track changed. Choose music again.")
    current = eligible(manager,owner).get(track_id)
    if current is None or input_hash(current) != input_hash(item):
        raise ValueError("The selected track or license evidence changed. Choose music again.")
    from .rendering import RenderPipeline
    root = manager.project_path(owner)
    relative = RenderPipeline(manager,project_id).import_audio((root/item["file"]).read_bytes(),Path(item["file"]).name,item["source"])
    if root.resolve() != path.resolve():
        atomic_copy(root/item["licenseEvidence"],path/item["licenseEvidence"])
    if file_hash(path/item["licenseEvidence"]) != item["licenseEvidenceHash"]:
        raise ValueError("The track's license evidence changed during import. No music was applied.")
    from .audio_assets import publish
    publish(manager,project_id,"BGM",music_id,requirement["Asset"],relative,expected=expected,shared_expected=shared_expected,
        provenance={"provider":"pixabay","track":item,"aiReason":reason})
    return item

