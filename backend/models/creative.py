"""Provider-neutral, validated contracts for each creative module."""
from typing import Literal

from pydantic import Field, model_validator, model_serializer

from .core import Dialogue, Identifier, Model, Nonempty, Scene


class Location(Model):
    location_id: Identifier
    name: Nonempty
    description: Nonempty


class World(Model):
    era: Nonempty
    rules: list[Nonempty] = Field(min_length=1)
    technology_magic_rules: list[str]
    organizations: list[str]
    locations: list[Location] = Field(min_length=1)
    timeline: list[str]
    forbidden_contradictions: list[str]

    @model_validator(mode="after")
    def unique_locations(self):
        unique([item.location_id for item in self.locations], "location IDs")
        return self


class Character(Model):
    character_id: Identifier
    name: Nonempty
    age: int = Field(ge=0)
    appearance: Nonempty
    clothing: Nonempty
    personality: Nonempty
    background: Nonempty
    speaking_style: Nonempty
    voice_id: Nonempty
    reference_image: str | None
    immutable_traits: list[Nonempty] = Field(min_length=1)


class Characters(Model):
    characters: list[Character] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_characters(self):
        unique([item.character_id for item in self.characters], "character IDs")
        return self


class Story(Model):
    title: Nonempty
    target_duration_sec: int = Field(gt=0)
    hook: Nonempty
    setup: Nonempty
    conflict: Nonempty
    escalation: Nonempty
    twist: Nonempty
    climax: Nonempty
    ending: Nonempty


class ReviewIssue(Model):
    severity: Literal["info", "warning", "error"]
    category: Literal["logic", "character", "appearance", "world", "pacing", "foreshadowing", "repetition"]
    description: Nonempty
    suggestion: Nonempty


class ReviewReport(Model):
    summary: Nonempty
    issues: list[ReviewIssue]
    recommendation: Literal["approve", "revise"]


class ScriptScene(Model):
    scene_id: Identifier
    location_id: Identifier
    character_ids: list[Identifier]
    purpose: Nonempty
    narration: str
    narration_audio_tags: list[Nonempty] = Field(default_factory=list)
    sfx: list[Nonempty] = Field(default_factory=list)
    dialogue: list[Dialogue]
    action: Nonempty
    estimated_duration_sec: float = Field(gt=0, allow_inf_nan=False)


class Script(Model):
    title: Nonempty
    scenes: list[ScriptScene] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_scenes(self):
        unique([item.scene_id for item in self.scenes], "scene IDs")
        return self


class StoryboardProp(Model):
    prop_id: Identifier
    title: Nonempty
    description: Nonempty
    modes: list[Nonempty] = Field(default_factory=lambda:["default"],min_length=1)


class StoryboardBGM(Model):
    bgm_id: Identifier
    title: Nonempty
    description: Nonempty


class StoryboardDetailBackground(Model):
    location_id: Identifier
    detail_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    area: Nonempty
    description: Nonempty


class Storyboard(Model):
    scenes: list[Scene] = Field(min_length=1)
    backgrounds: list[Location] = Field(default_factory=list)
    props: list[StoryboardProp] = Field(default_factory=list)
    bgm: list[StoryboardBGM] = Field(default_factory=list)
    detail_backgrounds: list[StoryboardDetailBackground] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_details(self,handler):
        data = handler(self)
        if not self.detail_backgrounds:
            # Adding an optional contract must not change hashes of saved
            # storyboards and prompts which predate detail declarations.
            data.pop("detailBackgrounds",None)
            data.pop("detail_backgrounds",None)
        return data

    @model_validator(mode="after")
    def unique_asset_ids(self):
        unique([item.location_id for item in self.backgrounds],"background IDs")
        unique([item.prop_id for item in self.props],"prop IDs")
        unique([item.bgm_id for item in self.bgm],"BGM IDs")
        unique([(item.location_id,item.detail_id) for item in self.detail_backgrounds],"detail background IDs")
        return self

    @model_validator(mode="after")
    def unique_scenes(self):
        unique([item.scene_id for item in self.scenes], "scene IDs")
        if [s.order for s in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Storyboard orders must be consecutive starting at 1")
        return self


class PromptItem(Model):
    scene_id: Identifier
    provider: Nonempty
    model: Nonempty
    image_prompt: Nonempty
    video_prompt: Nonempty
    negative_prompt: Nonempty


class Prompts(Model):
    prompts: list[PromptItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_prompts(self):
        unique([item.scene_id for item in self.prompts], "prompt scene IDs")
        return self


def unique(values: list[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"Duplicate {label}")
