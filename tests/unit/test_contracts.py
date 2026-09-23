from pathlib import Path

import pytest
from pydantic import ValidationError

from mp2_schemas.api import AssetCreate
from mp2_schemas.evidence import EvidenceClaimOutput
from mp2_schemas.gateway import ModelGatewayRequest
from mp2_schemas.scene_packet import ScenePacket, SceneTimeRange
from mp2_storage import LocalObjectStore


def _range(**overrides: object) -> SceneTimeRange:
    base = {"segment_id": "seg-1", "index": 0, "start_ms": 0, "end_ms": 1000}
    return SceneTimeRange(**{**base, **overrides})  # type: ignore[arg-type]


def test_scene_time_cannot_be_inverted() -> None:
    with pytest.raises(ValidationError):
        _range(start_ms=1000, end_ms=10)


def test_zero_length_scene_is_permitted() -> None:
    # Deliberate: a source whose duration cannot be determined must not fail the run.
    assert _range(start_ms=0, end_ms=0).duration_s == 0.0


def test_gateway_requires_input() -> None:
    with pytest.raises(ValidationError):
        ModelGatewayRequest(
            capability="scene-evidence", capability_schema_version="1",
            privacy_class="private", quality_tier="local", maximum_cost=0,
            determinism_requirement="preferred", allowed_provider_classes=["local"],
            output_schema_id="evidence/1", trace_id="trace",
        )


def test_gateway_rejects_negative_cost() -> None:
    with pytest.raises(ValidationError):
        ModelGatewayRequest(
            capability="scene-evidence", capability_schema_version="1",
            bounded_input={"scene": "x"}, privacy_class="private", quality_tier="local",
            maximum_cost=-1, determinism_requirement="preferred",
            allowed_provider_classes=["local"], output_schema_id="evidence/1",
            trace_id="trace",
        )


def test_scene_packet_carries_no_genome_or_provider_fields() -> None:
    """The packet is what a model sees. It must leak neither conclusions nor vendors."""
    packet = ScenePacket(analysis_run_id="r1", source_asset_id="a1", scene=_range())
    dumped = packet.model_dump()
    serialised = str(dumped).lower()
    for forbidden in ("genome", "projection", "openai", "anthropic", "gpt", "claude"):
        assert forbidden not in serialised


def test_scene_packet_carries_no_media_bytes() -> None:
    packet = ScenePacket(analysis_run_id="r1", source_asset_id="a1", scene=_range())
    for value in packet.model_dump().values():
        assert not isinstance(value, (bytes, bytearray))


def test_scene_packet_rejects_unknown_fields() -> None:
    # extra="forbid" keeps provider-specific fields from being smuggled into the contract.
    with pytest.raises(ValidationError):
        ScenePacket(analysis_run_id="r1", source_asset_id="a1", scene=_range(),
                    openai_model="gpt-4")  # type: ignore[call-arg]


def test_evidence_claim_must_cite_evidence() -> None:
    with pytest.raises(ValidationError):
        EvidenceClaimOutput(claim_type="scene.descriptor", structured_value={},
                            confidence=0.5, evidence_refs=[])


def test_rights_metadata_is_mandatory() -> None:
    with pytest.raises(ValidationError):
        AssetCreate(local_path=Path("fixture.mp4"))  # type: ignore[call-arg]


def test_local_object_store_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "fixture.bin"
    source.write_bytes(b"mp2-synthetic-fixture")
    store = LocalObjectStore(tmp_path / "objects")
    first = store.put_file("mp2-raw", "fixture/data.bin", source)
    second = store.put_file("mp2-raw", "fixture/data.bin", source)
    assert first == second
    assert store.open("mp2-raw", "fixture/data.bin").read() == source.read_bytes()


def test_local_object_store_blocks_traversal(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError):
        store.exists("mp2-raw", "../../escape")
