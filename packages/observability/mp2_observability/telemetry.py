"""OpenTelemetry-compatible instrumentation for MP2 services.

Design rules:

* **Optional by construction.** If the OTel SDK is not installed, or no OTLP endpoint is
  configured, every function here becomes a no-op. A service must never fail to start, and
  must never slow down, because telemetry is unavailable.
* **Vendor-neutral.** MP2 emits OTLP and nothing else. No observability vendor SDK is
  imported anywhere, so the collector, backend and dashboards are all replaceable.
* **No payloads in spans.** Span attributes carry IDs, counts, durations and classes —
  never transcripts, measurements, file contents or rights-bearing material. Traces are
  operational data and are not a second copy of the evidence.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("mp2.telemetry")

_CONFIGURED: set[str] = set()
_TRACER: Any | None = None


def endpoint() -> str | None:
    """The configured OTLP endpoint, or None when telemetry is disabled."""
    value = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    return value or None


def enabled() -> bool:
    return endpoint() is not None


def setup(service_name: str) -> Any | None:
    """Configure tracing for a service. Safe to call more than once.

    Returns a tracer, or None when telemetry is disabled or unavailable.
    """
    global _TRACER
    if service_name in _CONFIGURED:
        return _TRACER
    _CONFIGURED.add(service_name)

    if not enabled():
        log.info("telemetry disabled (no OTEL_EXPORTER_OTLP_ENDPOINT)")
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.info("telemetry requested but the OpenTelemetry SDK is not installed")
        return None

    resource = Resource.create({
        "service.name": service_name,
        "service.namespace": "mp2",
        "service.version": os.getenv("MP2_VERSION", "0.1.0"),
        "deployment.environment": os.getenv("MP2_ENV", "development"),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("mp2")
    log.info("telemetry enabled for %s -> %s", service_name, endpoint())
    return _TRACER


def instrument_fastapi(app: Any, service_name: str) -> None:
    """Instrument a FastAPI app plus its SQLAlchemy and httpx clients, if available."""
    if setup(service_name) is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
    except Exception as exc:  # pragma: no cover - instrumentation must never break startup
        log.warning("FastAPI instrumentation unavailable: %s", type(exc).__name__)
    _instrument_clients()


def _instrument_clients() -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        log.debug("SQLAlchemy instrumentation unavailable: %s", type(exc).__name__)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        log.debug("httpx instrumentation unavailable: %s", type(exc).__name__)


def instrument_worker(service_name: str = "mp2-worker") -> None:
    if setup(service_name) is None:
        return
    _instrument_clients()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """Record a span if telemetry is on; otherwise do nothing at all.

    Attributes must be IDs, counts, durations or classes — never evidence payloads.
    """
    if _TRACER is None:
        yield
        return
    with _TRACER.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(f"mp2.{key}", value)
        yield
