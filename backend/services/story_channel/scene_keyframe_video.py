"""Kling-base/Aleph timed-keyframe finishing with durable paid-stage checkpoints."""
import json
import math
from pathlib import Path

from PIL import Image

from .files import atomic_write,input_hash,read_text
from .media_utils import file_hash
from .models import GeneratedAsset,ProviderConfig
from . import scene_keyframes as store


def saved(service,sid):
    from .file_cache import read_json
    path=service.path/"scene_image_keyframes.json"
    return read_json(path).get(sid,{}) if path.is_file() else {}


def fingerprint(service,sid):
    entry=saved(service,sid)
    frames=entry.get("frames",[])
    if len(frames)<=1:
        return None
    values=[]
    for frame in frames:
        if not store.frame_intact(service.assets,frame):
            raise ValueError("A saved keyframe image is missing or modified. Update Multiple scene images before animating.")
        values.append({"id":frame["id"],"timeSec":frame["timeSec"],"fileHash":frame["fileHash"],"file":frame["asset"]["filePath"]})
    return input_hash({"sourceHash":entry.get("sourceHash"),"frames":sorted(values,key=lambda frame:frame["timeSec"]),"method":"kling-aleph-timed-v1"})


def validated(service,sid,options):
    entry=saved(service,sid)
    if len(entry.get("frames",[]))<=1:
        return None
    if options.provider!="runway" or options.generation_mode!="image":
        raise ValueError("Multiple scene images use Runway / Kling O3 Pro followed by Aleph 2.0. Select Runway image-to-video in Video settings.")
    signature=fingerprint(service,sid)
    scene,*_,source_hash=store.context(service.assets,sid)
    if entry.get("sourceHash")!=source_hash:
        raise ValueError("Saved keyframes use outdated storyboard/assets. Reopen Multiple scene images and save a current image set before animating.")
    frames=store.validate_times(entry["frames"],{frame["id"]:frame["timeSec"] for frame in entry["frames"]},scene.duration_sec)
    return {"hash":signature,"frames":frames,"duration":scene.duration_sec}


def resume_base(service,previous,key,force):
    checkpoint=previous.get("multiKeyframeJob",{})
    if force or previous.get("status") not in ("failed","stopped","running") or checkpoint.get("sourceHash")!=key:
        return None
    try:
        asset=GeneratedAsset.model_validate(checkpoint["baseAsset"])
        if file_hash(service.assets.local_path(asset.file_path))==checkpoint["baseHash"]:
            return asset
    except (KeyError,OSError,ValueError):
        pass
    return None


def _read(path):
    return json.loads(read_text(path)) if path.is_file() else None


def finish(service,sid,base,options,keyframes,tools,progress,*,provider=None):
    from .balanced_providers import RunwayVideoEditProvider
    from .library_media import LibraryMedia
    from .provider_prompts import compile_prompt
    from .runway_prompt_limits import require_prompt_budget
    source=service.assets.local_path(base.file_path)
    source_hash=file_hash(source)
    frames=keyframes["frames"]
    duration=float(keyframes["duration"])
    job_hash=input_hash({"base":source_hash,"keyframes":keyframes["hash"],"fps":options.fps,"duration":duration})
    work=source.parent/("aleph-keyframes-"+job_hash[:20])
    work.mkdir(parents=True,exist_ok=True)
    receipt=work/"complete.json"
    completed=_read(receipt)
    if completed:
        try:
            if file_hash(service.assets.local_path(completed["asset"]["filePath"]))==completed["fileHash"]:
                return GeneratedAsset.model_validate(completed["asset"])
        except (KeyError,OSError,ValueError):
            pass
    actual=tools.validate(source)
    if actual+1/options.fps<duration:
        raise ValueError("The base video is shorter than the scene timeline. The Kling base is retained; regenerate a complete base before Aleph.")
    probe=work/"size.png"
    tools.run(["-i",source,"-frames:v","1",probe])
    with Image.open(probe) as image:
        width,height=image.size
    scale=min(1,1920/width,1080/height) if width>=height else min(1,1080/width,1920/height)
    width,height=max(2,int(width*scale)//2*2),max(2,int(height*scale)//2*2)
    fps=min(30,options.fps)
    count=max(1,math.ceil(duration/30))
    provider=provider or RunwayVideoEditProvider(timeout=service.manager.settings.llm_timeout_sec)
    parts=[]
    for index in range(count):
        if service.stop_requested():
            raise InterruptedError("Stopped before the next Aleph keyframe pass. Completed media is retained.")
        start=duration*index/count
        length=duration/count
        requested=max(2.,length)
        folder=work/f"part_{index:03d}"
        folder.mkdir(exist_ok=True)
        chunk_receipt=folder/"complete.json"
        cached=_read(chunk_receipt)
        if cached:
            try:
                if file_hash(service.assets.local_path(cached["file"]))==cached["fileHash"]:
                    parts.append(cached)
                    continue
            except (KeyError,OSError,ValueError):
                pass
        progress(f"Applying timed keyframes (Aleph 2.0) · {index+1}/{count}")
        input_video=folder/"input.mp4"
        tools.run(["-ss",start,"-i",source,"-an","-vf",
            f"trim=duration={length:.9f},setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,fps={fps},tpad=stop_mode=clone:stop_duration={max(0,requested-length)+2/fps:.9f}",
            "-frames:v",math.ceil(requested*fps),
            "-c:v","libx264","-crf","18","-pix_fmt","yuv420p","-movflags","+faststart",input_video])
        guides=[{"file":service.assets.local_path(frame["asset"]["filePath"]),
            "seconds":max(0.,min(length,float(frame["timeSec"])-start)),"id":frame["id"]}
            for frame in frames if start-1e-6<=frame["timeSec"]<=start+length+1e-6]
        if index and len(guides)<5 and not any(guide["seconds"]<1e-6 for guide in guides):
            anchor=folder/"continuity.png"
            tools.run(["-sseof","-1","-i",service.assets.local_path(parts[-1]["file"]),"-vf","reverse","-frames:v","1",anchor])
            guides.insert(0,{"file":anchor,"seconds":0.,"id":"previous-segment-final-frame"})
        direction=" ".join(shot.action+" Camera: "+shot.camera for shot in service.direction(sid).shots)
        timeline="; ".join(f"{guide['seconds']:g}s: {guide['id']}" for guide in guides)
        prompt=compile_prompt(service.assets.creative,
            "Refine this base animation to follow the supplied timed keyframe images at their assigned times. "
            "Maintain smooth natural motion between them. Preserve character identity, costumes, prop appearance, "
            "room geometry, lighting and rendering style. Do not introduce cuts or a slideshow. "
            "Keep the source timeline and duration unchanged. Direction: "+direction+" Keyframes: "+timeline,
            video=True,task_key=f"animation:visual_keyframes:{sid}:prompt")
        require_prompt_budget(prompt)
        output=folder/"generated.mp4"
        config=ProviderConfig(provider="runway",model="aleph2",options={"output_path":str(output),"duration":requested,
            "keyframes":[{"file":str(guide["file"]),"seconds":guide["seconds"]} for guide in guides]})
        atomic_write(folder/"request.json",json.dumps({"sceneId":sid,"start":start,"duration":length,
            "inputDuration":requested,"keyframes":[{**guide,"file":str(guide["file"])} for guide in guides],"prompt":prompt},ensure_ascii=False,indent=2))
        task=f"animation:visual_keyframes:{sid}:{index}"
        try:
            # Running/completed task IDs are retrieved again without submission.
            # A terminal provider failure needs a new job; preserve its receipt
            # for diagnosis while keeping the paid base and successful segments.
            remote=output.with_suffix(output.suffix+".task.json")
            previous=_read(remote)
            if previous and previous.get("status") in ("FAILED","CANCELED","CANCELLED"):
                remote.replace(folder/("failed-task-"+input_hash(previous)[:20]+".json"))
            asset=LibraryMedia(service.manager,service.project_id)._call(provider,provider.edit_clip,(prompt,input_video),config,task)
            if Path(asset.file_path).resolve()!=output.resolve():
                raise ValueError("Aleph returned an invalid output path.")
            if abs(tools.validate(output)-requested)>max(.15,2/fps):
                raise ValueError("Aleph changed the clip duration. Its result and the base are retained; keyframe timing was not stretched.")
            normalized=work/f"part_{index:03d}.mp4"
            # Frame counts use shared global boundaries, avoiding one lost
            # frame per segment and cumulative duration drift on long scenes.
            frame_count=max(1,round((start+length)*options.fps)-round(start*options.fps))
            tools.run(["-i",output,"-an","-vf",f"scale={width}:{height},setsar=1,fps={options.fps},tpad=stop_mode=clone:stop_duration={2/options.fps:.9f}",
                "-frames:v",frame_count,
                "-c:v","libx264","-crf","0","-pix_fmt","yuv420p",normalized])
            asset.file_path=output.relative_to(service.path).as_posix()
            item={"file":normalized.relative_to(service.path).as_posix(),"fileHash":file_hash(normalized),
                "start":start,"duration":length,"requestDuration":requested,"asset":asset.model_dump(mode="json",by_alias=True)}
            atomic_write(chunk_receipt,json.dumps(item,ensure_ascii=False,indent=2))
            parts.append(item)
            from .error_reports import clear_reports
            clear_reports(service.path,task)
        except Exception as exc:
            from .error_reports import record_failure,matching_reports
            if not matching_reports(service.path,str(exc),task):
                record_failure(service.path,task,exc)
            exc.error_report_project_path=str(service.path)
            exc.error_report_task=task
            raise
    if service.stop_requested():
        raise InterruptedError("Stopped before assembling Aleph results. Completed parts are retained.")
    listing=work/"parts.txt"
    atomic_write(listing,"\n".join("file '"+Path(item["file"]).name+"'" for item in parts)+"\n")
    result=work/"visual.mp4"
    tools.run(["-f","concat","-safe","1","-i",listing,"-an","-c:v","copy","-t",duration,"-movflags","+faststart",result],cwd=work)
    actual=tools.validate(result)
    if abs(actual-duration)>max(.15,2/options.fps):
        raise ValueError("Assembled keyframe video has an unexpected duration. Paid source files are retained.")
    final=GeneratedAsset(type="video",provider="runway",model="aleph2",source_scene_id=sid,
        file_path=result.relative_to(service.path).as_posix(),duration=actual,prompt_hash=job_hash,
        generation_params={"method":"kling-base-aleph-timed-keyframes","multiKeyframesHash":keyframes["hash"],
            "keyframes":[{"id":frame["id"],"seconds":frame["timeSec"],"file":frame["asset"]["filePath"],"fileHash":frame["fileHash"]} for frame in frames],
            "baseAnimation":base.model_dump(mode="json",by_alias=True),"parts":parts})
    atomic_write(receipt,json.dumps({"asset":final.model_dump(mode="json",by_alias=True),"fileHash":file_hash(result)},ensure_ascii=False,indent=2))
    return final
