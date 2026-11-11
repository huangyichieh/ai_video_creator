"""Edit a fixed video rectangle and composite it over the protected source."""
import json
import math
import re
from copy import deepcopy
from pathlib import Path

from PIL import Image

from .files import atomic_write,input_hash
from .locking import project_lock
from .media_utils import file_hash
from .models import GeneratedAsset,ProviderConfig,ApprovalRecord
from .scene_visual_revisions import archive,intact,next_revision

EDIT_MODEL = "aleph2"


def ffmpeg(service):
    from .ffmpeg_renderer import FFmpeg
    return FFmpeg(service.manager.settings.ffmpeg_path,service.manager.settings.render_timeout_sec)


def preview(service,sid,record):
    if not intact(service,record):
        raise ValueError("This scene video is missing or modified. Generate or apply an intact revision first.")
    source = service.assets.local_path(record["asset"]["filePath"])
    folder = service.assets.local_path("videos/visual_previews/"+sid+"/"+record["fileHash"][:20])
    folder.mkdir(parents=True,exist_ok=True)
    poster = folder/"frame.png"
    if not poster.is_file():
        ffmpeg(service).run(["-i",source,"-frames:v","1",poster])
    with Image.open(poster) as image:
        size = image.size
    return source,poster,size


def playback_preview(service,sid,record):
    """Cache a seekable browser proxy; edits still use the full-resolution source."""
    source,poster,size=preview(service,sid,record)
    destination=poster.parent/"playback_720p_v1.mp4"
    if not destination.is_file():
        from uuid import uuid4
        import os
        temporary=destination.with_name("playback_"+uuid4().hex+".mp4")
        try:
            ffmpeg(service).run(["-i",source,"-vf",
                "scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24",
                "-an","-c:v","libx264","-preset","veryfast","-crf","26",
                "-profile:v","main","-pix_fmt","yuv420p","-g","24","-keyint_min","24",
                "-movflags","+faststart",temporary])
            os.replace(temporary,destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination,size,ffmpeg(service).validate(source)


def region(selection,size):
    from .detail_zoom import rectangle
    left,top,right,bottom = rectangle(selection,size)
    # YUV420's 2x2 chroma blocks must remain aligned, including the overlay.
    left,top = left//2*2,top//2*2
    right,bottom = min(size[0]//2*2,math.ceil(right/2)*2),min(size[1]//2*2,math.ceil(bottom/2)*2)
    if right-left<8 or bottom-top<8:
        raise ValueError("Select a region at least 8 pixels wide and high.")
    return left,top,right,bottom


def timecode(value):
    """Parse clip-relative HH:MM:SS (optional fractional seconds)."""
    if not isinstance(value,str) or not re.fullmatch(r"\d{2,}:\d{2}:\d{2}(?:\.\d{1,3})?",value.strip()):
        raise ValueError("Use HH:MM:SS or HH:MM:SS.sss for the edit range.")
    hours,minutes,seconds=value.strip().split(":")
    if int(minutes)>=60 or float(seconds)>=60:
        raise ValueError("Minutes and seconds must be less than 60.")
    return int(hours)*3600+int(minutes)*60+float(seconds)


def format_timecode(seconds):
    milliseconds=round(seconds*1000)
    hours,remainder=divmod(milliseconds,3600000)
    minutes,remainder=divmod(remainder,60000)
    seconds,millis=divmod(remainder,1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"+(f".{millis:03d}" if millis else "")


def edit_interval(start,end,duration):
    try:
        start=0. if start is None else float(start)
        end=duration if end is None else float(end)
    except (TypeError,ValueError):
        raise ValueError("Choose a valid edit time range.") from None
    if not all(math.isfinite(v) for v in (start,end)) or not 0<=start<end<=duration:
        raise ValueError("Edit start must precede end, within the scene video duration.")
    return start,end


def modify(service,sid,options,*,expected,selection,prompt,provider=None,expected_input_hash=None,start_time=None,end_time=None):
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError("Enter what should replace the selected area.")
    with project_lock(service.path):
        values = service.visuals()
        previous = values.get(sid,{})
        if input_hash(previous) != expected:
            raise ValueError("The scene video changed. Reopen Modify on the current revision.")
        if expected_input_hash is not None and service.visual_hash(sid,options) != expected_input_hash:
            raise ValueError("Scene direction or video settings changed. Reopen Modify before generating.")
        from .animation_formats import delivery_formats,create_portrait,LANDSCAPE,PORTRAIT
        formats = delivery_formats(options)
        if not intact(service,previous,formats):
            raise ValueError("Use an intact revision containing the current delivery formats first.")
        source,poster,size = preview(service,sid,previous)
        box = region(selection,size)
        tools = ffmpeg(service)
        duration = tools.validate(source)
        if not math.isfinite(duration) or duration<=0:
            raise ValueError("The source video has no usable duration.")
        edit_start,edit_end=edit_interval(start_time,end_time,duration)
        edit_duration=edit_end-edit_start
        source_hash = service.visual_hash(sid,options)
        token = input_hash({"source":previous["fileHash"],"asset":previous["asset"]["assetId"],
                            "rectangle":box,"prompt":prompt.strip(),"model":EDIT_MODEL,
                            "startTime":edit_start,"endTime":edit_end,"preparation":"timed-region-v2"})
        work = service.assets.local_path("videos/visual_edits/"+sid+"/"+token)
        work.mkdir(parents=True,exist_ok=True)
        archive(service,sid,previous)
        from .balanced_providers import RunwayVideoEditProvider
        from .library_media import LibraryMedia
        from .provider_prompts import compile_prompt
        provider = provider or RunwayVideoEditProvider(timeout=service.manager.settings.llm_timeout_sec)
        left,top,right,bottom = box
        width,height = right-left,bottom-top
        instructions = ("Edit this cropped region of an existing animated scene. Preserve the original camera, timing, "
            "motion and visual style except for the requested change. Keep framing and scale stable, without cuts "
            "or new borders. This edited region will replace the same fixed rectangle in the original video. Request: "+prompt.strip())
        submitted = compile_prompt(service.assets.creative,instructions,video=True,task_key="animation:visual_region:"+sid+":prompt")
        from .runway_prompt_limits import require_prompt_budget
        require_prompt_budget(submitted)
        atomic_write(work/"edit_request.json",json.dumps({"sourceAssetId":previous["asset"]["assetId"],
            "sourceFile":source.relative_to(service.path).as_posix(),"sourceHash":previous["fileHash"],
            "regionPixels":list(box),"regionNormalized":selection,"prompt":prompt.strip(),"submittedPrompt":submitted,
            "startTime":edit_start,"endTime":edit_end,"model":EDIT_MODEL,"inputHash":source_hash},ensure_ascii=False,indent=2))
        chunks=[]
        count = max(1,math.ceil(edit_duration/28))
        try:
            for index in range(count):
                if service.stop_requested():
                    raise InterruptedError("Stopped before the next regional video request. The original is retained.")
                start = edit_start+edit_duration*index/count
                length = edit_duration/count
                request_duration = max(2.,length)
                crop,edited,normalized = [work/f"part_{index:03d}_{name}.mp4" for name in ("crop","edited","normalized")]
                if not crop.exists():
                    limit = min(1.,1920/width,1080/height) if width>=height else min(1.,1080/width,1920/height)
                    upload_width,upload_height = max(8,int(width*limit)//2*2),max(8,int(height*limit)//2*2)
                    filters = (f"crop={width}:{height}:{left}:{top},scale={upload_width}:{upload_height},fps={min(30,options.fps)},"
                               f"tpad=stop_mode=clone:stop_duration={max(0,request_duration-length):.9f}")
                    tools.run(["-ss",start,"-i",source,"-t",request_duration,"-vf",filters,
                        "-an","-c:v","libx264","-crf","18","-maxrate","20M","-bufsize","40M",
                        "-pix_fmt","yuv420p",crop])
                config = ProviderConfig(provider="runway",model=EDIT_MODEL,
                    options={"output_path":str(edited),"duration":request_duration})
                asset = LibraryMedia(service.manager,service.project_id)._call(provider,provider.edit_clip,
                    (submitted,crop),config,"animation:visual_region:"+sid)
                output = Path(asset.file_path).resolve()
                if output != edited.resolve() or not output.is_file():
                    raise ValueError("The edit provider returned an invalid output path.")
                edited_duration = tools.validate(edited)
                if not math.isfinite(edited_duration) or edited_duration<=0:
                    raise ValueError("The edited region has no usable duration.")
                # Normalize each returned part, preserving the source timeline.
                tools.run(["-i",edited,"-t",length,"-vf",
                    f"scale={width}:{height},setpts={(request_duration/edited_duration):.12f}*(PTS-STARTPTS),fps={options.fps}",
                    "-an","-c:v","libx264","-crf","0","-pix_fmt","yuv420p",normalized])
                asset.file_path = edited.relative_to(service.path).as_posix()
                chunks.append({"file":normalized.relative_to(service.path).as_posix(),"start":start,"duration":length,"requestDuration":request_duration,
                    "asset":asset.model_dump(mode="json",by_alias=True)})
            if service.stop_requested():
                raise InterruptedError("Stopped before compositing. The original scene video is retained.")
            # Filenames are generated internally, avoiding concat-path quoting.
            listing = work/"parts.txt"
            atomic_write(listing,"\n".join("file '"+Path(item["file"]).name+"'" for item in chunks)+"\n")
            patch = work/"patch.mp4"
            tools.run(["-f","concat","-safe","1","-i",listing,"-an","-c:v","copy",patch],cwd=work)
            output = work/"visual.mp4"
            # Lossless H.264 keeps source pixels outside the effective rectangle.
            tools.run(["-i",source,"-i",patch,"-filter_complex_threads","1","-filter_complex",
                f"[0:v]setpts=PTS-STARTPTS[base];[1:v]setpts=PTS-STARTPTS+{edit_start:.9f}/TB[patch];"
                f"[base][patch]overlay={left}:{top}:eof_action=pass:repeatlast=0:shortest=0:"
                f"enable='gte(t,{edit_start:.9f})*lt(t,{edit_end:.9f})',format=yuv420p[v]",
                "-map","[v]","-map","0:a?","-t",duration,"-c:v","libx264","-crf","0",
                "-pix_fmt","yuv420p","-c:a","copy","-movflags","+faststart",output])
            actual_duration = tools.validate(output)
            if abs(actual_duration-duration) > max(.15,2/options.fps):
                raise ValueError("The edited scene duration differs from its source; the original was retained.")
            clip = GeneratedAsset(type="video",provider="runway",model=EDIT_MODEL,source_scene_id=sid,
                file_path=output.relative_to(service.path).as_posix(),duration=actual_duration,prompt_hash=input_hash(submitted),
                generation_params={"sourceAssetId":previous["asset"]["assetId"],"sourceFile":str(source.relative_to(service.path)),
                    "sourceFileHash":previous["fileHash"],"regionPixels":list(box),"regionNormalized":selection,
                    "regionMode":"fixed_screen_rectangle","modificationPrompt":prompt.strip(),"submittedPrompt":submitted,
                    "protectedOutsideRegion":True,"startTime":edit_start,"endTime":edit_end,
                    "protectedOutsideTimeRange":True,"segments":chunks,"deliveryFormats":formats})
            record = deepcopy(previous)
            record.update(status="succeeded",revision=next_revision(service,sid,previous),
                approval=ApprovalRecord(content_hash=source_hash).model_dump(mode="json",by_alias=True),error="",
                attempts=previous.get("attempts",0)+1,sourceHash=source_hash,generationSourceHash=source_hash,
                asset=clip.model_dump(mode="json",by_alias=True),
                fileHash=file_hash(output))
            record.pop("appliedAt",None)
            record.pop("appliedFromAssetId",None)
            record["formats"] = {formats[0]:{"status":"succeeded","asset":record["asset"],"fileHash":record["fileHash"]}}
            if formats == [LANDSCAPE,PORTRAIT]:
                portrait = work/"portrait.mp4"
                seconds = create_portrait(tools,output,portrait,options)
                pclip = GeneratedAsset(type="video",provider="local",model="portrait-center-crop",
                    file_path=portrait.relative_to(service.path).as_posix(),duration=seconds,prompt_hash=clip.prompt_hash,
                    generation_params={"sourceAssetId":clip.asset_id,"deliveryFormat":PORTRAIT})
                record["formats"][PORTRAIT] = {"status":"succeeded","asset":pclip.model_dump(mode="json",by_alias=True),"fileHash":file_hash(portrait)}
            # Check again before replacing the active selection. Failed work
            # never overwrites the previous record or its approval.
            latest = service.visuals()
            if input_hash(latest.get(sid,{})) != expected or service.visual_hash(sid,options) != source_hash:
                raise ValueError("Scene inputs changed during editing. The candidate was saved but not applied.")
            metadata = {"source":previous["asset"],"rectangle":list(box),"startTime":edit_start,"endTime":edit_end,"prompt":submitted,"result":record}
            atomic_write(work/"edit_metadata.json",json.dumps(metadata,ensure_ascii=False,indent=2,default=str))
            archive(service,sid,record)
            latest[sid] = record
            atomic_write(service.path/"visual_scenes.json",json.dumps(latest,ensure_ascii=False,indent=2,default=str))
            from .error_reports import clear_reports
            clear_reports(service.path,"animation:visual_region:"+sid)
            return record
        except InterruptedError:
            raise
        except Exception as exc:
            from .error_reports import record_failure,matching_reports
            task = "animation:visual_region:"+sid
            if not matching_reports(service.path,str(exc),task):
                record_failure(service.path,task,exc)
            exc.error_report_project_path = str(service.path)
            exc.error_report_task = task
            raise
