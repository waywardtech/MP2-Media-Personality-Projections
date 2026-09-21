"""Worker-side runtime: configuration, database sessions, object store and idempotent upserts.

The orchestrator deliberately does not import the API application. Services read their own
environment; only mp2_domain / mp2_schemas / mp2_storage are shared.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from mp2_domain.models import ExtractorDefinition, Measurement, Segment
from mp2_extractors import ExtractorSpec, content_hash
from mp2_storage import LocalObjectStore, ObjectStore, S3ObjectStore, sha256_file

RAW_BUCKET = "mp2-raw"
NORMALIZED_BUCKET = "mp2-normalized"
DERIVED_BUCKET = "mp2-derived"


def database_url() -> str:
    return os.getenv("MP2_DATABASE_URL", "postgresql+psycopg://mp2:mp2-dev-only@postgres:5432/mp2")


_engine = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(_engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def object_store() -> ObjectStore:
    if os.getenv("MP2_OBJECT_STORE", "local") == "seaweedfs":
        return S3ObjectStore(
            os.getenv("MP2_S3_ENDPOINT", "http://seaweedfs:8333"),
            os.getenv("MP2_S3_ACCESS_KEY", "mp2-dev"),
            os.getenv("MP2_S3_SECRET_KEY", "change-me"),
            os.getenv("MP2_S3_REGION", "us-east-1"),
        )
    return LocalObjectStore(Path(os.getenv("MP2_LOCAL_OBJECT_ROOT", "/var/lib/mp2/objects")))


def work_dir(analysis_run_id: str) -> Path:
    root = Path(os.getenv("MP2_WORK_ROOT", "/var/lib/mp2/work")) / analysis_run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def clear_work_dir(analysis_run_id: str) -> None:
    """Disposable intermediates only; never touches object storage or PostgreSQL."""
    shutil.rmtree(work_dir(analysis_run_id), ignore_errors=True)


def materialize(store: ObjectStore, bucket: str, key: str, target: Path,
                expected_sha256: str | None = None) -> Path:
    """Fetch an object to local scratch once. Safe to call on every activity attempt."""
    if target.is_file() and (expected_sha256 is None or sha256_file(target) == expected_sha256):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")
    with store.open(bucket, key) as source, tmp.open("wb") as sink:
        shutil.copyfileobj(source, sink)
    tmp.replace(target)
    if expected_sha256 is not None and sha256_file(target) != expected_sha256:
        raise ValueError(f"hash mismatch materializing {bucket}/{key}")
    return target


# --- idempotent persistence ---------------------------------------------------------------

def ensure_extractor_definition(session: Session, spec: ExtractorSpec) -> ExtractorDefinition:
    existing = session.scalar(
        select(ExtractorDefinition).where(
            ExtractorDefinition.extractor_id == spec.extractor_id,
            ExtractorDefinition.extractor_version == spec.version,
        )
    )
    if existing is not None:
        return existing
    definition = ExtractorDefinition(
        extractor_id=spec.extractor_id,
        extractor_version=spec.version,
        repeatability_class=spec.repeatability_class,
        input_schema_version=spec.input_schema_version,
        output_schema_version=spec.output_schema_version,
        parameters=spec.parameters,
        license_record=spec.license_record,
        hardware_class=spec.hardware_class,
    )
    session.add(definition)
    session.flush()
    return definition


def upsert_segment(session: Session, source_asset_id: uuid.UUID, kind: str,
                   start_ms: int, end_ms: int) -> Segment:
    existing = session.scalar(
        select(Segment).where(
            Segment.source_asset_id == source_asset_id,
            Segment.kind == kind,
            Segment.start_ms == start_ms,
            Segment.end_ms == end_ms,
        )
    )
    if existing is not None:
        return existing
    segment = Segment(source_asset_id=source_asset_id, kind=kind,
                      start_ms=start_ms, end_ms=end_ms)
    session.add(segment)
    session.flush()
    return segment


def upsert_measurement(session: Session, analysis_run_id: uuid.UUID, spec: ExtractorSpec,
                       metric: str, value: dict[str, Any], source_ref: str,
                       segment_id: uuid.UUID | None = None) -> Measurement:
    """Write a measurement exactly once per (run, segment, extractor, metric).

    A retried activity recomputes the same value and overwrites in place, so a worker
    crash cannot produce duplicate canonical output.
    """
    definition = ensure_extractor_definition(session, spec)
    existing = session.scalar(
        select(Measurement).where(
            Measurement.analysis_run_id == analysis_run_id,
            Measurement.extractor_definition_id == definition.id,
            Measurement.metric == metric,
            Measurement.segment_id.is_(segment_id) if segment_id is None
            else Measurement.segment_id == segment_id,
        )
    )
    digest = content_hash(value)
    if existing is not None:
        existing.value = value
        existing.source_ref = source_ref
        existing.content_hash = digest
        session.flush()
        return existing
    measurement = Measurement(
        analysis_run_id=analysis_run_id, segment_id=segment_id,
        extractor_definition_id=definition.id, metric=metric, value=value,
        source_ref=source_ref, content_hash=digest,
    )
    session.add(measurement)
    session.flush()
    return measurement
