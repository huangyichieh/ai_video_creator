"""Lip-sync and generated effect layers, kept separate from approved story assets."""
from typing import Literal
from pydantic import Field, model_validator
from .models import Model, Identifier, GeneratedAsset, Money


class FaceTarget(Model):
    scene_id: Identifier
    character_id: Identifier
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    subject: Identifier | None = None


class EffectLayer(Model):
    effect_id: Identifier
    scene_id: Identifier
    kind: Literal["rain", "snow", "smoke", "sparks", "fire", "lightning", "energy", "hud", "openai"] = "rain"
    prompt: str = ""
    at: float = Field(default=0, ge=0, allow_inf_nan=False)
    duration: float = Field(default=3, gt=0, le=30, allow_inf_nan=False)
    opacity: float = Field(default=.5, ge=0, le=1)
    intensity: float = Field(default=.5, gt=0, le=1)
    seed: int = Field(default=1, ge=0, le=2**31-1)
    enabled: bool = True

    @model_validator(mode="after")
    def prompt_required(self):
        if self.kind == "openai" and not self.prompt.strip():
            raise ValueError("Describe the effect before generating an OpenAI VFX layer")
        return self


class FinishingPlan(Model):
    lip_sync: bool = False
    lip_model: Literal["lipsync-2", "lipsync-2-pro"] = "lipsync-2"
    sound_provider: Literal["local", "elevenlabs"] = "local"
    face_targets: list[FaceTarget] = Field(default_factory=list)
    effects: list[EffectLayer] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({effect.effect_id for effect in self.effects}) != len(self.effects):
            raise ValueError("Use unique effect IDs")
        if len({(face.scene_id, face.character_id, face.subject) for face in self.face_targets}) != len(self.face_targets):
            raise ValueError("Choose only one face target per scene and character")
        return self


class FinishingRecord(Model):
    key: str
    scene_id: Identifier
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    input_hash: str = ""
    file_hash: str = ""
    asset: GeneratedAsset | None = None
    revision: int = 0
    attempts: int = 0
    error: str | None = None
    job_id: str | None = None
    job_status: str = ""
    progress: int = 0
    idempotency_key: str = ""
    work_dir: str = ""
    charge_recorded: bool = False
    recorded_cost: Money = 0
    pricing_known: bool = True
    modification_source: str | None = None
    modification_prompt: str = ""


class FinishingState(Model):
    plan: FinishingPlan = Field(default_factory=FinishingPlan)
    records: dict[str, FinishingRecord] = Field(default_factory=dict)
