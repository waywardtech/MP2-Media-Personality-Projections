from __future__ import annotations

import json
import subprocess
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mp2_domain.enums import RunStatus
from mp2_domain.models import (
    AnalysisRun,
    EvidenceClaim,
    ExtractorDefinition,
    Measurement,
    MediaEdition,
    MediaWork,
    ModelExecution,
    Segment,
    SourceAsset,
)
from mp2_observability import telemetry
from mp2_schemas.api import AnalysisRunView, AnalyzeRequest, AssetCreate, MediaCreate, MediaView
from mp2_storage import LocalObjectStore, ObjectStore, S3ObjectStore

from .config import settings
from .db import session_scope

app = FastAPI(title="MP2 API", version="0.1.0")
# No-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the SDK is installed.
telemetry.instrument_fastapi(app, "mp2-api")
Db = Annotated[Session, Depends(session_scope)]


def object_store() -> ObjectStore:
    if settings.object_store == "seaweedfs":
        return S3ObjectStore(settings.s3_endpoint, settings.s3_access_key,
                             settings.s3_secret_key, settings.s3_region)
    return LocalObjectStore(settings.local_object_root)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Db) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ready"}


@app.post("/v1/media", response_model=MediaView, status_code=status.HTTP_201_CREATED)
def create_media(payload: MediaCreate, db: Db) -> MediaWork:
    work = MediaWork(title=payload.title, work_type=payload.work_type,
                     metadata_json=payload.metadata)
    db.add(work)
    db.flush()
    db.add(MediaEdition(media_work_id=work.id, edition_label=payload.edition_label))
    db.commit()
    return work


@app.get("/v1/media/{media_work_id}", response_model=MediaView)
def get_media(media_work_id: uuid.UUID, db: Db) -> MediaWork:
    work = db.get(MediaWork, media_work_id)
    if work is None:
        raise HTTPException(status_code=404, detail="media work not found")
    return work


@app.post("/v1/media/{media_work_id}/assets", status_code=status.HTTP_201_CREATED)
def add_asset(media_work_id: uuid.UUID, payload: AssetCreate, db: Db) -> dict[str, object]:
    edition = db.scalar(select(MediaEdition).where(MediaEdition.media_work_id == media_work_id))
    if edition is None:
        raise HTTPException(status_code=404, detail="media work not found")
    path = payload.local_path.resolve()
    if not path.is_file():
        raise HTTPException(status_code=400, detail="local_path must reference a readable file")
    ingest_root = settings.ingest_root.resolve()
    if ingest_root != path and ingest_root not in path.parents:
        raise HTTPException(status_code=403,
                            detail="local_path is outside the controlled ingest root")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                            "-of", "json", str(path)], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise HTTPException(status_code=422, detail=f"ffprobe failed: {probe.stderr[-500:]}")
    key = f"{media_work_id}/{uuid.uuid4()}/{path.name}"
    info = object_store().put_file("mp2-raw", key, path)
    asset = SourceAsset(media_edition_id=edition.id, object_key=key, sha256=info.sha256,
                        size_bytes=info.size_bytes, mime_type=payload.mime_type,
                        provenance_class=payload.provenance_class.value,
                        rights_access_class=payload.rights_access_class.value,
                        retention_class=payload.retention_class.value,
                        probe_json=json.loads(probe.stdout))
    db.add(asset)
    db.commit()
    return {"id": asset.id, "sha256": asset.sha256, "object_key": asset.object_key}


@app.post("/v1/media/{media_work_id}/analyze", response_model=AnalysisRunView,
          status_code=status.HTTP_202_ACCEPTED)
async def analyze(media_work_id: uuid.UUID, payload: AnalyzeRequest, db: Db) -> AnalysisRun:
    asset = db.get(SourceAsset, payload.source_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="source asset not found")
    edition = db.get(MediaEdition, asset.media_edition_id)
    if edition is None or edition.media_work_id != media_work_id:
        raise HTTPException(status_code=409, detail="source asset does not belong to media work")
    run = AnalysisRun(media_work_id=media_work_id, source_asset_id=asset.id,
                      status=RunStatus.CREATED.value, parameters=payload.parameters)
    db.add(run)
    db.flush()
    run.workflow_id = f"analyze-media-{run.id}-v{run.version}"
    db.commit()
    try:
        from temporalio.client import Client

        from mp2_orchestrator.workflow import AnalyzeMediaInput, AnalyzeMediaWorkflow
        client = await Client.connect(settings.temporal_address)
        await client.start_workflow(AnalyzeMediaWorkflow.run,
                                    AnalyzeMediaInput(str(run.id), str(asset.id), run.version),
                                    id=run.workflow_id, task_queue="mp2-io")
    except Exception as exc:
        run.error = f"workflow dispatch failed: {type(exc).__name__}"
        db.commit()
        raise HTTPException(status_code=503, detail="workflow dispatch unavailable") from exc
    return run


@app.get("/v1/analysis-runs/{run_id}", response_model=AnalysisRunView)
def get_analysis_run(run_id: uuid.UUID, db: Db) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return run


# --- Read models for the console -----------------------------------------------------------
# These exist so the web tier can render evidence without ever touching object storage.

@app.get("/v1/media", response_model=list[MediaView])
def list_media(db: Db, limit: int = 100) -> list[MediaWork]:
    return list(db.scalars(
        select(MediaWork).order_by(MediaWork.created_at.desc()).limit(min(limit, 500))
    ).all())


@app.get("/v1/analysis-runs", response_model=list[AnalysisRunView])
def list_analysis_runs(db: Db, limit: int = 100) -> list[AnalysisRun]:
    return list(db.scalars(
        select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(min(limit, 500))
    ).all())


@app.get("/v1/analysis-runs/{run_id}/evidence")
def get_run_evidence(run_id: uuid.UUID, db: Db) -> dict[str, object]:
    """Lineage-complete view of one run: measurements, claims and model executions.

    Every claim carries the segment, the model execution and the evidence refs it came
    from, so nothing in the console is presented without its provenance.
    """
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="analysis run not found")

    definitions = {d.id: d for d in db.scalars(select(ExtractorDefinition)).all()}
    measurements = db.scalars(
        select(Measurement).where(Measurement.analysis_run_id == run_id)
    ).all()
    claims = db.scalars(
        select(EvidenceClaim).where(EvidenceClaim.analysis_run_id == run_id)
    ).all()
    executions = db.scalars(
        select(ModelExecution).where(ModelExecution.analysis_run_id == run_id)
    ).all()
    segment_ids = {m.segment_id for m in measurements if m.segment_id} | {
        c.segment_id for c in claims if c.segment_id
    }
    segments = db.scalars(
        select(Segment).where(Segment.id.in_(segment_ids))
    ).all() if segment_ids else []

    return {
        "run": {"id": str(run.id), "status": run.status, "version": run.version,
                "workflow_id": run.workflow_id, "error": run.error},
        "segments": [
            {"id": str(s.id), "kind": s.kind, "start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in sorted(segments, key=lambda s: s.start_ms)
        ],
        "measurements": [
            {
                "id": str(m.id),
                "metric": m.metric,
                "segment_id": str(m.segment_id) if m.segment_id else None,
                "content_hash": m.content_hash,
                "source_ref": m.source_ref,
                "extractor": (
                    {
                        "id": definitions[m.extractor_definition_id].extractor_id,
                        "version": definitions[m.extractor_definition_id].extractor_version,
                        "repeatability_class":
                            definitions[m.extractor_definition_id].repeatability_class,
                        "license": definitions[m.extractor_definition_id].license_record,
                    }
                    if m.extractor_definition_id in definitions else None
                ),
                "value": m.value,
            }
            for m in sorted(measurements, key=lambda m: m.metric)
        ],
        "evidence_claims": [
            {"id": str(c.id), "claim_type": c.claim_type,
             "segment_id": str(c.segment_id) if c.segment_id else None,
             "model_execution_id": str(c.model_execution_id) if c.model_execution_id else None,
             "confidence": c.confidence, "evidence_refs": c.evidence_refs,
             "schema_version": c.schema_version, "structured_value": c.structured_value}
            for c in claims
        ],
        "model_executions": [
            {"id": str(e.id), "provider_adapter": e.provider_adapter,
             "model_tool_id": e.model_tool_id, "model_tool_version": e.model_tool_version,
             "execution_location": e.execution_location, "latency_ms": e.latency_ms,
             "estimated_cost": e.estimated_cost, "content_hash": e.content_hash,
             "warnings": e.warnings}
            for e in executions
        ],
    }
