import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ModelGatewayRequest(BaseModel):
    request_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    capability: str
    capability_schema_version: str
    input_refs: list[str] = Field(default_factory=list)
    bounded_input: dict[str, Any] | None = None
    privacy_class: str
    quality_tier: str
    maximum_cost: float = Field(ge=0)
    determinism_requirement: Literal["required", "preferred", "none"]
    allowed_provider_classes: list[Literal["local", "external"]]
    output_schema_id: str
    trace_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def has_input(self) -> "ModelGatewayRequest":
        if not self.input_refs and self.bounded_input is None:
            raise ValueError("input_refs or bounded_input is required")
        return self


class ModelGatewayResponse(BaseModel):
    structured_output: dict[str, Any]
    provider_adapter: str
    model_tool_id: str
    model_tool_version: str
    execution_location: str
    parameters: dict[str, Any]
    usage: dict[str, Any]
    estimated_cost: float = Field(ge=0)
    latency_ms: int = Field(ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    run_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
