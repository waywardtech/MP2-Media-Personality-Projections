import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mp2_domain.enums import ProvenanceClass, RetentionClass, RightsAccessClass, RunStatus


class MediaCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    work_type: str = Field(min_length=1, max_length=64)
    edition_label: str = Field(default="development", max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetCreate(BaseModel):
    local_path: Path
    provenance_class: ProvenanceClass
    rights_access_class: RightsAccessClass
    retention_class: RetentionClass
    mime_type: str | None = None


class AnalyzeRequest(BaseModel):
    source_asset_id: uuid.UUID
    parameters: dict[str, Any] = Field(default_factory=dict)


class MediaView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: int
    title: str
    work_type: str
    metadata_json: dict[str, Any]


class AnalysisRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    version: int
    media_work_id: uuid.UUID
    source_asset_id: uuid.UUID | None
    status: RunStatus | str
    workflow_id: str | None
    parameters: dict[str, Any]
    error: str | None
