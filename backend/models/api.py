"""HTTP contracts; existing production contracts are in the adjacent modules."""
from typing import Any
from pydantic import BaseModel,Field
from typing import Literal
from backend.models.worldview import WorldSettings


class WorldFormSave(BaseModel):
    settings: WorldSettings
    default_language: str
    expected: str


class WorldFormResolution(BaseModel):
    settings: WorldSettings
    decision: Literal['apply', 'revert']
    expected: str


class WorldAdditionCreate(BaseModel):
    text: str=Field(min_length=1)


class WorldAdditionEdit(WorldAdditionCreate):
    expected: str


class WorldAdditionResolution(BaseModel):
    decision: Literal['apply', 'revert']
    expected: str


class CommandRequest(BaseModel):
    project_id: str
    operation: str
    arguments: dict[str,Any]=Field(default_factory=dict)


class CreateWorldRequest(BaseModel):
    name: str=Field(min_length=1,max_length=200)
    premise: str=Field(min_length=1)
    language: str='zh-TW'


class DeleteRequest(BaseModel):
    confirmation: str


class ArtifactEdit(BaseModel):
    value: Any
    expected: str


class KeysUpdate(BaseModel):
    values: dict[str,str]
