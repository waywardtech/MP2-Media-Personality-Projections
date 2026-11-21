from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Protocol

import httpx

from mp2_schemas.gateway import ModelGatewayRequest, ModelGatewayResponse
from mp2_schemas.scene_packet import json_schema_for


class ModelAdapter(Protocol):
    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse: ...


SYSTEM_PROMPT = (
    "You are a scene analyst for a media-evidence system. You receive measurements and "
    "transcript text for one scene and return structured evidence claims.\n"
    "Rules:\n"
    "1. Base every claim on the supplied measurements and transcript. Do not invent facts.\n"
    "2. Do not infer or assert the identity of any real person, and do not describe or "
    "speculate about anyone's body, appearance or characteristics as a person.\n"
    "3. Describe the SCENE - its craft, mood, pacing and content - not the viewer and not "
    "the performers as individuals.\n"
    "4. Express uncertainty with the confidence field, not with hedging prose.\n"
    "5. Cite what each claim rests on in evidence_refs.\n"
    "6. Do not produce psychological or personality conclusions. Those are calculated "
    "later from evidence, never asserted here."
)


class LlamaCppAdapter:
    """Local generative adapter.

    Output validity is enforced by **grammar-constrained decoding**: the gateway resolves
    the request's declared output schema and hands llama.cpp a JSON Schema, so the sampler
    cannot emit a non-conforming token. Schema conformance therefore does not depend on
    model size or prompt obedience - only claim *quality* does.
    """

    def __init__(self, base_url: str, model_id: str, model_version: str = "local-file",
                 timeout_s: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.model_version = model_version
        self.timeout_s = timeout_s

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        if "local" not in request.allowed_provider_classes:
            raise PermissionError("request does not allow local providers")
        started = time.perf_counter()

        schema = json_schema_for(request.output_schema_id)
        if schema is not None:
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {"name": "mp2_scene_evidence", "strict": True,
                                "schema": schema},
            }
        else:
            # MP2 does not own this contract, so the grammar cannot be derived. Fall back
            # to plain JSON mode and flag it: validity is no longer guaranteed.
            response_format = {"type": "json_object"}

        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request.bounded_input)},
            ],
            # Deterministic by default: a determinism_requirement of "required" or
            # "preferred" must not sample.
            "temperature": request.parameters.get(
                "temperature", 0.0 if request.determinism_requirement != "none" else 0.7),
            "top_p": request.parameters.get("top_p", 1.0),
            "seed": request.parameters.get("seed", 0),
            "max_tokens": request.parameters.get("max_tokens", 1024),
            "response_format": response_format,
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            result = await client.post(f"{self.base_url}/v1/chat/completions", json=body)
            result.raise_for_status()
            raw = result.json()

        content = raw["choices"][0]["message"]["content"]
        output = json.loads(content)

        warnings: list[str] = []
        if schema is None:
            warnings.append(
                f"no MP2 schema registered for {request.output_schema_id}; "
                "decoding was not grammar-constrained")
        if raw["choices"][0].get("finish_reason") == "length":
            warnings.append("output truncated at max_tokens")

        # Provenance comes from the server, not from our configuration. A gateway whose
        # environment has drifted from the running llama-server would otherwise record a
        # model that never produced the claim, which makes the evidence unreproducible -
        # the one thing lineage exists to prevent. This is a real drift that occurred:
        # the gateway was configured for a 7B while the server was serving a 1.5B.
        served_model = str(raw.get("model") or "").strip() or self.model_id
        if served_model != self.model_id:
            warnings.append(
                f"configured model '{self.model_id}' but llama-server served "
                f"'{served_model}'; recording the served model")

        canonical = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
        return ModelGatewayResponse(
            structured_output=output, provider_adapter="llama.cpp",
            model_tool_id=served_model, model_tool_version=self.model_version,
            execution_location="local",
            # Record the sampling parameters, not the prompt payload: the packet is
            # already stored as evidence and does not belong in a second place.
            parameters={k: v for k, v in body.items() if k != "messages"},
            usage=raw.get("usage", {}),
            estimated_cost=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            confidence=None, warnings=warnings, run_id=str(uuid.uuid4()),
            content_hash=hashlib.sha256(canonical).hexdigest(),
        )


class DisabledExternalAdapter:
    """Provider-neutral OpenAI-compatible boundary.

    Deliberately disabled without explicit policy approval and credentials.
    """

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        raise PermissionError("external model routing is disabled by policy")
