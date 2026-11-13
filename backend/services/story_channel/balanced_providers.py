"""v0.3 production adapters: Runway-hosted Kling/Act-Two and ElevenLabs voices."""
from .service_limits import limited
import json
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps, ImageDraw

from .effect_providers import MediaHTTP
from .files import atomic_write, input_hash
from .llm import ProviderError, TransientProviderError
from .models import GeneratedAsset, AudioAsset
from .media_utils import write_bytes, wav_duration
from .runway_prompt_limits import require_prompt_budget


class RunwayMedia(MediaHTTP):
    base = "https://api.dev.runwayml.com/v1"

    def headers(self):
        key = os.environ.get("RUNWAYML_API_SECRET", "").strip()
        if not key:
            raise ProviderError("Set RUNWAYML_API_SECRET in API key settings before generating Runway / Kling media")
        return {"Authorization": "Bearer " + key, "X-Runway-Version": "2024-11-06"}

    def upload(self, path):
        path = Path(path)
        # Ephemeral uploads avoid embedding large images/videos in JSON or logs.
        upload = self._request("POST", self.base + "/uploads", headers=self.headers(),
                               json={"filename": path.name, "type": "ephemeral"}).json()
        import httpx
        target = httpx.URL(upload["uploadUrl"])
        if target.scheme != "https" or target.userinfo or target.host in ("localhost", "127.0.0.1", "::1"):
            raise ProviderError("Invalid media upload URL")
        with path.open("rb") as stream:
            self._request("POST", str(target), data=upload["fields"], files={"file": (path.name, stream, mimetypes.guess_type(path.name)[0] or "application/octet-stream")})
        return upload["runwayUri"]

    def task(self, endpoint, body, config, unit, amount):
        self.last_usage = {}
        if "promptText" in body:
            require_prompt_budget(body["promptText"])
        destination = Path(config.options["output_path"])
        receipt = destination.with_suffix(destination.suffix + ".task.json")
        job_id = config.options.get("job_id")
        if not job_id and receipt.exists():
            previous = json.loads(receipt.read_text(encoding="utf-8"))
            job_id = previous["id"]
        self.last_usage = {unit: 0} if job_id else {}
        update = config.options.get("job_update", lambda *_: None)
        if not job_id:
            # Ambiguous submission failures are not automatically retried; without
            # a task ID, submitting again could create a second paid generation.
            try:
                job = self._request("POST", self.base + endpoint, headers=self.headers(), json=body).json()
            except TransientProviderError:
                raise ProviderError("Runway submission outcome unknown. Check provider tasks before retrying; no automatic resubmission was made.") from None
            job_id = job.get("id")
            if not job_id:
                raise ProviderError("Runway returned no task ID; check provider tasks before retrying")
            self.last_usage = {unit: amount, "job_id": job_id}
            atomic_write(receipt, json.dumps({"id": job_id}))
            update(job_id, "queued", 0)
        else:
            self.last_usage["job_id"] = job_id
        deadline = time.monotonic() + config.options.get("poll_timeout", 1800)
        while True:
            job = self._request("GET", self.base + "/tasks/" + quote(job_id, safe=""), headers=self.headers()).json()
            status = job.get("status", "UNKNOWN")
            normalized = {"SUCCEEDED": "completed", "FAILED": "failed", "CANCELED": "cancelled", "CANCELLED": "cancelled"}.get(status, "in_progress")
            update(job_id, normalized, 100 if status == "SUCCEEDED" else 0)
            if status == "SUCCEEDED":
                if not job.get("output"):
                    raise ProviderError("Runway task returned no generated output")
                self._download(job["output"][0], destination)
                return job_id
            if status in ("FAILED", "CANCELED", "CANCELLED"):
                from .provider_errors import safe_error_text
                code = job.get("failureCode")
                reason = job.get("failure")
                detail = safe_error_text("; ".join(str(value) for value in (code, reason) if value))
                atomic_write(receipt, json.dumps({"id": job_id, "status": status, "failure": detail}))
                raise ProviderError(f"Runway task {job_id} {status.lower()}. " + (detail + ". " if detail else "") + "Previously completed media is retained.")
            if time.monotonic() >= deadline:
                raise TransientProviderError("Runway task is still running; retry to retrieve the saved task")
            time.sleep(config.options.get("poll_interval", 5))


class RunwayImageProvider(RunwayMedia):
    @limited('runway')
    def generate(self, prompt, references, config):
        self.last_usage = {}
        destination = Path(config.options["output_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        refs = list(references)
        tags = list(config.options.get("reference_tags", []))
        scene_packing = None
        # Gen-4 Image accepts three references. Keep every identity/style input
        # visible by packing larger reference sets into numbered contact sheets.
        if len(refs) > 3 and config.options.get("scene_reference_roles"):
            from .scene_reference_sheets import pack,NOTICE
            roles = config.options["scene_reference_roles"]
            if len(roles) != len(refs):
                raise ProviderError("Scene reference roles do not match the supplied image references")
            refs,tags,scene_packing = pack(refs,roles,destination)
            prompt = NOTICE+prompt
        elif len(refs) > 3:
            separate_style = bool(tags and tags[-1] == "style")
            packed = refs[:-1] if separate_style else refs
            sheet = Image.new("RGB", (1536, 512 * ((len(packed)+2)//3)), "white")
            draw = ImageDraw.Draw(sheet)
            for i, path in enumerate(packed):
                with Image.open(path) as source:
                    sheet.paste(ImageOps.contain(source.convert("RGB"), (500, 475)), ((i%3)*512, (i//3)*512+30))
                draw.text(((i%3)*512+8, (i//3)*512+8), f"Input {i+1}", fill="black")
            contact = destination.with_suffix(".references.jpg")
            sheet.save(contact, quality=90)
            refs = [contact, refs[-1]] if separate_style else [contact]
            tags = ["content", "style"] if separate_style else []
            prompt = ("The numbered sheet contains identity/layout inputs in original order; @style is a separate style-only image. "
                      if separate_style else "The reference is a numbered contact sheet of all original inputs, in their original order. ") + prompt
        from .runway_reference_images import prepare
        refs,reference_preparation = prepare(refs,destination)
        submitted = require_prompt_budget(prompt)
        size = config.options.get("size", "1536x1024")
        ratio = "1080:1920" if size == "1024x1536" else "1024:1024" if size == "1024x1024" else "1920:1080"
        body = {"model": config.model, "promptText": submitted, "ratio": ratio}
        if refs:
            body["referenceImages"] = [{"uri": self.upload(path)} for path in refs]
            if tags and len(tags) == len(refs):
                for item, tag in zip(body["referenceImages"], tags):
                    item["tag"] = tag
            elif config.options.get("reference_role") == "design" and len(refs) == 1:
                body["referenceImages"][0]["tag"] = "design"
        from .error_reports import redact
        from .media_utils import file_hash
        request_file = destination.with_suffix(destination.suffix + ".request.json")
        receipt = destination.with_suffix(destination.suffix + ".task.json")
        if config.options.get("job_id") and config.options.get("request_snapshot_path"):
            original_request = Path(config.options["request_snapshot_path"])
            if original_request.is_file():
                request_file = original_request
        if not config.options.get("job_id") and not receipt.exists():
            snapshot = redact({"method": "POST", "endpoint": self.base + "/text_to_image",
                "apiVersion": "2024-11-06", "body": body, "contactSheet": len(references) > 3,
                "referenceImagePreparation":reference_preparation,
                **({"sceneReferencePacking":scene_packing} if scene_packing else {}),
                **({"sceneReferenceInputs":config.options["scene_reference_roles"]} if config.options.get("scene_reference_roles") else {}),
                **({"yawFill": config.options["yaw_fill"]} if config.options.get("yaw_fill") else {}), "referenceInputs": [
                    {"index": index, "file": str(Path(path).resolve()), "fileHash": file_hash(path),
                     "tag": body["referenceImages"][index-1].get("tag", ""),
                     "uri": body["referenceImages"][index-1]["uri"]}
                    for index, path in enumerate(refs, 1)]})
            atomic_write(request_file, json.dumps(snapshot, ensure_ascii=False, indent=2))
        job_id = self.task("/text_to_image", body, config, "image_count", 1)
        request_snapshot = json.loads(request_file.read_text(encoding="utf-8")) if request_file.exists() else None
        with Image.open(destination) as image:
            image.convert("RGB").save(destination, format="PNG")
        return GeneratedAsset(type="image", provider="runway", model=config.model, file_path=str(destination),
                              prompt_hash=input_hash(request_snapshot["body"]["promptText"] if request_snapshot else submitted), generation_params={"jobId": job_id,
                                  "submittedPrompt": request_snapshot["body"]["promptText"] if request_snapshot else submitted,
                                  "runwayRequest": request_snapshot, "contactSheet": request_snapshot.get("contactSheet", False) if request_snapshot else len(references) > 3})


class KlingVideoProvider(RunwayMedia):
    @limited('runway')
    def generate_clip(self, prompt, references, config):
        self.last_usage = {}
        if not references:
            raise ProviderError("Kling O3 Pro production requires an approved first-frame image")
        seconds = config.options["seconds"]
        size = config.options["size"]
        prompt = require_prompt_budget(prompt)
        body = {"model": "klingO3_pro", "promptText": prompt, "duration": seconds,
                "ratio": "1080:1920" if size.startswith("720x") else "1920:1080", "audio": False,
                "promptImage": self.upload(references[0])}
        extra = list(references[1:])
        last_frame = config.options.get("last_frame")
        if last_frame:
            body["promptImage"] = [{"uri": body["promptImage"], "position": "first"},
                                   {"uri": self.upload(last_frame), "position": "last"}]
        if len(extra) > (5 if last_frame else 6):
            raise ProviderError("Kling accepts at most seven total keyframe/reference images")
        if extra:
            body["references"] = [{"uri": self.upload(path)} for path in extra]
        job_id = self.task("/image_to_video", body, config, "video_seconds", seconds)
        return GeneratedAsset(type="video", provider="runway", model="klingO3_pro", file_path=config.options["output_path"],
                              duration=seconds, prompt_hash=input_hash(prompt), generation_params={"jobId": job_id, "submittedPrompt": body["promptText"]})


class RunwayVideoEditProvider(RunwayMedia):
    """Video-to-video crop edits; spatial masking is enforced by compositing."""
    @limited('runway')
    def edit_clip(self,prompt,source,config):
        self.last_usage = {}
        prompt = require_prompt_budget(prompt)
        keyframes=config.options.get("keyframes",[])
        if len(keyframes)>5:
            raise ValueError("Aleph accepts at most five timed keyframes per request.")
        import math
        duration=float(config.options["duration"])
        if any(not math.isfinite(float(frame["seconds"])) or not 0<=float(frame["seconds"])<=duration for frame in keyframes):
            raise ValueError("Aleph keyframe times must lie inside the input video.")
        body = {"model":"aleph2","videoUri":self.upload(source),
                "promptText":prompt,"outputFormat":"mp4"}
        if keyframes:
            body["keyframes"]=[{"uri":self.upload(frame["file"]),
                **({"at":1} if abs(float(frame["seconds"])-duration)<1e-6 else {"seconds":float(frame["seconds"])})}
                for frame in keyframes]
        job_id = self.task("/video_to_video",body,config,"video_seconds",config.options["duration"])
        return GeneratedAsset(type="video",provider="runway",model="aleph2",file_path=config.options["output_path"],
            duration=config.options["duration"],prompt_hash=input_hash(prompt),
            generation_params={"jobId":job_id,"submittedPrompt":body["promptText"],
                **({"timedKeyframes":[{"file":str(frame["file"]),"seconds":frame["seconds"]} for frame in keyframes]} if keyframes else {})})


class ActTwoProvider(RunwayMedia):
    @limited('runway')
    def generate_clip(self, prompt, references, config):
        self.last_usage = {}
        driving = Path(config.options["performance_video"])
        body = {"model": "act_two", "character": {"type": "image", "uri": self.upload(references[0])},
                "reference": {"type": "video", "uri": self.upload(driving)},
                "bodyControl": True, "expressionIntensity": config.options.get("expression_intensity", 3),
                "ratio": "720:1280" if config.options["size"].startswith("720x") else "1280:720"}
        duration = config.options.get("performance_duration") or config.options["seconds"]
        job_id = self.task("/character_performance", body, config, "video_seconds", duration)
        return GeneratedAsset(type="video", provider="runway", model="act_two", file_path=config.options["output_path"],
                              duration=duration, prompt_hash=input_hash(prompt), generation_params={"jobId": job_id})


class ElevenLabsTTSProvider(MediaHTTP):
    @limited('elevenlabs')
    def generate(self, text, voice_id, config):
        self.last_usage = {}
        secret = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not secret:
            raise ProviderError("Set ELEVENLABS_API_KEY in API key settings before generating voices")
        if not voice_id.strip() or voice_id in ("alloy", "echo", "fable", "onyx", "nova", "shimmer", "coral", "sage", "ash", "ballad", "marin", "cedar"):
            raise ProviderError("Choose an explicit ElevenLabs Voice ID and approve its audition; OpenAI voice names cannot be reused")
        performance = config.options.get("performance", {})
        profile = config.options.get("voice_profile", {})
        from .audio_tags import speech_tags
        tags = speech_tags(config.options) if config.model.startswith(("eleven_v3", "eleven_v4")) else []
        spoken = "".join(f"[{tag}] " for tag in tags) + text
        speed = max(.7, min(1.2, float(performance.get("pace", profile.get("pace", 1)))))
        response = self._request("POST", "https://api.elevenlabs.io/v1/text-to-speech/" + quote(voice_id, safe=""),
            headers={"xi-api-key": secret}, params={"output_format": "mp3_44100_128"},
            json={"model_id": config.model, "text": spoken, "voice_settings": {"speed": speed}})
        self.last_usage = {"audio_characters": len(spoken)}
        destination = Path(config.options["output_path"])
        mp3 = destination.with_suffix(".source.mp3")
        write_bytes(mp3, response.content)
        from .ffmpeg_renderer import FFmpeg
        volume = max(0., min(2., float(performance.get("volume", 1))))
        FFmpeg().run(["-i", mp3, "-ac", "1", "-ar", "24000", "-af", f"volume={volume}", "-c:a", "pcm_s16le", destination])
        return AudioAsset(provider="elevenlabs", model=config.model, voice_id=voice_id, file_path=str(destination),
                          duration=wav_duration(destination), prompt_hash=input_hash(text))


def image_provider(settings):
    if settings.image_provider == "runway":
        return RunwayImageProvider(timeout=settings.llm_timeout_sec)
    from .media_providers import OpenAIImageProvider
    return OpenAIImageProvider(timeout=settings.llm_timeout_sec)


def tts_provider(settings):
    if settings.tts_provider == "elevenlabs":
        return ElevenLabsTTSProvider(timeout=settings.llm_timeout_sec)
    from .media_providers import OpenAITTSProvider
    return OpenAITTSProvider(timeout=settings.llm_timeout_sec)


def video_provider(options, settings):
    if options.provider == "runway":
        return KlingVideoProvider(timeout=settings.llm_timeout_sec)
    from .video_providers import OpenAIVideoProvider
    return OpenAIVideoProvider(timeout=settings.llm_timeout_sec)
