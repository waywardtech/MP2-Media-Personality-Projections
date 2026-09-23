"""Evidence output contracts.

The canonical scene packet lives in `mp2_schemas.scene_packet`. This module holds the
evidence-claim shape that a semantic adapter is expected to return.
"""

from typing import Any

from pydantic import BaseModel, Field


class EvidenceClaimOutput(BaseModel):
    """A single structured claim produced by a semantic adapter.

    `evidence_refs` is mandatory and non-empty: a claim that cannot cite what it was
    derived from is not evidence, and MP2 will not store one.
    """

    claim_type: str
    structured_value: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
