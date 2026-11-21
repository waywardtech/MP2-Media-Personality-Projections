"""The deterministic local adapter must honour the gateway contract exactly.

This is the adapter that keeps the semantic boundary testable without model weights, so
its contract compliance is load-bearing for the whole evidence path.
"""

from __future__ import annotations

import pytest

from mp2_model_gateway.baseline import DeterministicBaselineAdapter
from mp2_schemas.gateway import ModelGatewayRequest
from mp2_schemas.scene_packet import (
    EVIDENCE_SCHEMA_VERSION,
    SceneEvidenceOutput,
    ScenePacket,
    SceneTimeRange,
)

RUN = "11111111-1111-1111-1111-111111111111"
ASSET = "22222222-2222-2222-2222-222222222222"
SEGMENT = "33333333-3333-3333-3333-333333333333"


def _packet() -> ScenePacket:
    return ScenePacket(
        analysis_run_id=RUN,
        source_asset_id=ASSET,
        scene=SceneTimeRange(segment_id=SEGMENT, index=0, start_ms=0, end_ms=4000),
        transcript_text="hello there",
        utterances=[{"index": 0, "start_s": 0.1, "end_s": 1.2, "text": "hello there"}],
        visual_measurements={"frames_sampled": 3, "luminance_mean": 120.0,
                             "saturation_mean": 90.0, "edge_density": 0.08,
                             "motion_magnitude_mean": 1.1},
        audio_measurements={"samples": 64000, "silence_ratio": 0.2, "rms_mean": 0.12,
                            "spectral_centroid_mean": 1500.0, "tempo_candidate": 120.0,
                            "dynamic_range_proxy": 0.4},
        dialogue_measurements={"utterance_count": 1, "word_count": 2,
                               "dialogue_density": 0.27, "words_per_minute": 30.0},
    )


def _request(packet: ScenePacket) -> ModelGatewayRequest:
    return ModelGatewayRequest(
        capability="scene_semantic_interpretation",
        capability_schema_version="1",
        bounded_input=packet.model_dump(mode="json"),
        privacy_class="internal_test",
        quality_tier="draft",
        maximum_cost=0,
        determinism_requirement="preferred",
        allowed_provider_classes=["local"],
        output_schema_id=EVIDENCE_SCHEMA_VERSION,
        trace_id=RUN,
    )


async def test_returns_schema_valid_evidence() -> None:
    response = await DeterministicBaselineAdapter().execute(_request(_packet()))

    # The declared output schema must actually parse, not merely look similar.
    parsed = SceneEvidenceOutput.model_validate(response.structured_output)
    assert parsed.claims
    for claim in parsed.claims:
        assert 0.0 <= claim.confidence <= 1.0
        assert claim.evidence_refs, "every claim must cite its evidence"
        assert f"mp2:segment/{SEGMENT}" in claim.evidence_refs

    assert response.execution_location == "local"
    assert response.estimated_cost == 0
    assert len(response.content_hash) == 64


async def test_is_repeatable_for_identical_input() -> None:
    adapter = DeterministicBaselineAdapter()
    first = await adapter.execute(_request(_packet()))
    second = await adapter.execute(_request(_packet()))
    # D0: identical input yields an identical content hash. run_id/latency intentionally differ.
    assert first.content_hash == second.content_hash
    assert first.structured_output == second.structured_output


async def test_rejects_requests_that_disallow_local_execution() -> None:
    request = _request(_packet())
    request.allowed_provider_classes = ["external"]
    with pytest.raises(PermissionError):
        await DeterministicBaselineAdapter().execute(request)


async def test_missing_measurements_are_reported_not_invented() -> None:
    packet = _packet()
    packet.visual_measurements = {}
    packet.audio_measurements = {}
    response = await DeterministicBaselineAdapter().execute(_request(packet))

    claim_types = {c["claim_type"] for c in response.structured_output["claims"]}
    assert "scene.visual_character" not in claim_types
    assert "scene.audio_character" not in claim_types
    assert response.warnings, "absent measurements must surface as warnings"
