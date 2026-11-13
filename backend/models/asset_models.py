from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .core import ApprovalRecord, GeneratedAsset, Model, Nonempty, utc_now

ASSET_STAGES = ("character_references", "backgrounds", "scene_images", "voices", "subtitles", "consistency")


class Cue(Model):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: Nonempty
    speaker: str = ""

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Cue end must be after start")
        return self


class Alignment(Model):
    duration: float = Field(gt=0, allow_inf_nan=False)
    segments: list[Cue]
    words: list[Cue] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_timestamps(self):
        for cues in (self.segments, self.words):
            previous = 0.0
            for cue in cues:
                if cue.start < previous or cue.end > self.duration + 0.05:
                    raise ValueError("Alignment timestamps must be ordered and within the audio duration")
                previous = cue.start
        return self


class AssetRecord(Model):
    stage: str
    subject: str
    status: Literal["pending", "running", "succeeded", "failed", "stale"] = "pending"
    input_hash: str = ""
    applied_input_hash: str = ""
    stale_reason: str = ""
    file_hash: str = ""
    asset: GeneratedAsset | None = None
    attempts: int = 0
    revision: int = 0
    remote_job_id: str | None = None
    remote_job_hash: str = ""
    remote_request_file: str = ""
    remote_status: str = ""
    error: str | None = None
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class SceneAssets(Model):
    scene_id: str
    image: str
    audio: str
    srt: str
    vtt: str
    timing: str
    duration: float
    hashes: dict[str, str] = Field(default_factory=dict)


class AssetModification(Model):
    prompt: Nonempty
    source_path: Nonempty
    source_hash: Nonempty
    source_asset_id: Nonempty
    spoken_text: str = ""
    delivery: dict = Field(default_factory=dict)


class VoiceDelivery(Model):
    emotion: str = "neutral"
    intensity: float = Field(default=.5, ge=0, le=1)
    pace: float = Field(default=1, ge=.7, le=1.2)
    volume: float = Field(default=1, ge=0, le=2)


class Production(Model):
    location_workflow_version: int = 0
    location_reference_sets: dict[str, dict] = Field(default_factory=dict)
    location_pair_prompts: dict[str, dict[str,str]] = Field(default_factory=dict)
    location_master_approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)
    location_yaw_approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)
    schema_version: Literal["1.0"] = "1.0"
    source_hash: str = ""
    provider_mode: Literal["openai", "demo"] = "openai"
    next_stage: str = "character_references"
    records: dict[str, AssetRecord] = Field(default_factory=dict)
    character_approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)
    background_approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)
    background_images: dict[str, dict[str, str]] = Field(default_factory=dict)
    background_modes: dict[str, str] = Field(default_factory=dict)
    generation_inputs: dict[str, dict] = Field(default_factory=dict)
    project_style_rules: str = ""
    project_style_image: dict[str, str] | None = None
    background_panorama_reviews: dict[str, dict] = Field(default_factory=dict)
    character_images: dict[str, dict[str, str]] = Field(default_factory=dict)
    voice_map: dict[str, str] = Field(default_factory=dict)
    voice_line_overrides: dict[str, dict] = Field(default_factory=dict)
    style_prompts: dict[str, str] = Field(default_factory=dict)
    # Older fields remain readable as historical provenance. Current World /
    # Episode projects use individual references after the one-time migration.
    per_asset_references: bool = False
    character_prompt_version: int = 0
    generic_prompt: str = ""
    style_reference: str | None = None
    style_reference_hash: str = ""
    style_references: list[dict[str, str]] = Field(default_factory=list, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def legacy_style_reference(cls, values):
        if isinstance(values, dict) and "style_references" not in values and "styleReferences" not in values:
            path = values.get("style_reference", values.get("styleReference"))
            if path:
                values = dict(values)
                values["style_references"] = [{"file": path, "hash": values.get("style_reference_hash", values.get("styleReferenceHash", ""))}]
        return values
    stopped: bool = False
    max_parallel_tasks: int = Field(default=5, ge=1, le=5)
    generation_total: int = 0
    generation_finished: int = 0
    generation_active: int = 0
    generation_queued: int = 0
    asset_modifications: dict[str, AssetModification] = Field(default_factory=dict)
    scenes: dict[str, SceneAssets] = Field(default_factory=dict)
    consistency_report: dict = Field(default_factory=dict)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
