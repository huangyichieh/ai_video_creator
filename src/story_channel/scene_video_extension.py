"""Append a prompted last-frame continuation without replacing existing footage."""
from copy import deepcopy
import json
import math
from pathlib import Path

from PIL import Image

from .files import atomic_write,input_hash
from .locking import project_lock
from .media_utils import file_hash
from .models import GeneratedAsset,ProviderConfig,ApprovalRecord
from .scene_visual_revisions import archive,intact,next_revision
from .scene_video_region import ffmpeg

MODEL="klingO3_pro"


def extension_seconds(value):
    try:
        seconds=float(value)
    except (TypeError,ValueError):
        raise ValueError("Enter an extension duration from 0.1 to 15 seconds.") from None
    if isinstance(value,bool) or not math.isfinite(seconds) or not .1<=seconds<=15:
        raise ValueError("Enter an extension duration from 0.1 to 15 seconds.")
    return seconds


def extend(service,sid,options,*,expected,seconds,prompt,expected_input_hash=None,provider=None):
    seconds=extension_seconds(seconds)
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError("Enter a prompt describing what happens during the extension.")
    task="animation:visual_extend:"+sid
    with project_lock(service.path):
        values=service.visuals()
        previous=values.get(sid,{})
        source_hash=service.visual_hash(sid,options)
        if input_hash(previous)!=expected or expected_input_hash is not None and source_hash!=expected_input_hash:
            raise ValueError("The scene video or direction changed. Reopen Extend on the current revision.")
        from .animation_formats import delivery_formats,create_portrait,LANDSCAPE,PORTRAIT
        formats=delivery_formats(options)
        if not intact(service,previous,formats):
            raise ValueError("Use an intact revision containing the current delivery formats first.")
        tools=ffmpeg(service)
        source=service.assets.local_path(previous["asset"]["filePath"])
        duration=tools.validate(source)
        if not math.isfinite(duration) or duration<=0:
            raise ValueError("The source video has no usable duration.")
        token=input_hash({"source":previous["fileHash"],"asset":previous["asset"]["assetId"],
            "seconds":seconds,"prompt":prompt.strip(),"model":MODEL,"version":"last-frame-append-v1"})
        work=service.assets.local_path("videos/visual_extensions/"+sid+"/"+token)
        work.mkdir(parents=True,exist_ok=True)
        archive(service,sid,previous)
        try:
            if service.stop_requested():
                raise InterruptedError("Stopped before extending. The original scene is retained.")
            seed=work/"last_frame.png"
            tools.run(["-sseof","-1","-i",source,"-vf","reverse","-frames:v","1",seed])
            with Image.open(seed) as image:
                width,height=image.size
                factor=max(1,300/width,300/height)
                image.convert("RGB").resize((math.ceil(width*factor),math.ceil(height*factor))).save(work/"reference.png")
            from .provider_prompts import compile_prompt
            instructions=("Continue this existing animated scene immediately AFTER its supplied final frame. "
                "The image is the exact final pose, not a new scene. Preserve character identity, clothing, "
                "object positions, background, lighting and art style. Continue the motion naturally with no "
                "cut, replay, fade or opening title. Show the requested continuation immediately from the beginning. "
                f"The first {seconds:g} seconds will be appended to the original clip. Request: "+prompt.strip())
            submitted=compile_prompt(service.assets.creative,instructions,video=True,task_key=task+":prompt")
            from .runway_prompt_limits import require_prompt_budget
            require_prompt_budget(submitted)
            request_seconds=max(3,math.ceil(seconds))
            params={"sourceAssetId":previous["asset"]["assetId"],"sourceFile":previous["asset"]["filePath"],
                "sourceFileHash":previous["fileHash"],"originalDuration":duration,"extensionSeconds":seconds,
                "requestedProviderSeconds":request_seconds,"extensionPrompt":prompt.strip(),"submittedPrompt":submitted,
                "firstFrame":seed.relative_to(service.path).as_posix(),"deliveryFormats":formats}
            atomic_write(work/"extension_request.json",json.dumps(params,ensure_ascii=False,indent=2))
            from .balanced_providers import KlingVideoProvider
            from .library_media import LibraryMedia
            provider=provider or KlingVideoProvider(timeout=service.manager.settings.llm_timeout_sec)
            generated=work/"generated.mp4"
            config=ProviderConfig(provider="runway",model=MODEL,options={"output_path":str(generated),
                "seconds":request_seconds,"duration":request_seconds,"size":"720x1280" if height>width else "1280x720"})
            candidate=LibraryMedia(service.manager,service.project_id)._call(provider,provider.generate_clip,
                (submitted,[work/"reference.png"]),config,task)
            if Path(candidate.file_path).resolve()!=generated.resolve() or not generated.is_file():
                raise ValueError("The extension provider returned an invalid output path.")
            generated_duration=tools.validate(generated)
            if generated_duration+1/options.fps<seconds:
                raise ValueError("The generated continuation is shorter than requested. The original was retained.")
            if service.stop_requested():
                raise InterruptedError("Stopped after generating the continuation. The original scene is retained.")
            candidate.file_path=generated.relative_to(service.path).as_posix()
            params["continuationAsset"]=candidate.model_dump(mode="json",by_alias=True)
            output=work/"visual.mp4"
            # Keep the original sequence at its original speed; trim only the new tail.
            tools.run(["-i",source,"-i",generated,"-filter_complex_threads","1","-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS,setsar=1[base];"
                f"[1:v]scale={width}:{height},fps={options.fps},setsar=1,trim=duration={seconds:.9f},setpts=PTS-STARTPTS[tail];"
                "[base][tail]concat=n=2:v=1:a=0,format=yuv420p[v]",
                "-map","[v]","-map","0:a?","-af","apad","-t",duration+seconds,
                "-c:v","libx264","-crf","0","-pix_fmt","yuv420p","-c:a","aac","-movflags","+faststart",output])
            actual=tools.validate(output)
            if abs(actual-duration-seconds)>max(.15,2/options.fps):
                raise ValueError("The extended scene has an unexpected duration. The original was retained.")
            asset=GeneratedAsset(type="video",provider="runway",model=MODEL,source_scene_id=sid,
                file_path=output.relative_to(service.path).as_posix(),duration=actual,prompt_hash=input_hash(submitted),
                generation_params=params)
            record=deepcopy(previous)
            record.update(status="succeeded",revision=next_revision(service,sid,previous),
                approval=ApprovalRecord(content_hash=source_hash).model_dump(mode="json",by_alias=True),error="",
                attempts=previous.get("attempts",0)+1,sourceHash=source_hash,generationSourceHash=source_hash,
                asset=asset.model_dump(mode="json",by_alias=True),fileHash=file_hash(output))
            record.pop("appliedAt",None)
            record.pop("appliedFromAssetId",None)
            record["formats"]={formats[0]:{"status":"succeeded","asset":record["asset"],"fileHash":record["fileHash"]}}
            if formats==[LANDSCAPE,PORTRAIT]:
                portrait=work/"portrait.mp4"
                pseconds=create_portrait(tools,output,portrait,options)
                pclip=GeneratedAsset(type="video",provider="local",model="portrait-center-crop",
                    file_path=portrait.relative_to(service.path).as_posix(),duration=pseconds,prompt_hash=asset.prompt_hash,
                    generation_params={"sourceAssetId":asset.asset_id,"deliveryFormat":PORTRAIT})
                record["formats"][PORTRAIT]={"status":"succeeded","asset":pclip.model_dump(mode="json",by_alias=True),"fileHash":file_hash(portrait)}
            latest=service.visuals()
            if input_hash(latest.get(sid,{}))!=expected or service.visual_hash(sid,options)!=source_hash:
                raise ValueError("Scene inputs changed during extension. The candidate was saved but not applied.")
            atomic_write(work/"extension_result.json",json.dumps(record,ensure_ascii=False,indent=2))
            archive(service,sid,record)
            latest[sid]=record
            atomic_write(service.path/"visual_scenes.json",json.dumps(latest,ensure_ascii=False,indent=2))
            from .error_reports import clear_reports
            clear_reports(service.path,task)
            return record
        except InterruptedError:
            raise
        except Exception as exc:
            from .error_reports import record_failure,matching_reports
            if not matching_reports(service.path,str(exc),task):
                record_failure(service.path,task,exc)
            exc.error_report_project_path=str(service.path)
            exc.error_report_task=task
            raise
