"""Provider-neutral per-scene video generation; FFmpeg composes the final timeline."""
from pathlib import Path
import time

from .ffmpeg_renderer import FFmpeg
from .files import input_hash
from .llm import ProviderError, TransientProviderError
from .media_providers import OpenAIMedia, ThreadLocalUsage
from .models import GeneratedAsset
from .render_models import RenderOptions


class LocalVideoProvider(ThreadLocalUsage):
    def __init__(self, ffmpeg=None):
        self.ffmpeg = ffmpeg or FFmpeg()

    def generate_clip(self, prompt, references, config):
        self.last_usage = {}
        if not references:
            raise ValueError("Local motion rendering needs a scene image. Select OpenAI for text-to-video.")
        destination = Path(config.options["output_path"])
        self.ffmpeg.visual(references[0], destination, config.options["duration"], RenderOptions.model_validate(config.options["render_options"]))
        return GeneratedAsset(type="video", provider="local", model="ffmpeg-image-motion", file_path=str(destination),
                              duration=config.options["duration"], prompt_hash=input_hash(prompt))


class OpenAIVideoProvider(OpenAIMedia):
    def generate_clip(self, prompt, references, config):
        from PIL import Image, ImageOps
        destination = Path(config.options["output_path"])
        update = config.options.get("job_update", lambda *_: None)
        job_id = config.options.get("job_id")
        charged_seconds = 0
        try:
            if not job_id:
                params = {"model": config.model, "prompt": prompt, "seconds": str(config.options["seconds"]), "size": config.options["size"]}
                if config.options.get("edit_source"):
                    from openai.types.video import Video
                    # The SDK's edit helper currently treats `video` as an upload.
                    # Send the documented JSON form for an existing video ID.
                    job = self._request(lambda client: client.post("/videos/edits", cast_to=Video,
                        body={"prompt": prompt, "video": {"id": config.options["edit_source"]}}))
                elif references:
                    size = tuple(map(int, config.options["size"].split("x")))
                    reference = destination.parent / "video_reference.png"
                    with Image.open(references[0]) as image:
                        ImageOps.fit(image.convert("RGB"), size).save(reference, "PNG")
                    with reference.open("rb") as stream:
                        job = self._request(lambda client: client.videos.create(**params, input_reference=stream))
                else:
                    job = self._request(lambda client: client.videos.create(**params))
                job_id = job.id
                charged_seconds = int(job.seconds or config.options["seconds"])
                update(job.id, job.status, getattr(job, "progress", 0) or 0)
            else:
                job = self._request(lambda client: client.videos.retrieve(job_id))
            deadline = time.monotonic() + config.options.get("poll_timeout", 1800)
            while job.status in ("queued", "in_progress"):
                update(job.id, job.status, getattr(job, "progress", 0) or 0)
                if time.monotonic() >= deadline:
                    raise TransientProviderError("Video job is still processing. Retry this scene to resume the saved job without submitting another generation.")
                time.sleep(config.options.get("poll_interval", 2))
                job = self._request(lambda client: client.videos.retrieve(job.id))
            update(job.id, job.status, getattr(job, "progress", 0) or 0)
            if job.status != "completed":
                raise ProviderError("OpenAI video generation failed. Retry this scene; its job ID and usage were saved.")
            response = self._request(lambda client: client.videos.download_content(job.id, variant="video"))
            response.write_to_file(str(destination))
            return GeneratedAsset(type="video", provider="openai", model=config.model, file_path=str(destination),
                                  duration=int(job.seconds), prompt_hash=input_hash(prompt), generation_params={"jobId": job.id, "editSource": config.options.get("edit_source")})
        finally:
            # Retrieval/download calls do not create a second video generation charge.
            self.last_usage = {"video_seconds": charged_seconds, "job_id": job_id}
