"""Replaceable image, speech, and timestamp adapters. No calls until generation."""
from .service_limits import limited
import base64
import io
import os
import re
from contextlib import ExitStack
from pathlib import Path
from threading import local, Lock

from PIL import Image, ImageDraw

from .asset_models import Alignment, Cue
from .files import input_hash
from .llm import ProviderError, TransientProviderError
from .media_utils import silence, wav_duration, write_bytes
from .models import AudioAsset, GeneratedAsset, ProviderConfig


class ThreadLocalUsage:
    @property
    def last_usage(self):
        context = self.__dict__.setdefault("_usage_context", local())
        return getattr(context, "usage", {})

    @last_usage.setter
    def last_usage(self, value):
        context = self.__dict__.setdefault("_usage_context", local())
        context.usage = value


class OpenAIMedia(ThreadLocalUsage):
    def __init__(self, client=None, *, timeout=120):
        self.client = client
        self._owns_client = client is None
        self.timeout = timeout
        self.last_usage = {}
        self._client_lock = Lock()

    def _request(self, callback):
        from openai import APIConnectionError, APIStatusError, OpenAI
        self.last_usage = {}
        if self._owns_client:
            with self._client_lock:
                key = os.environ.get("OPENAI_API_KEY", "").strip()
                if not key:
                    raise ProviderError("Set OPENAI_API_KEY in .env before generating OpenAI assets.")
                if self.client is None:
                    self.client = OpenAI(api_key=key, timeout=self.timeout, max_retries=0)
                else:
                    self.client.api_key = key
        from .request_tracking import track_client
        track_client(self.client)
        try:
            return callback(self.client)
        except APIConnectionError as exc:
            raise TransientProviderError("OpenAI media request timed out or could not connect.") from exc
        except APIStatusError as exc:
            body = exc.body if isinstance(exc.body, dict) else {}
            error = body.get("error", body)
            error = error if isinstance(error, dict) else {}
            code = error.get("code")
            billing = error.get("type") == "insufficient_quota" or code in {
                "insufficient_quota", "credit_balance_exhausted", "project_spend_limit_exceeded",
                "organization_spend_limit_exceeded", "organization_usage_limit_exceeded", "billing_hard_limit_reached"}
            if billing:
                raise ProviderError("OpenAI API credits or usage/spend limits were reached. Check billing before retrying.") from exc
            message = f"OpenAI media request failed (HTTP {exc.status_code})."
            # Report structured diagnostics, never the arbitrary upstream message:
            # providers may echo prompt text, credentials or uploaded file paths.
            hints = {
                "model_not_found": "The selected model is unavailable or this API project lacks access.",
                "model_not_available": "The selected model is unavailable for this API project.",
                "organization_verification_required": "Complete OpenAI organization verification for this model.",
                "content_policy_violation": "OpenAI rejected the image or prompt under its content policy; review the references and prompt.",
                "moderation_blocked": "OpenAI rejected the image or prompt during moderation; review the references and prompt.",
                "invalid_image": "OpenAI rejected an input image; check its format, size and contents.",
                "invalid_image_format": "OpenAI rejected the input image format.",
                "image_parse_error": "OpenAI could not decode an input image.",
                "unsupported_parameter": "A request parameter is unsupported by the selected model.",
                "unknown_parameter": "The selected endpoint does not accept a request parameter.",
                "invalid_value": "A request parameter has an unsupported value.",
                "invalid_request_error": "OpenAI rejected the request parameters or image input.",
            }
            if isinstance(code, str) and code in hints:
                message += f" {hints[code]} Code: {code}."
            param = error.get("param")
            if param in ("model", "image", "image[]", "size", "quality", "output_format", "prompt", "n", "input_fidelity"):
                message += f" Parameter: {param}."
            request_id = getattr(exc, "request_id", None)
            if isinstance(request_id, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{8,128}", request_id):
                message += f" Request ID: {request_id}."
            if exc.status_code == 429 or exc.status_code >= 500:
                raise TransientProviderError(message + " Wait and retry.") from exc
            raise ProviderError(message + " Check model access and provider configuration.") from exc


class OpenAIImageProvider(OpenAIMedia):
    @limited('openai')
    def generate(self, prompt: str, references: list[Path], config: ProviderConfig) -> GeneratedAsset:
        options = config.options
        params = dict(model=config.model, prompt=prompt, n=1, size=options.get("size", "1536x1024"),
                      quality=options.get("quality", "medium"), output_format="png")
        with ExitStack() as stack:
            if references:
                params["image"] = [stack.enter_context(path.open("rb")) for path in references]
                if options.get("edit_mask"):
                    params["mask"] = stack.enter_context(Path(options["edit_mask"]).open("rb"))
                response = self._request(lambda client: client.images.edit(**params))
            else:
                response = self._request(lambda client: client.images.generate(**params))
        usage = getattr(response, "usage", None)
        self.last_usage = {"image_count": 1}
        if usage:
            self.last_usage.update(input_tokens=getattr(usage, "input_tokens", 0),
                                   output_tokens=getattr(usage, "output_tokens", 0))
        if not response.data or not response.data[0].b64_json:
            raise ProviderError("Image provider returned no image data.")
        data = base64.b64decode(response.data[0].b64_json, validate=True)
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        path = Path(options["output_path"])
        write_bytes(path, data)
        return GeneratedAsset(type="image", provider=config.provider, model=config.model,
                              prompt_hash=input_hash(prompt), file_path=str(path))


class OpenAITTSProvider(OpenAIMedia):
    @limited('openai')
    def generate(self, text: str, voice_id: str, config: ProviderConfig) -> AudioAsset:
        params = dict(model=config.model, input=text, voice=voice_id, response_format="wav")
        if config.model.startswith("gpt-"):
            params["instructions"] = "Read clearly and naturally. Keep a consistent voice. Speak only the supplied text."
            if options := config.options.get("project_style_rules"):
                params["instructions"] += " Audio direction only; do not speak these rules: " + options
            if config.options.get("voice_profile"):
                import json
                params["instructions"] += " Fixed voice profile: " + json.dumps(config.options["voice_profile"], ensure_ascii=False)
            if config.options.get("performance"):
                import json
                direction = {key: value for key, value in config.options["performance"].items() if key != "text"}
                params["instructions"] += " Act this line naturally using this direction; do not speak the metadata: " + json.dumps(direction, ensure_ascii=False)
            if config.options.get("generic_prompt"):
                params["instructions"] += " Overall genre and tone: " + config.options["generic_prompt"]
            if config.options.get("audio_tags"):
                from .audio_tags import speech_tags
                params["instructions"] += " Vocal performance cues (not spoken): " + ", ".join(speech_tags(config.options))
            if config.options.get("style_prompt"):
                params["instructions"] += " Delivery style: " + config.options["style_prompt"]
            if config.options.get("modification"):
                params["instructions"] += " Modify the delivery of the current spoken text as follows, preserving the words: " + config.options["modification"]["prompt"]
        elif any(config.options.get(key) for key in ("style_prompt", "generic_prompt", "modification", "voice_profile", "performance")):
            raise ProviderError("This speech model does not support style instructions. Use gpt-4o-mini-tts or clear the style prompt.")
        response = self._request(lambda client: client.audio.speech.create(**params))
        self.last_usage = {"audio_characters": len(text)}
        path = Path(config.options["output_path"])
        write_bytes(path, response.content)
        duration = wav_duration(path)
        if duration <= 0:
            raise ProviderError("Speech provider returned empty audio.")
        self.last_usage["audio_seconds"] = duration
        return AudioAsset(provider=config.provider, model=config.model, voice_id=voice_id,
                          prompt_hash=input_hash(text), file_path=str(path), duration=duration)


class OpenAIAlignmentProvider(OpenAIMedia):
    @limited('openai')
    def align(self, audio: Path, text: str, language: str, config: ProviderConfig) -> Alignment:
        duration = wav_duration(audio)
        with audio.open("rb") as stream:
            response = self._request(lambda client: client.audio.transcriptions.create(
                file=stream, model=config.model, response_format="verbose_json",
                timestamp_granularities=["segment", "word"], language=language.split("-")[0], prompt=text[:250]))
        self.last_usage = {"audio_seconds": duration}
        segments = [Cue(start=item.start, end=item.end, text=item.text.strip())
                    for item in (response.segments or []) if item.text.strip()]
        words = [Cue(start=item.start, end=item.end, text=item.word.strip())
                 for item in (response.words or []) if item.word.strip() and item.end > item.start]
        if not segments and not words:
            raise ProviderError("No speech timestamps were returned. Retry the alignment track.")
        return Alignment(duration=duration, segments=segments or words, words=words)


class DemoImageProvider(ThreadLocalUsage):

    def generate(self, prompt, references, config):
        image = Image.new("RGB", (768, 384 if config.options.get("panorama") else 432), "#e2e8f0")
        draw = ImageDraw.Draw(image)
        draw.text((32, 40), "OFFLINE DEMO - placeholder image", fill="#0f172a")
        draw.text((32, 80), input_hash(prompt)[:32], fill="#334155")
        output = io.BytesIO()
        image.save(output, "PNG")
        path = Path(config.options["output_path"])
        write_bytes(path, output.getvalue())
        return GeneratedAsset(type="image", provider="demo", model="demo-image", prompt_hash=input_hash(prompt), file_path=str(path))


class DemoTTSProvider(ThreadLocalUsage):

    def generate(self, text, voice_id, config):
        path = Path(config.options["output_path"])
        duration = max(0.5, min(len(text) / 10, 6))
        write_bytes(path, silence(duration))
        return AudioAsset(provider="demo", model="demo-voice", voice_id=voice_id,
                          prompt_hash=input_hash(text), file_path=str(path), duration=wav_duration(path))


class DemoAlignmentProvider(ThreadLocalUsage):

    def align(self, audio, text, language, config):
        duration = wav_duration(audio)
        return Alignment(duration=duration, segments=[Cue(start=0, end=duration, text=text)])
