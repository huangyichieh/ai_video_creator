"""Existing provider/library samples and saved auditions; never synthesize audio."""
from urllib.parse import urlparse

from .animation import AnimationDirector, voice_signature
from .llm import ProviderError
from .media_utils import file_hash
from .voice_validation import lookup_elevenlabs_voice


def saved_voice_sample(manager, project_id, speaker, voice_id, *, demo, narrator=False):
    if not voice_id.strip():
        raise ProviderError("Save a Voice ID before playing a sample.")
    if not demo and manager.settings.tts_provider == "elevenlabs":
        data = lookup_elevenlabs_voice(manager, project_id, voice_id, speaker=speaker, step="voice_sample")
        url = data.get("preview_url")
        if isinstance(url, str) and urlparse(url).scheme == "https" and urlparse(url).hostname:
            return {"voice_id": voice_id, "source": url, "format": "audio/mpeg",
                    "name": data.get("name") or speaker, "kind": "ElevenLabs library sample"}
    if not narrator:
        director = AnimationDirector(manager, project_id)
        state = director.load()
        profile = state.plan.voices.get(speaker) if state else None
        preview = state.previews.get(speaker) if state else None
        if (profile and preview and profile.voice_id == voice_id
                and preview.profile_hash == voice_signature(profile, manager.settings)
                and preview.asset.provider == ("demo" if demo else manager.settings.tts_provider)):
            path = director.assets.local_path(preview.asset.file_path)
            if path.is_file() and file_hash(path) == preview.file_hash:
                return {"voice_id": voice_id, "source": str(path), "format": "audio/wav", "name": speaker,
                        "kind": "Offline demo placeholder" if demo else "Saved voice audition"}
    raise ProviderError("No existing sample is available for this saved voice. Generate a voice preview or add a preview in ElevenLabs first. No audio was generated.")
