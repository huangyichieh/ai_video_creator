"""Documented Sync multipart lip-sync and ElevenLabs sound-generation adapters."""
from .service_limits import limited
import json
import math
import os
from pathlib import Path
import time

import httpx

from .files import input_hash
from .llm import ProviderError, TransientProviderError
from .media_providers import ThreadLocalUsage
from .media_utils import write_bytes
from .models import GeneratedAsset
from .request_tracking import track_client


class MediaHTTP(ThreadLocalUsage):
    def __init__(self, client=None, *, timeout=120):
        self.client, self.timeout = client, timeout

    def _client(self):
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout)
        track_client(self.client)
        return self.client

    def _request(self, method, url, **kwargs):
        try:
            response = self._client().request(method, url, **kwargs)
        except httpx.TransportError:
            raise TransientProviderError("Media request could not finish; retry to resume any saved job.") from None
        if response.status_code >= 400:
            error = TransientProviderError if response.status_code == 429 or response.status_code >= 500 else ProviderError
            from .provider_errors import response_error
            detail = response_error(response)
            raise error(f"Media provider returned HTTP {response.status_code}. " +
                        (detail or "Check credentials, account access and input limits."))
        return response

    def _download(self, url, path):
        target = httpx.URL(url)
        if target.scheme != "https" or target.userinfo or target.host in ("localhost", "127.0.0.1", "::1"):
            raise ProviderError("The media provider returned an invalid download URL")
        # Authentication is attached only to API calls, never to the output CDN.
        response = self._request("GET", str(target))
        if response.status_code != 200 or not response.content:
            raise ProviderError("The generated media could not be downloaded")
        write_bytes(path, response.content)


class SyncLipSyncProvider(MediaHTTP):
    def generate(self, video, audio, config):
        self.last_usage = {"video_seconds": 0} if config.options.get("job_id") else {}
        secret = os.environ.get("SYNC_API_KEY", "").strip()
        if not secret:
            raise ProviderError("Set SYNC_API_KEY in .env before generating lip-sync")
        update = config.options.get("job_update", lambda *_: None)
        job_id = config.options.get("job_id")
        headers = {"x-api-key": secret}
        if not job_id:
            data = {"model": config.model, "options": json.dumps({"sync_mode": "cut_off",
                **({"active_speaker_detection": config.options["face_target"]} if config.options.get("face_target") else {})})}
            headers["Idempotency-Key"] = config.options["idempotency_key"]
            with Path(video).open("rb") as visual, Path(audio).open("rb") as speech:
                response = self._request("POST", "https://api.sync.so/v2/generate", headers=headers, data=data,
                    files={"video": ("visual.mp4", visual, "video/mp4"), "audio": ("dialogue.wav", speech, "audio/wav")})
            job = response.json()
            job_id = job.get("id")
            if not job_id:
                raise ProviderError("Lip-sync submission returned no job ID; retry with the saved idempotency key")
            self.last_usage = {"video_seconds": 0 if config.options.get("charge_recorded") else config.options["duration"], "job_id": job_id}
            update(job_id, job.get("status", "PENDING"), 0)
        else:
            job = self._request("GET", f"https://api.sync.so/v2/generate/{job_id}", headers=headers).json()
        deadline = time.monotonic() + config.options.get("poll_timeout", 1800)
        while job.get("status") in ("PENDING", "PROCESSING"):
            update(job_id, job["status"], int(job.get("progress_percent") or 0))
            if time.monotonic() >= deadline:
                raise TransientProviderError("Lip-sync is still processing. Retry to resume the saved job without creating another.")
            time.sleep(config.options.get("poll_interval", 1))
            job = self._request("GET", f"https://api.sync.so/v2/generate/{job_id}", headers=headers,
                                params={"wait": "true", "timeout": 5}).json()
        update(job_id, job.get("status", "UNKNOWN"), 100 if job.get("status") == "COMPLETED" else 0)
        if job.get("status") != "COMPLETED":
            raise ProviderError("Lip-sync generation failed or was rejected; inspect the scene and regenerate this item")
        destination = Path(config.options["output_path"])
        # Resolve a fresh, plan-appropriate download rather than persisting an
        # expiring signed URL or forwarding credentials to its storage host.
        url = self._request("GET", f"https://api.sync.so/v2/generations/{job_id}/download", headers=headers).text.strip()
        self._download(url, destination)
        return GeneratedAsset(type="video", provider="sync", model=config.model, file_path=str(destination),
            duration=job.get("outputDuration") or config.options["duration"],
            prompt_hash=input_hash({"model": config.model, "face": config.options.get("face_target")}),
            generation_params={"jobId": job_id, "syncMode": "cut_off"})


class ElevenLabsSFXProvider(MediaHTTP):
    @limited('elevenlabs')
    def generate(self, prompt, config):
        self.last_usage = {}
        secret = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        if not secret:
            raise ProviderError("Set ELEVENLABS_API_KEY in .env before generating sound effects")
        response = self._request("POST", "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": secret}, params={"output_format": "mp3_44100_128"},
            json={"text": prompt, "model_id": "eleven_text_to_sound_v2", "duration_seconds": config.options["duration"],
                  "loop": config.options.get("loop", False), "prompt_influence": config.options.get("prompt_influence", .9)})
        self.last_usage = {"audio_seconds": config.options["duration"]}
        units = response.headers.get("character-cost")
        if units:
            try:
                count = float(units)
                if math.isfinite(count) and count >= 0:
                    self.last_usage.update(billing_units=count, billing_unit="characters")
            except ValueError:
                pass
        destination = Path(config.options["output_path"])
        write_bytes(destination, response.content)
        return GeneratedAsset(type="audio", provider="elevenlabs", model="eleven_text_to_sound_v2",
            file_path=str(destination), duration=config.options["duration"], prompt_hash=input_hash(prompt),
            generation_params={"prompt": prompt, "loop": config.options.get("loop", False),
                               "promptInfluence": config.options.get("prompt_influence", .9)})


class ElevenLabsMusicProvider(MediaHTTP):
    @limited('elevenlabs')
    def generate(self,prompt,config):
        self.last_usage = {}
        secret = os.environ.get("ELEVENLABS_API_KEY","").strip()
        if not secret:
            raise ProviderError("Set ELEVENLABS_API_KEY in .env before generating music")
        duration = config.options["duration"]
        if not math.isfinite(duration) or not 3 <= duration <= 600 or not prompt.strip() or len(prompt) > 4100:
            raise ValueError("Music requires a prompt up to 4100 characters and a duration from 3 to 600 seconds.")
        response = self._request("POST","https://api.elevenlabs.io/v1/music",headers={"xi-api-key":secret},
            params={"output_format":"mp3_44100_128"},json={"prompt":prompt,"music_length_ms":round(duration*1000),
                "model_id":config.model,"force_instrumental":True})
        self.last_usage = {"audio_seconds":duration}
        units = response.headers.get("character-cost")
        if units:
            try:
                number = float(units)
                if math.isfinite(number) and number >= 0:
                    self.last_usage.update(billing_units=number,billing_unit="characters")
            except ValueError:
                pass
        song_id = response.headers.get("song-id")
        if song_id:
            self.last_usage["provider_job_id"] = song_id
        write_bytes(Path(config.options["output_path"]),response.content)
        return GeneratedAsset(type="audio",provider="elevenlabs",model=config.model,file_path=config.options["output_path"],
            duration=duration,prompt_hash=input_hash(prompt),generation_params={"prompt":prompt,"instrumental":True,"songId":song_id})
