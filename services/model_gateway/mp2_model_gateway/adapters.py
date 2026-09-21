from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Protocol

import httpx

from mp2_schemas.gateway import ModelGatewayRequest, ModelGatewayResponse


class ModelAdapter(Protocol):
    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse: ...


class LlamaCppAdapter:
    def __init__(self, base_url: str, model_id: str, model_version: str = "local-file"):
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.model_version = model_version

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        if "local" not in request.allowed_provider_classes:
            raise PermissionError("request does not allow local providers")
        started = time.perf_counter()
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system",
                 "content": "Return JSON matching the requested MP2 schema."},
                {"role": "user", "content": json.dumps(request.bounded_input)},
            ],
            "temperature": request.parameters.get("temperature", 0),
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=120) as client:
            result = await client.post(f"{self.base_url}/v1/chat/completions", json=body)
            result.raise_for_status()
            raw = result.json()
        output = json.loads(raw["choices"][0]["message"]["content"])
        canonical = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
        return ModelGatewayResponse(
            structured_output=output, provider_adapter="llama.cpp",
            model_tool_id=self.model_id, model_tool_version=self.model_version,
            execution_location="local", parameters=body, usage=raw.get("usage", {}),
            estimated_cost=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            confidence=None, warnings=[], run_id=str(uuid.uuid4()),
            content_hash=hashlib.sha256(canonical).hexdigest(),
        )


class DisabledExternalAdapter:
    """Provider-neutral OpenAI-compatible boundary.

    Deliberately disabled without explicit policy approval and credentials.
    """

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        raise PermissionError("external model routing is disabled by policy")
