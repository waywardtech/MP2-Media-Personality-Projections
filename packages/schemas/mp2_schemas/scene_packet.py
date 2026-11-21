"""Scene packet: the bounded, provider-neutral input handed to a semantic model.

A scene packet never contains media bytes and never names a provider or model. It is the
only thing the Model Gateway is allowed to send to a generative instrument, which keeps
raw source media out of external systems by construction.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENE_PACKET_SCHEMA_VERSION: Final = "scene-packet/1"
# Final so the value narrows to a Literal and can pin the output contract below.
EVIDENCE_SCHEMA_VERSION: Final = "scene-evidence/1"


class SceneTimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str
    index: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self) -> SceneTimeRange:
        # A zero-length range is permitted so an odd but valid source - for example an
        # asset whose duration ffprobe cannot determine - does not fail the whole
        # analysis run. An inverted range is always a bug.
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must not be earlier than start_ms")
        return self

    @property
    def duration_s(self) -> float:
        return max(self.end_ms - self.start_ms, 0) / 1000.0


class ScenePacket(BaseModel):
    """MP2-owned contract. Field names are domain terms, never provider terms."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCENE_PACKET_SCHEMA_VERSION
    analysis_run_id: str
    source_asset_id: str
    scene: SceneTimeRange

    transcript_text: str = ""
    utterances: list[dict[str, Any]] = Field(default_factory=list)

    visual_measurements: dict[str, Any] = Field(default_factory=dict)
    audio_measurements: dict[str, Any] = Field(default_factory=dict)
    dialogue_measurements: dict[str, Any] = Field(default_factory=dict)

    representative_frame_refs: list[str] = Field(default_factory=list)
    frame_embedding_refs: list[str] = Field(default_factory=list)

    preceding_context_summary: str | None = None
    following_context_summary: str | None = None

    # Only aliases explicitly supplied by an operator; MP2 never infers identity.
    known_character_aliases: list[str] = Field(default_factory=list)

    schema_instruction: str = (
        "Return JSON only, matching the declared output schema. Base every claim on the "
        "supplied measurements and transcript. Do not infer speaker identity. Do not "
        "invent facts that are not supported by the packet. Express uncertainty with the "
        "confidence field rather than by hedging in prose."
    )


class SceneEvidenceClaim(BaseModel):
    """One structured, evidence-bearing statement about a scene.

    Semantic output lands here as an evidence claim. It never writes canonical genome
    values; genome calculation consumes evidence in a later milestone.
    """

    model_config = ConfigDict(extra="forbid")

    claim_type: str = Field(min_length=1, max_length=255)
    structured_value: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    # Non-empty by contract. A claim that cannot say what it rests on is not evidence, and
    # because the local runtime decodes against this schema, the grammar itself forces a
    # citation rather than relying on the model to volunteer one.
    evidence_refs: list[str] = Field(min_length=1)


class SceneEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Pinned: a model asked to emit this contract must not echo the input packet's
    # schema_version, which is exactly what an unconstrained field invites.
    schema_version: Literal["scene-evidence/1"] = EVIDENCE_SCHEMA_VERSION
    claims: list[SceneEvidenceClaim] = Field(default_factory=list)


# --- Output schema registry -------------------------------------------------------------
# A gateway request names the contract it wants by id. Resolving that id to a JSON Schema
# lets a local runtime constrain decoding, which makes schema validity a property of the
# grammar rather than something the model is merely asked for.

OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    EVIDENCE_SCHEMA_VERSION: SceneEvidenceOutput,
}


def json_schema_for(output_schema_id: str) -> dict[str, Any] | None:
    """JSON Schema for a declared output contract, or None if MP2 does not own it."""
    model = OUTPUT_SCHEMAS.get(output_schema_id)
    return model.model_json_schema() if model is not None else None
