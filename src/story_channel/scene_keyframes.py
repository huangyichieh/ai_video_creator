"""Scene-specific timed image sets; dialog drafts never replace the primary still."""
from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .files import atomic_write,input_hash,read_text
from .locking import project_lock
from .ledger_lock import ledger_lock
from .media_utils import file_hash
from .models import Identifier,utc_now
from pydantic import TypeAdapter

MAX_IMAGES=5


def read(path,default):
    return json.loads(read_text(path)) if path.is_file() else deepcopy(default)


def draft_file(engine,token):
    TypeAdapter(Identifier).validate_python(token)
    return engine.local_path("scene_keyframe_drafts/"+token+"/draft.json")


def frame_intact(engine,frame):
    try:
        return bool(frame.get("asset") and frame.get("fileHash") and
            file_hash(engine.local_path(frame["asset"]["filePath"]))==frame["fileHash"])
    except (OSError,ValueError,KeyError):
        return False


def context(engine,sid):
    scene=next((item for item in engine.creative.artifact("storyboard").scenes if item.scene_id==sid),None)
    if scene is None:
        raise ValueError("This storyboard scene no longer exists.")
    state=engine.load()
    task=next(item for item in engine.tasks(state) if item.stage=="scene_images" and item.subject==sid)
    config=engine._config(task,state)
    record=state.records.get(task.key)
    if not record or not record.asset or not engine._intact(record):
        raise ValueError("Generate an intact primary scene image first.")
    fingerprint=input_hash({"scene":scene.model_dump(mode="json",by_alias=True),
        "primary":record.asset.asset_id,"primaryHash":record.file_hash,
        "references":config.options.get("sceneAssetReferences"),"prompt":task.prompt,
        "config":config.model_dump(mode="json",by_alias=True),
        "style":state.project_style_rules,"styleImage":state.project_style_image})
    return scene,state,task,config,record,fingerprint


def start(engine,sid,token):
    with project_lock(engine.path),ledger_lock(engine.path):
        scene,state,task,config,record,fingerprint=context(engine,sid)
        saved=read(engine.path/"scene_image_keyframes.json",{}).get(sid)
        primary={"id":record.asset.asset_id,"status":"ready","timeSec":0.,"prompt":"Primary scene image",
            "asset":record.asset.model_dump(mode="json",by_alias=True),"fileHash":record.file_hash}
        frames=deepcopy(saved["frames"]) if saved and saved.get("sourceHash")==fingerprint else [primary]
        data={"id":token,"sceneId":sid,"sourceHash":fingerprint,"savedHash":input_hash(saved),
            "duration":scene.duration_sec,"frames":frames,"candidates":[],"createdAt":utc_now().isoformat(),"closed":False}
        atomic_write(draft_file(engine,token),json.dumps(data,ensure_ascii=False,indent=2))
        return data


def load(engine,token):
    return read(draft_file(engine,token),{})


def validate_times(frames,times,duration):
    if not 1<=len(frames)<=MAX_IMAGES:
        raise ValueError("Keep between 1 and 5 scene images in the list.")
    if set(times)!={frame["id"] for frame in frames}:
        raise ValueError("Set a keyframe time for every scene image.")
    result=[]
    seen=set()
    for frame in frames:
        value=times[frame["id"]]
        try:
            seconds=float(value)
        except (TypeError,ValueError):
            raise ValueError("Keyframe times must be valid numbers.") from None
        if isinstance(value,bool) or not math.isfinite(seconds) or not 0<=seconds<=duration:
            raise ValueError(f"Keyframe times must be between 0 and {duration:g} seconds.")
        if round(seconds,6) in seen:
            raise ValueError("Each scene image needs a different keyframe time.")
        seen.add(round(seconds,6))
        result.append({**deepcopy(frame),"timeSec":seconds})
    return sorted(result,key=lambda frame:frame["timeSec"])


def generate(engine,token,identity,reference_id,prompt,*,provider=None,preview=False):
    TypeAdapter(Identifier).validate_python(identity)
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError("Describe the new scene image.")
    task_key="assets:scene_keyframes:"+identity
    try:
        with project_lock(engine.path),ledger_lock(engine.path):
            draft=load(engine,token)
            if not draft or draft.get("closed"):
                raise ValueError("This scene-image dialog has been closed.")
            scene,state,task,config,record,fingerprint=context(engine,draft["sceneId"])
            if fingerprint!=draft["sourceHash"]:
                raise ValueError("The storyboard, primary image or asset references changed. Reopen Multiple scene images.")
            blocker=engine.generation_blocker("scene_images",state)
            if blocker:
                raise ValueError(blocker)
            reference=next((item for item in draft["frames"] if item["id"]==reference_id and item["status"]=="ready"),None)
            if not reference or not frame_intact(engine,reference):
                raise ValueError("Choose an intact image from this scene's image list as reference.")
            if any(item["id"]==identity for item in draft["frames"]+draft.get("candidates",[])):
                raise ValueError("This image request already exists. Add a new image to retry.")
            if not preview and len(draft["frames"])>=MAX_IMAGES:
                raise ValueError("A scene can contain at most 5 images. Remove an image before adding another.")
            row={"id":identity,"status":"generating","phase":"preparing","startedAt":utc_now().isoformat(),
                "referenceId":reference_id,"prompt":prompt.strip(),"timeSec":None}
            collection="candidates" if preview else "frames"
            draft.setdefault(collection,[]).append(row)
            atomic_write(draft_file(engine,token),json.dumps(draft,ensure_ascii=False,indent=2))
            from .scene_keyframe_request import prepare
            instructions,references=prepare(engine,state,task,config,reference,prompt)
            output=draft_file(engine,token).parent/(identity+".png")
            config.options["output_path"]=str(output)
        if engine.stop_requested():
            raise InterruptedError("Stopped before generating the additional scene image.")
        from .library_media import LibraryMedia
        provider=provider or engine.scene_image_provider
        with ledger_lock(engine.path):
            progress=load(engine,token)
            entry=next(item for item in progress[collection] if item["id"]==identity)
            entry.update(phase="requesting",requestStartedAt=utc_now().isoformat(),
                provider=config.provider,model=config.model,referenceCount=len(references))
            atomic_write(draft_file(engine,token),json.dumps(progress,ensure_ascii=False,indent=2))
        asset=LibraryMedia(engine.manager,engine.project_id)._call(provider,provider.generate,(instructions,references),config,task_key)
        with ledger_lock(engine.path):
            progress=load(engine,token)
            entry=next(item for item in progress[collection] if item["id"]==identity)
            entry.update(phase="saving",responseReceivedAt=utc_now().isoformat())
            atomic_write(draft_file(engine,token),json.dumps(progress,ensure_ascii=False,indent=2))
        actual=Path(asset.file_path).resolve()
        if asset.type!="image" or actual!=output.resolve() or not actual.is_file():
            raise ValueError("The image provider returned an invalid output path.")
        with Image.open(actual) as image:
            image.verify()
        asset.file_path=output.relative_to(engine.path).as_posix()
        asset.source_scene_id=draft["sceneId"]
        asset.generation_params.update(referenceId=reference_id,keyframePrompt=prompt.strip(),sourceHash=fingerprint,
            keyframeEdit=config.options["keyframe_edit"],referenceManifest=config.options["scene_reference_roles"],submittedPrompt=instructions)
        with project_lock(engine.path),ledger_lock(engine.path):
            latest=load(engine,token)
            if context(engine,draft["sceneId"])[-1]!=fingerprint:
                raise ValueError("Scene inputs changed during generation. The image is retained but cannot be applied; reopen the dialog.")
            entry=next(item for item in latest[collection] if item["id"]==identity)
            entry.update(status="ready",phase="completed",completedAt=utc_now().isoformat(),
                asset=asset.model_dump(mode="json",by_alias=True),fileHash=file_hash(output))
            atomic_write(draft_file(engine,token),json.dumps(latest,ensure_ascii=False,indent=2))
        from .error_reports import clear_reports
        clear_reports(engine.path,task_key)
        return entry
    except Exception as exc:
        with ledger_lock(engine.path):
            latest=load(engine,token)
            for item in latest.get("frames",[])+latest.get("candidates",[]):
                if item["id"]==identity:
                    item.update(status="failed",phase="failed",completedAt=utc_now().isoformat(),error=str(exc))
            if latest:
                atomic_write(draft_file(engine,token),json.dumps(latest,ensure_ascii=False,indent=2))
        from .error_reports import record_failure,matching_reports
        if not matching_reports(engine.path,str(exc),task_key):
            record_failure(engine.path,task_key,exc)
        exc.error_report_project_path=str(engine.path)
        exc.error_report_task=task_key
        raise


def add_generated(engine,token,identity):
    with project_lock(engine.path),ledger_lock(engine.path):
        draft=load(engine,token)
        if not draft or draft.get("closed"):
            raise ValueError("Reopen Multiple scene images before adding an image.")
        if context(engine,draft["sceneId"])[-1]!=draft["sourceHash"]:
            raise ValueError("Scene inputs changed. Reopen Multiple scene images.")
        if len(draft["frames"])>=MAX_IMAGES:
            raise ValueError("A scene can contain at most 5 images. Remove an image before adding another.")
        frame=next((frame for frame in draft.get("candidates",[]) if frame["id"]==identity),None)
        if not frame or frame["status"]!="ready" or not frame_intact(engine,frame):
            raise ValueError("Generate an image successfully before adding it to the list.")
        draft["frames"].append(deepcopy(frame))
        draft["candidates"]=[item for item in draft["candidates"] if item["id"]!=identity]
        atomic_write(draft_file(engine,token),json.dumps(draft,ensure_ascii=False,indent=2))
        return frame


def remove_image(engine,token,identity):
    with ledger_lock(engine.path):
        draft=load(engine,token)
        if not draft or draft.get("closed"):
            raise ValueError("Reopen Multiple scene images before removing an image.")
        if len(draft["frames"])<=1:
            raise ValueError("Keep at least one scene image to use as a reference.")
        if not any(frame["id"]==identity for frame in draft["frames"]):
            raise ValueError("This image is no longer in the list.")
        draft["frames"]=[frame for frame in draft["frames"] if frame["id"]!=identity]
        atomic_write(draft_file(engine,token),json.dumps(draft,ensure_ascii=False,indent=2))


def confirm(engine,token,times,*,keep_open=False):
    with project_lock(engine.path),ledger_lock(engine.path):
        draft=load(engine,token)
        if not draft or draft.get("closed"):
            raise ValueError("Reopen Multiple scene images before saving.")
        if context(engine,draft["sceneId"])[-1]!=draft["sourceHash"]:
            raise ValueError("Scene inputs changed. Reopen Multiple scene images.")
        if not draft["frames"] or any(frame["status"]!="ready" or not frame_intact(engine,frame) for frame in draft["frames"]):
            raise ValueError("Finish generating all images, or remove failed images, before saving.")
        frames=validate_times(draft["frames"],times,draft["duration"])
        saved=read(engine.path/"scene_image_keyframes.json",{})
        if input_hash(saved.get(draft["sceneId"]))!=draft["savedHash"]:
            raise ValueError("Another session changed this scene's image set. Reopen the dialog.")
        saved[draft["sceneId"]]={"sceneId":draft["sceneId"],"sourceHash":draft["sourceHash"],"frames":frames,"updatedAt":utc_now().isoformat()}
        atomic_write(engine.path/"scene_image_keyframes.json",json.dumps(saved,ensure_ascii=False,indent=2))
        draft["closed"]=not keep_open
        draft["frames"]=deepcopy(frames)
        draft["savedHash"]=input_hash(saved[draft["sceneId"]])
        atomic_write(draft_file(engine,token),json.dumps(draft,ensure_ascii=False,indent=2))
        return saved[draft["sceneId"]]
