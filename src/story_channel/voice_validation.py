"""Read-only provider voice verification before persisting a new casting ID."""
from contextlib import nullcontext
import os
from urllib.parse import quote

import httpx
from httpx import Client

from .llm import ProviderError
from .request_tracking import track_client, tracked_call


class VoiceNotFoundError(ProviderError):
    """The provider could not find the requested voice."""


def validate_provider_voice(manager, project_id, voice_id, *, speaker, demo=None, client=None):
    if demo is None:
        from .assets import AssetPipeline
        demo = AssetPipeline(manager, project_id).demo
    if demo or manager.settings.tts_provider != "elevenlabs":
        return None
    data = lookup_elevenlabs_voice(manager, project_id, voice_id, speaker=speaker, client=client)
    return data.get("name") if isinstance(data.get("name"), str) else voice_id


def lookup_elevenlabs_voice(manager, project_id, voice_id, *, speaker, client=None, step="voice_validation"):
    voice_id = voice_id.strip()
    if not voice_id:
        raise ProviderError("Enter an ElevenLabs Voice ID before saving.")
    secret = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not secret:
        raise ProviderError("Set ELEVENLABS_API_KEY in API key settings before saving a voice. The saved voice was not changed.")

    def lookup():
        with nullcontext(client) if client is not None else Client(timeout=10, follow_redirects=False) as connection:
            track_client(connection)
            try:
                response = connection.get("https://api.elevenlabs.io/v1/voices/" + quote(voice_id, safe=""),
                                          headers={"xi-api-key": secret}, timeout=10, follow_redirects=False)
            except httpx.RequestError:
                raise ProviderError("Could not reach ElevenLabs to verify the Voice ID. Try saving again. The saved voice was not changed.") from None
            if response.status_code == 404:
                raise VoiceNotFoundError("ElevenLabs Voice ID was not found or is unavailable to this account. Copy the ID from My Voices and try again. The saved voice was not changed.")
            if response.status_code == 401:
                raise ProviderError("ElevenLabs rejected the API key. Update ELEVENLABS_API_KEY in API key settings before saving. The saved voice was not changed.")
            if response.status_code == 403:
                raise ProviderError("ElevenLabs denied access to this voice. Check your API key's voice read permissions and account access. The saved voice was not changed.")
            if response.status_code == 429:
                raise ProviderError("ElevenLabs rate limited voice verification. Try saving again later. The saved voice was not changed.")
            if not response.is_success:
                raise ProviderError(f"Could not verify the ElevenLabs Voice ID (HTTP {response.status_code}). Try saving again. The saved voice was not changed.")
            try:
                data = response.json()
            except ValueError:
                data = None
            if not isinstance(data, dict) or data.get("voice_id") != voice_id:
                raise ProviderError("ElevenLabs returned an invalid voice lookup response. Try saving again. The saved voice was not changed.")
            return data

    return tracked_call(manager, project_id, f"{step}:{speaker}", lookup)
