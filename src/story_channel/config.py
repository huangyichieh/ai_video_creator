import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from dotenv import load_dotenv

from .models import Model, Money, Nonempty


class Settings(Model):
    data_root: Path = Path("AI_Story_Channel")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    default_language: str = Field(default="zh-TW", min_length=1)
    default_target_duration_sec: int = Field(default=600, gt=0)
    llm_provider: Literal["openai", "demo"] = "openai"
    llm_model: Nonempty = "gpt-6.1-sol"
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_timeout_sec: float = Field(default=120, gt=0, allow_inf_nan=False)
    llm_max_output_tokens: int = Field(default=16000, gt=0)
    input_cost_per_million: Money | None = None
    cached_input_cost_per_million: Money | None = None
    output_cost_per_million: Money | None = None
    prompt_provider: Nonempty = "generic"
    prompt_model: Nonempty = "generic"
    asset_provider: Literal["openai", "demo"] = "openai"
    image_provider: Literal["runway", "openai"] = "runway"
    image_model: Nonempty = "gen4_image"
    image_size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1536x1024"
    image_quality: Literal["low", "medium", "high"] = "medium"
    tts_provider: Literal["elevenlabs", "openai"] = "elevenlabs"
    tts_model: Nonempty = "eleven_v4"
    alignment_model: Literal["whisper-1"] = "whisper-1"
    narrator_voice: str = ""
    image_cost_per_image: Money | None = None
    tts_cost_per_million_characters: Money | None = None
    alignment_cost_per_minute: Money | None = None
    video_cost_per_second: Money | None = None
    runway_image_cost_per_image: Money | None = None
    runway_video_cost_per_second: Money | None = None
    act_two_cost_per_second: Money | None = None
    elevenlabs_tts_cost_per_million_characters: Money | None = None
    lip_sync_cost_per_second: Money | None = None
    sfx_cost_per_second: Money | None = None
    music_cost_per_minute: Money | None = None
    ffmpeg_path: str | None = None
    render_timeout_sec: float = Field(default=1800, gt=0, allow_inf_nan=False)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        # Read only this working directory's file; existing environment values win.
        load_dotenv(Path.cwd() / ".env", override=False, encoding="utf-8-sig", interpolate=False)
        values = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
        # Normalize aliases before applying overrides to avoid duplicate keys.
        values = cls.model_validate(values).model_dump()
        # Environment overrides JSON; credentials are never part of Settings or manifests.
        for field in cls.model_fields:
            value = os.environ.get(f"STORY_{field.upper()}")
            if value is not None:
                values[field] = value
        return cls.model_validate(values)
