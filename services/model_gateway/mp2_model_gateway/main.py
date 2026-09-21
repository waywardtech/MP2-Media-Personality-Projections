"""MP2 Model Gateway.

Every generative or external-model call in MP2 crosses this boundary. Callers describe a
capability, a privacy class and a policy; they never name a provider, a vendor model or an
SDK object. Routing is the gateway's concern alone, which is what makes models replaceable.
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException

from mp2_schemas.gateway import ModelGatewayRequest, ModelGatewayResponse

from .adapters import DisabledExternalAdapter, LlamaCppAdapter, ModelAdapter
from .baseline import DeterministicBaselineAdapter

app = FastAPI(title="MP2 Model Gateway", version="0.1.0")


def external_enabled() -> bool:
    return os.getenv("MP2_EXTERNAL_MODELS_ENABLED", "false").lower() == "true"


def local_generative_configured() -> bool:
    """A llama.cpp server is only routed to when a model has actually been configured."""
    return os.getenv("MP2_LLAMA_MODEL_ID", "unset") not in ("", "unset")


def select_adapter(request: ModelGatewayRequest) -> ModelAdapter:
    """Policy-driven routing. Local is always preferred; external needs explicit enablement."""
    if "local" in request.allowed_provider_classes:
        if local_generative_configured():
            return LlamaCppAdapter(
                os.getenv("MP2_LLAMA_BASE_URL", "http://llama-server:8080"),
                os.getenv("MP2_LLAMA_MODEL_ID", "unset"),
            )
        return DeterministicBaselineAdapter()
    if "external" in request.allowed_provider_classes:
        if not external_enabled():
            raise PermissionError("external model routing is disabled by policy")
        return DisabledExternalAdapter()
    raise PermissionError("no permitted provider class for this request")


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, object]:
    return {
        "status": "ready",
        "external_models_enabled": external_enabled(),
        "local_generative_configured": local_generative_configured(),
        "default_local_adapter": (
            "llama.cpp" if local_generative_configured() else "mp2-deterministic-baseline"
        ),
    }


@app.get("/v1/capabilities")
def capabilities() -> dict[str, object]:
    """Provider-neutral description of what the gateway can currently do."""
    return {
        "capabilities": ["scene_semantic_interpretation"],
        "provider_classes": {
            "local": True,
            "external": external_enabled(),
        },
        "adapters": [
            {"id": "mp2-deterministic-baseline", "class": "local", "available": True,
             "determinism": "D0", "generative": False},
            {"id": "llama.cpp", "class": "local",
             "available": local_generative_configured(), "determinism": "D2",
             "generative": True},
            {"id": "external-openai-compatible", "class": "external",
             "available": external_enabled(), "determinism": "D2", "generative": True},
        ],
    }


@app.post("/v1/execute", response_model=ModelGatewayResponse)
async def execute(request: ModelGatewayRequest) -> ModelGatewayResponse:
    if request.maximum_cost != 0 and not external_enabled():
        raise HTTPException(status_code=403,
                            detail="non-zero maximum_cost requires external models to be enabled")
    try:
        adapter = select_adapter(request)
        response: ModelGatewayResponse = await adapter.execute(request)
        return response
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
