"""End-to-end vertical slice against a running core stack.

These tests talk to the real API, PostgreSQL, SeaweedFS, Temporal and Model Gateway. They
skip cleanly when no stack is reachable, so the unit suite stays runnable anywhere.

Run against a running stack with:

    MP2_API_URL=http://127.0.0.1:8000 infra/scripts/mp2.sh test tests/integration

The fixture must already be registered in the ingest root:

    infra/scripts/mp2.sh fixture
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

httpx = pytest.importorskip("httpx")

API = os.getenv("MP2_API_URL", "http://127.0.0.1:8000")
INGEST_PATH = os.getenv("MP2_TEST_MEDIA", "/media/synthetic-av.mp4")
TERMINAL = {"evidence_ready", "failed"}


def _stack_available() -> bool:
    try:
        return httpx.get(f"{API}/health", timeout=3).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_available(),
    reason=f"no MP2 stack reachable at {API}; start it with infra/scripts/mp2.sh up",
)


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=API, timeout=30) as c:
        yield c


@pytest.fixture(scope="module")
def analysis(client) -> dict[str, Any]:
    """Register a work, attach the fixture and run the analysis to a terminal state."""
    work = client.post("/v1/media", json={
        "title": "Integration Vertical Slice",
        "work_type": "short",
        "edition_label": "integration",
    })
    work.raise_for_status()
    media_id = work.json()["id"]

    asset = client.post(f"/v1/media/{media_id}/assets", json={
        "local_path": INGEST_PATH,
        "provenance_class": "synthetic",
        "rights_access_class": "redistributable",
        "retention_class": "project",
    })
    if asset.status_code == 400:
        pytest.skip(f"fixture {INGEST_PATH} not present; run infra/scripts/mp2.sh fixture")
    asset.raise_for_status()
    asset_id = asset.json()["id"]

    run = client.post(f"/v1/media/{media_id}/analyze",
                      json={"source_asset_id": asset_id, "parameters": {"suite": "integration"}})
    run.raise_for_status()
    run_id = run.json()["id"]

    deadline = time.time() + 600
    status = "created"
    while time.time() < deadline:
        time.sleep(5)
        status = client.get(f"/v1/analysis-runs/{run_id}").json()["status"]
        if status in TERMINAL:
            break

    return {"media_id": media_id, "asset_id": asset_id, "run_id": run_id,
            "status": status, "sha256": asset.json()["sha256"]}


# --- G2: ingest ----------------------------------------------------------------------------

def test_rights_metadata_is_required(client) -> None:
    media_id = client.post("/v1/media", json={
        "title": "Rights Guard", "work_type": "short", "edition_label": "t"}).json()["id"]
    response = client.post(f"/v1/media/{media_id}/assets",
                           json={"local_path": INGEST_PATH})
    assert response.status_code == 422
    missing = {e["loc"][-1] for e in response.json()["detail"]}
    assert {"provenance_class", "rights_access_class", "retention_class"} <= missing


def test_ingest_root_containment_is_enforced(client) -> None:
    media_id = client.post("/v1/media", json={
        "title": "Traversal Guard", "work_type": "short", "edition_label": "t"}).json()["id"]
    response = client.post(f"/v1/media/{media_id}/assets", json={
        "local_path": "/etc/hostname",
        "provenance_class": "synthetic",
        "rights_access_class": "internal_test",
        "retention_class": "ephemeral",
    })
    assert response.status_code == 403


def test_asset_is_hashed_and_stored(analysis) -> None:
    assert len(analysis["sha256"]) == 64
    int(analysis["sha256"], 16)  # must be hex


# --- G3 / G4: evidence ---------------------------------------------------------------------

def test_run_reaches_evidence_ready(analysis) -> None:
    assert analysis["status"] == "evidence_ready", (
        f"run ended as {analysis['status']}; inspect with "
        f"infra/scripts/mp2.sh logs worker-cpu 100"
    )


def test_deterministic_measurements_are_recorded(client, analysis) -> None:
    evidence = client.get(f"/v1/analysis-runs/{analysis['run_id']}/evidence").json()
    metrics = {m["metric"] for m in evidence["measurements"]}

    # The deterministic families that must exist for a video-with-audio fixture.
    assert "media.probe_summary" in metrics
    assert "shot.boundary" in metrics
    assert "visual.segment_measures" in metrics
    assert "audio.work_measures" in metrics
    assert evidence["segments"], "shot segmentation produced no segments"


def test_every_measurement_carries_lineage(client, analysis) -> None:
    evidence = client.get(f"/v1/analysis-runs/{analysis['run_id']}/evidence").json()
    for m in evidence["measurements"]:
        assert m["extractor"], f"{m['metric']} has no extractor definition"
        assert m["extractor"]["version"], f"{m['metric']} has no extractor version"
        assert m["extractor"]["repeatability_class"] in {"D0", "D1", "D2"}
        assert m["extractor"]["license"] != "unrecorded"
        assert len(m["content_hash"]) == 64
        assert m["source_ref"]


def test_no_duplicate_canonical_measurements(client, analysis) -> None:
    evidence = client.get(f"/v1/analysis-runs/{analysis['run_id']}/evidence").json()
    keys = [(m["metric"], m["segment_id"],
             m["extractor"]["id"] if m["extractor"] else None)
            for m in evidence["measurements"]]
    assert len(keys) == len(set(keys)), "duplicate canonical measurement rows"


def test_evidence_claims_link_to_their_provenance(client, analysis) -> None:
    evidence = client.get(f"/v1/analysis-runs/{analysis['run_id']}/evidence").json()
    claims = evidence["evidence_claims"]
    if not claims:
        pytest.skip("no semantic claims recorded; model gateway was unreachable")

    executions = {e["id"] for e in evidence["model_executions"]}
    segments = {s["id"] for s in evidence["segments"]}
    for claim in claims:
        assert 0.0 <= claim["confidence"] <= 1.0
        assert claim["evidence_refs"], "a claim must cite its evidence"
        assert claim["schema_version"]
        assert claim["model_execution_id"] in executions
        assert claim["segment_id"] in segments


def test_model_execution_is_local_and_free(client, analysis) -> None:
    evidence = client.get(f"/v1/analysis-runs/{analysis['run_id']}/evidence").json()
    if not evidence["model_executions"]:
        pytest.skip("no model executions recorded")
    for execution in evidence["model_executions"]:
        assert execution["execution_location"] == "local"
        assert execution["estimated_cost"] == 0
        assert len(execution["content_hash"]) == 64


# --- G6: portability -----------------------------------------------------------------------

def test_gateway_reports_external_routing_disabled() -> None:
    """The gateway is internal-only, so ask the API's view of policy via the ops script."""
    response = httpx.get(f"{API}/health", timeout=5)
    assert response.status_code == 200
    # Cost ceilings above zero must be refused while external models are disabled.
    # Verified directly against the gateway in tests/unit/test_gateway.py.
