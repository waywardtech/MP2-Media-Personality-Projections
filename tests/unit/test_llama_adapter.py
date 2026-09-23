"""LlamaCppAdapter contract, with emphasis on provenance correctness.

The bug these guard against actually happened on DEV-01: the gateway's environment still
named a 7B model while llama-server was serving a 1.5B, and MP2 recorded the configured
model rather than the one that produced the claims. Evidence that names the wrong model is
not reproducible, which defeats the point of recording lineage at all.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mp2_model_gateway.adapters import LlamaCppAdapter
from mp2_schemas.gateway import ModelGatewayRequest
from mp2_schemas.scene_packet import EVIDENCE_SCHEMA_VERSION

VALID_OUTPUT = {
    "schema_version": EVIDENCE_SCHEMA_VERSION,
    "claims": [{
        "claim_type": "scene.descriptor",
        "structured_value": {"duration_s": 4.3},
        "confidence": 0.8,
        "evidence_refs": ["mp2:segment/abc"],
    }],
}


def _request(**overrides) -> ModelGatewayRequest:
    base = dict(
        capability="scene_semantic_interpretation",
        capability_schema_version="1",
        bounded_input={"scene": {"segment_id": "abc"}},
        privacy_class="internal_test",
        quality_tier="draft",
        maximum_cost=0,
        determinism_requirement="preferred",
        allowed_provider_classes=["local"],
        output_schema_id=EVIDENCE_SCHEMA_VERSION,
        trace_id="t",
    )
    base.update(overrides)
    return ModelGatewayRequest(**base)  # type: ignore[arg-type]


def _transport(served_model: str, captured: dict | None = None) -> httpx.MockTransport:
    def handler(http_request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.update(json.loads(http_request.content))
        return httpx.Response(200, json={
            "model": served_model,
            "choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(VALID_OUTPUT)}}],
            "usage": {"completion_tokens": 42, "prompt_tokens": 100},
        })

    return httpx.MockTransport(handler)


@pytest.fixture
def patched(monkeypatch):
    """Route the adapter's httpx client at a mock transport."""
    def install(transport: httpx.MockTransport) -> None:
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    return install


async def test_records_the_model_the_server_actually_served(patched) -> None:
    patched(_transport("qwen2.5-1.5b-instruct-q4_k_m"))
    adapter = LlamaCppAdapter("http://llama", "qwen2.5-7b-instruct-q4_k_m")

    response = await adapter.execute(_request())

    # Configuration said 7B; the server served 1.5B. Provenance must follow the server.
    assert response.model_tool_id == "qwen2.5-1.5b-instruct-q4_k_m"
    assert any("served" in w for w in response.warnings), (
        "a configured/served mismatch must be surfaced, not silently corrected"
    )


async def test_no_warning_when_configuration_matches(patched) -> None:
    patched(_transport("qwen2.5-1.5b-instruct-q4_k_m"))
    adapter = LlamaCppAdapter("http://llama", "qwen2.5-1.5b-instruct-q4_k_m")

    response = await adapter.execute(_request())

    assert response.model_tool_id == "qwen2.5-1.5b-instruct-q4_k_m"
    assert response.warnings == []


async def test_falls_back_to_configured_id_when_server_reports_none(patched) -> None:
    patched(_transport(""))
    adapter = LlamaCppAdapter("http://llama", "configured-model")

    response = await adapter.execute(_request())

    assert response.model_tool_id == "configured-model"


async def test_decoding_is_grammar_constrained_to_the_mp2_contract(patched) -> None:
    captured: dict = {}
    patched(_transport("m", captured))
    await LlamaCppAdapter("http://llama", "m").execute(_request())

    fmt = captured["response_format"]
    assert fmt["type"] == "json_schema", "MP2 owns this contract; decoding must be bound to it"
    assert fmt["json_schema"]["schema"]["properties"]["claims"]


async def test_unknown_output_contract_falls_back_and_warns(patched) -> None:
    captured: dict = {}
    patched(_transport("m", captured))

    response = await LlamaCppAdapter("http://llama", "m").execute(
        _request(output_schema_id="something-mp2-does-not-own/9"))

    assert captured["response_format"]["type"] == "json_object"
    assert any("not grammar-constrained" in w for w in response.warnings)


async def test_determinism_requirement_suppresses_sampling(patched) -> None:
    captured: dict = {}
    patched(_transport("m", captured))
    await LlamaCppAdapter("http://llama", "m").execute(
        _request(determinism_requirement="required"))

    assert captured["temperature"] == 0.0


async def test_truncation_is_reported(patched) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "model": "m",
            "choices": [{"finish_reason": "length",
                         "message": {"content": json.dumps(VALID_OUTPUT)}}],
            "usage": {},
        })

    patched(httpx.MockTransport(handler))
    response = await LlamaCppAdapter("http://llama", "m").execute(_request())
    assert any("truncated" in w for w in response.warnings)


async def test_rejects_requests_that_disallow_local(patched) -> None:
    patched(_transport("m"))
    with pytest.raises(PermissionError):
        await LlamaCppAdapter("http://llama", "m").execute(
            _request(allowed_provider_classes=["external"]))


async def test_prompt_payload_is_not_duplicated_into_parameters(patched) -> None:
    patched(_transport("m"))
    response = await LlamaCppAdapter("http://llama", "m").execute(_request())
    # The packet is already stored as evidence; it must not be copied into lineage too.
    assert "messages" not in response.parameters
    assert response.estimated_cost == 0
    assert response.execution_location == "local"
