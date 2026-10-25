from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True,
                              extra="forbid", validate_assignment=True)


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    STORY_GENERATED = "story_generated"
    STORY_APPROVED = "story_approved"
    STORYBOARD_GENERATED = "storyboard_generated"
    ASSETS_READY = "assets_ready"
    RENDERED = "rendered"
    PUBLISHED = "published"


class ProviderConfig(Model):
    provider: Nonempty
    model: Nonempty
    options: dict[str, Any] = Field(default_factory=dict)


class Manifest(Model):
    schema_version: Literal["1.0"] = "1.0"
    project_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    title: Nonempty
    language: Nonempty = "zh-TW"
    target_duration_sec: int = Field(default=600, gt=0)
    status: ProjectStatus = ProjectStatus.DRAFT
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    current_step: Nonempty = "outline"
    approved_steps: list[str] = Field(default_factory=list)
    provider_config: dict[str, ProviderConfig] = Field(default_factory=dict)
    estimated_cost: Money = Decimal("0")
    actual_cost: Money = Decimal("0")
    currency: Literal["USD"] = "USD"
    steps: dict[str, "StepState"] = Field(default_factory=dict)
    approvals: dict[str, "ApprovalRecord"] = Field(default_factory=dict)
    asset_state: Literal["not_started", "generating", "review", "approved", "stale"] = "not_started"
    asset_step: str | None = None
    asset_approvals: dict[str, "ApprovalRecord"] = Field(default_factory=dict)
    render_state: Literal["not_started", "rendering", "stopped", "failed", "review", "approved", "stale", "continuity_review"] = "not_started"
    final_approval: "ApprovalRecord | None" = None
    billing_project_id: str | None = Field(default=None, pattern=r"^proj_[A-Za-z0-9_-]+$")


class Dialogue(Model):
    character_id: Identifier
    text: Nonempty


class Scene(Model):
    scene_id: Identifier
    order: int = Field(ge=0)
    duration_sec: float = Field(gt=0, allow_inf_nan=False)
    location_id: Identifier
    character_ids: list[Identifier] = Field(default_factory=list)
    narration: str = ""
    dialogue: list[Dialogue] = Field(default_factory=list)
    action: str = ""
    shot_type: str = ""
    camera_movement: str = ""
    visual_description: str = ""
    bgm_mood: str = ""
    sfx: list[str] = Field(default_factory=list)
    asset_refs: list[Identifier] = Field(default_factory=list)
    status: Literal["draft", "approved", "generating", "ready", "failed"] = "draft"


class ScenePrompt(Model):
    """Provider-specific prompts remain separate from the core storyboard."""
    scene_id: Identifier
    provider: Nonempty
    model: Nonempty
    image_prompt: str = ""
    video_prompt: str = ""
    negative_prompt: str = ""


class GeneratedAsset(Model):
    asset_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    type: Literal["image", "video", "audio", "subtitle", "text"]
    provider: Nonempty
    model: Nonempty
    source_scene_id: Identifier | None = None
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    file_path: Nonempty
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cost: Money = Decimal("0")
    created_at: AwareDatetime = Field(default_factory=utc_now)
    generation_params: dict[str, Any] = Field(default_factory=dict)


class AudioAsset(GeneratedAsset):
    type: Literal["audio"] = "audio"
    voice_id: Nonempty


class UsageRecord(Model):
    record_id: Identifier = Field(default_factory=lambda: uuid4().hex)
    story_project_id: Identifier | None = None
    source_project_id: Identifier | None = None
    request_ids: list[str] = Field(default_factory=list)
    client_request_ids: list[str] = Field(default_factory=list)
    attribution: Literal["automatic", "legacy_assigned"] = "automatic"
    provider: Nonempty
    model: Nonempty
    step: Nonempty
    scene_id: Identifier | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)
    video_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    audio_characters: int = Field(default=0, ge=0)
    audio_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    retry_count: int = Field(default=0, ge=0)
    estimated_cost: Money = Decimal("0")
    cost: Money = Decimal("0")
    created_at: AwareDatetime = Field(default_factory=utc_now)
    pricing_known: bool = True
    outcome: Literal["succeeded", "failed"] = "succeeded"
    provider_job_id: str | None = None
    billing_units: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    billing_unit: str | None = None


class StepState(Model):
    status: Literal["pending", "running", "succeeded", "failed", "stale"] = "pending"
    input_hash: str = ""
    # Current context accepted by selective retention; input_hash keeps provenance.
    retained_input_hash: str = ""
    output_hash: str = ""
    attempts: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    error: str | None = None
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class ApprovalRecord(Model):
    content_hash: str
    approved_at: AwareDatetime = Field(default_factory=utc_now)
    note: str = ""


Manifest.model_rebuild()
