from typing import Literal

from pydantic import Field, model_validator, field_validator

from .core import Model, ApprovalRecord, GeneratedAsset


class RenderOptions(Model):
    continuity_animation: bool = False
    provider: Literal["local", "openai", "runway"] = "local"
    generation_mode: Literal["image", "text"] = "image"
    video_model: Literal["sora-2", "sora-2-pro", "klingO3_pro"] = "sora-2"
    video_seconds: Literal[4, 8, 12] = 8
    video_size: Literal["1280x720", "720x1280"] = "1280x720"
    delivery_formats: list[Literal["landscape_16_9", "portrait_9_16"]] = Field(default_factory=list)
    portrait_safe_area: bool = False
    width: int = Field(default=1280, ge=160, le=3840)
    height: int = Field(default=720, ge=90, le=3840)
    fps: int = Field(default=24, ge=12, le=60)
    motion: Literal["static", "zoom_pan"] = "zoom_pan"
    transition: Literal["cut", "fade"] = "fade"
    transition_seconds: float = Field(default=0.2, ge=0, le=2)
    subtitles: Literal["burn", "soft", "none"] = "burn"
    font: str = Field(default="Microsoft JhengHei", pattern=r"^[\w .-]{1,80}$")
    font_size: int = Field(default=24, ge=8, le=100)
    music: bool = True
    sfx: bool = True
    music_gain: float = Field(default=0.12, ge=0, le=1)
    sfx_gain: float = Field(default=0.3, ge=0, le=1)
    duck_music: bool = True
    normalize_voice: bool = True

    @field_validator("delivery_formats")
    @classmethod
    def ordered_formats(cls, value):
        return [name for name in ("landscape_16_9", "portrait_9_16") if name in value]

    @model_validator(mode="after")
    def even_size(self):
        if len(self.delivery_formats) == 2:
            object.__setattr__(self,"portrait_safe_area",True)
        if self.delivery_formats:
            portrait = self.delivery_formats == ["portrait_9_16"]
            object.__setattr__(self,"video_size","720x1280" if portrait else "1280x720")
            long_edge = max(self.width,self.height,320 if portrait else 160)
            short_edge = round(long_edge*9/16/2)*2
            object.__setattr__(self,"width",short_edge if portrait else long_edge)
            object.__setattr__(self,"height",long_edge if portrait else short_edge)
        if self.width % 2 or self.height % 2:
            raise ValueError("Video width and height must be even numbers")
        if self.provider == "local" and self.generation_mode == "text":
            raise ValueError("Text-to-video requires the OpenAI provider; local mode animates scene images")
        if self.continuity_animation and (self.provider not in ("openai", "runway") or self.generation_mode != "image"):
            raise ValueError("Continuity animation requires OpenAI image-to-video; local rendering is only a preview")
        if self.provider == "runway" and (self.video_model != "klingO3_pro" or self.generation_mode != "image"):
            raise ValueError("Runway production uses Kling O3 Pro image-to-video")
        if self.provider == "openai" and self.video_model not in ("sora-2", "sora-2-pro"):
            raise ValueError("Select a Sora model for the legacy OpenAI video provider")
        return self


class SoundCue(Model):
    name: str = ""
    at: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration: float = Field(default=0.5, gt=0, allow_inf_nan=False)
    file: str | None = None
    source: str = Field(default="Procedurally synthesized locally; no third-party recording", min_length=1)
    loop: bool = False
    prompt: str = ""


class SceneSound(Model):
    bgm_mood: str = "ambient"
    bgm_file: str | None = None
    bgm_source: str = Field(default="Procedurally synthesized locally; no third-party recording", min_length=1)
    cues: list[SoundCue] = Field(default_factory=list)
    ambience: list[SoundCue] = Field(default_factory=list)


class MusicPlan(Model):
    scenes: dict[str, SceneSound] = Field(default_factory=dict)


class RenderScene(Model):
    scene_id: str
    approved_hash: str = ""
    status: Literal["pending", "running", "succeeded", "failed", "stale"] = "pending"
    input_hash: str = ""
    revision: int = 0
    attempts: int = 0
    clip: GeneratedAsset | None = None
    clip_hash: str = ""
    file: str | None = None
    file_hash: str = ""
    duration: float = 0
    error: str | None = None
    modification_prompt: str = ""
    modification_source: str | None = None
    # A submitted remote job is persisted before polling to avoid duplicate paid jobs.
    remote_job_id: str | None = None
    remote_job_key: str = ""
    remote_status: str | None = None
    remote_progress: int = 0


class RenderState(Model):
    options: RenderOptions = Field(default_factory=RenderOptions)
    music_plan: MusicPlan = Field(default_factory=MusicPlan)
    music_plan_custom: bool = False
    source_hash: str = ""
    scenes: dict[str, RenderScene] = Field(default_factory=dict)
    status: Literal["not_started", "rendering", "stopped", "failed", "review", "approved", "stale", "continuity_review"] = "not_started"
    final_file: str | None = None
    final_hash: str = ""
    final_input_hash: str = ""
    final_revision: int = 0
    final_exports: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
    approval: ApprovalRecord | None = None
