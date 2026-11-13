"""Animation direction is a sidecar: existing story schemas remain compatible."""
from typing import Literal
from pydantic import Field, AliasChoices, model_validator
from .core import Model, Identifier, ApprovalRecord, GeneratedAsset


class VoiceProfile(Model):
    character_id: Identifier
    voice_id: str = ""
    gender_presentation: str = "unspecified; select by listening"
    age_range: str = ""
    pitch_range: str = "natural"
    accent: str = ""
    speaking_style: str = ""
    baseline_emotion: str = "neutral"
    pace: float = Field(default=1, ge=.5, le=2)


class Performance(Model):
    scene_id: Identifier
    line_index: int = Field(ge=0)
    character_id: Identifier
    text: str
    audio_tags: list[str] = Field(default_factory=list)
    emotion: str = "neutral"
    intensity: float = Field(default=.5, ge=0, le=1, validation_alias=AliasChoices("intensity", "emotionIntensity", "emotion_intensity"))
    pace: float = Field(default=1, ge=.5, le=2)
    pause_before: float = Field(default=0, ge=0, le=3)
    pause_after: float = Field(default=.15, ge=0, le=3)
    subtext: str = ""
    gesture: str = ""
    facial_expression: str = ""
    interruption: bool = False
    hesitation: bool = False
    volume: float = Field(default=1, ge=0, le=2)
    body_action: str = ""
    eye_direction: str = ""
    camera_reaction: str = ""
    sfx_cue: str = ""

    @model_validator(mode="before")
    @classmethod
    def milliseconds(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            for key, target, alias in (("pauseBeforeMs", "pause_before", "pauseBefore"), ("pauseAfterMs", "pause_after", "pauseAfter")):
                if key in value:
                    if target in value or alias in value:
                        raise ValueError("Specify a pause in seconds or milliseconds, not both")
                    milliseconds = value.pop(key)
                    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)):
                        raise ValueError("Pause milliseconds must be numeric")
                    value[target] = milliseconds / 1000
        return value


class LocationBible(Model):
    location_id: Identifier
    description: str
    layout: str = "Preserve the approved background's spatial arrangement"
    entrances: str = "Preserve reference positions"
    fixed_props: str = "Preserve reference objects"
    lighting: str = "Match approved background"
    time_of_day: str = "Match approved background"
    palette: str = "Match approved reference palette"
    camera_axis: str = "Preserve screen direction and the 180-degree axis"


class ShotPlan(Model):
    shot_id: Identifier
    scene_id: Identifier
    order: int = Field(ge=1)
    action: str
    camera: str
    camera_start_angle: float | None = Field(default=None, allow_inf_nan=False)
    camera_end_angle: float | None = Field(default=None, allow_inf_nan=False)
    camera_pitch: float = Field(default=0,ge=-89,le=89,allow_inf_nan=False)
    camera_fov: float = Field(default=70,gt=0,lt=170,allow_inf_nan=False)
    target_area: str = ""
    character_position: dict[str,float] | str | None = None
    start_pose: str = "Match approved scene image or the previous shot's cut frame"
    end_pose: str = "Finish the action naturally; preserve character proportions and costume"
    performance_video: str = ""
    last_frame: str = ""
    performance_character_id: str = ""
    expression_intensity: int = Field(default=3, ge=1, le=5)
    chain_previous: bool = True
    overlap_seconds: float = Field(default=1, ge=.5, le=1.5)
    interaction_target: str = ""
    costume_ids: list[Identifier] = Field(default_factory=list)
    prop_ids: list[Identifier] = Field(default_factory=list)
    sfx_cues: list[str] = Field(default_factory=list)
    ambience_cues: list[str] = Field(default_factory=list)


class AnimationPlan(Model):
    source_hash: str
    voices: dict[str, VoiceProfile]
    locations: dict[str, LocationBible]
    performances: list[Performance]
    shots: list[ShotPlan]


class DirectionEdit(Model):
    """List-based API view avoids dynamic object keys in strict JSON Schema."""
    source_hash: str
    voices: list[VoiceProfile]
    locations: list[LocationBible]
    performances: list[Performance]
    shots: list[ShotPlan]


class VoicePreview(Model):
    profile_hash: str
    asset: GeneratedAsset
    file_hash: str
    approval: ApprovalRecord | None = None


class AnimatedShot(Model):
    shot_id: Identifier
    scene_id: Identifier
    status: Literal["running", "succeeded", "failed"] = "running"
    input_hash: str = ""
    asset: GeneratedAsset | None = None
    file_hash: str = ""
    end_frame: str | None = None
    end_frame_hash: str = ""
    duration: float = 0
    revision: int = 0
    attempts: int = 0
    error: str | None = None
    modification_prompt: str = ""
    modification_source: str | None = None
    remote_job_id: str | None = None
    remote_job_key: str = ""
    remote_status: str | None = None
    remote_progress: int = 0


class AnimationState(Model):
    scene_approvals: dict[str, ApprovalRecord] = Field(default_factory=dict)
    enabled: bool = True
    plan: AnimationPlan
    plan_approval: ApprovalRecord | None = None
    previews: dict[str, VoicePreview] = Field(default_factory=dict)
    emotion_previews: dict[str, dict[str, VoicePreview]] = Field(default_factory=dict)
    performance_approval: ApprovalRecord | None = None
    shots: dict[str, AnimatedShot] = Field(default_factory=dict)
    continuity_approval: ApprovalRecord | None = None
