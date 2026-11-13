"""HTTP contracts; existing production contracts are in the adjacent modules."""
from typing import Any
from pydantic import BaseModel,Field


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
