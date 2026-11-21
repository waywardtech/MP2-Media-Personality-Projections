"""Telemetry must be free when it is off, and must never break a service.

The failure mode worth guarding against is a service that will not start, or an analysis
that fails, because an observability backend is missing. Telemetry is operational
convenience; it is never load-bearing.
"""

from __future__ import annotations

import importlib

import pytest

from mp2_observability import telemetry


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Each test gets a clean module: setup() memoises per service name."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    importlib.reload(telemetry)
    yield
    importlib.reload(telemetry)


def test_disabled_without_an_endpoint() -> None:
    assert telemetry.endpoint() is None
    assert telemetry.enabled() is False
    assert telemetry.setup("mp2-test") is None


def test_enabled_flag_follows_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    assert telemetry.enabled() is True
    assert telemetry.endpoint() == "http://otel-collector:4318"


def test_blank_endpoint_counts_as_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "   ")
    assert telemetry.enabled() is False


def test_span_is_a_no_op_when_disabled() -> None:
    # Must not raise, and must not require any OTel import.
    with telemetry.span("activity.Test", analysis_run_id="r1", segment_count=3):
        pass


def test_span_tolerates_none_attributes() -> None:
    with telemetry.span("activity.Test", analysis_run_id=None, segment_count=None):
        pass


def test_instrumenting_a_disabled_service_does_nothing() -> None:
    sentinel = object()
    telemetry.instrument_fastapi(sentinel, "mp2-test")  # type: ignore[arg-type]
    telemetry.instrument_worker("mp2-test")


def test_setup_is_idempotent_per_service() -> None:
    assert telemetry.setup("mp2-test") is None
    assert telemetry.setup("mp2-test") is None


def test_no_observability_vendor_sdk_is_imported() -> None:
    """MP2 emits OTLP only, so the backend stays replaceable."""
    import ast
    from pathlib import Path

    forbidden = {"datadog", "ddtrace", "newrelic", "elasticapm", "sentry_sdk",
                 "honeycomb", "lightstep", "signalfx"}
    root = Path("packages/observability")
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[0] in forbidden for n in names), f"{source}: {names}"
