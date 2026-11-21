from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class EntityMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaWork(EntityMixin, Base):
    __tablename__ = "media_work"
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    work_type: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MediaEdition(EntityMixin, Base):
    __tablename__ = "media_edition"
    media_work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_work.id"), index=True)
    edition_label: Mapped[str] = mapped_column(String(255), nullable=False)


class SourceAsset(EntityMixin, Base):
    __tablename__ = "source_asset"
    media_edition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_edition.id"), index=True)
    object_key: Mapped[str] = mapped_column(Text, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    provenance_class: Mapped[str] = mapped_column(String(64))
    rights_access_class: Mapped[str] = mapped_column(String(64))
    retention_class: Mapped[str] = mapped_column(String(64))
    probe_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Segment(EntityMixin, Base):
    __tablename__ = "segment"
    source_asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_asset.id"), index=True)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("source_asset_id", "kind", "start_ms", "end_ms"),)


class AnalysisRun(EntityMixin, Base):
    __tablename__ = "analysis_run"
    media_work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_work.id"), index=True)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("source_asset.id"))
    status: Mapped[str] = mapped_column(String(64), index=True, default="created")
    workflow_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class ExtractorDefinition(EntityMixin, Base):
    __tablename__ = "extractor_definition"
    extractor_id: Mapped[str] = mapped_column(String(255), index=True)
    extractor_version: Mapped[str] = mapped_column(String(128))
    repeatability_class: Mapped[str] = mapped_column(String(2))
    input_schema_version: Mapped[str] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    license_record: Mapped[str] = mapped_column(Text)
    hardware_class: Mapped[str] = mapped_column(String(128))
    __table_args__ = (UniqueConstraint("extractor_id", "extractor_version"),)


class Measurement(EntityMixin, Base):
    __tablename__ = "measurement"
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segment.id"), index=True)
    extractor_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extractor_definition.id"))
    metric: Mapped[str] = mapped_column(String(255), index=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_ref: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "segment_id", "extractor_definition_id", "metric",
                         postgresql_nulls_not_distinct=True),
    )


class ModelExecution(EntityMixin, Base):
    __tablename__ = "model_execution"
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    provider_adapter: Mapped[str] = mapped_column(String(255))
    model_tool_id: Mapped[str] = mapped_column(String(255))
    model_tool_version: Mapped[str] = mapped_column(String(128))
    execution_location: Mapped[str] = mapped_column(String(128))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)


class EvidenceClaim(EntityMixin, Base):
    __tablename__ = "evidence_claim"
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("segment.id"), index=True)
    model_execution_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("model_execution.id"))
    claim_type: Mapped[str] = mapped_column(String(255), index=True)
    structured_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "segment_id", "claim_type",
                         postgresql_nulls_not_distinct=True),
    )


class GenomeVersion(EntityMixin, Base):
    __tablename__ = "genome_version"
    analysis_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_run.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), default="stub")


class GenomeValue(EntityMixin, Base):
    __tablename__ = "genome_value"
    genome_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("genome_version.id"), index=True)
    dimension_id: Mapped[str] = mapped_column(String(255))
    value: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON)


class Projection(EntityMixin, Base):
    __tablename__ = "projection"
    genome_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("genome_version.id"))
    projection_type: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class ViewerProfile(EntityMixin, Base):
    __tablename__ = "viewer_profile"
    subject_ref: Mapped[str] = mapped_column(String(255), unique=True)


class ViewerSignal(EntityMixin, Base):
    __tablename__ = "viewer_signal"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(128))
    value: Mapped[dict[str, Any]] = mapped_column(JSON)


class ViewerDimension(EntityMixin, Base):
    __tablename__ = "viewer_dimension"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    dimension_id: Mapped[str] = mapped_column(String(255))
    value: Mapped[float] = mapped_column(Float)


class ViewerState(EntityMixin, Base):
    __tablename__ = "viewer_state"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)


class ViewingContext(EntityMixin, Base):
    __tablename__ = "viewing_context"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON)


class RecommendationRun(EntityMixin, Base):
    __tablename__ = "recommendation_run"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    status: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)


class RecommendationCandidate(EntityMixin, Base):
    __tablename__ = "recommendation_candidate"
    recommendation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_run.id"), index=True)
    media_work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_work.id"))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    rationale: Mapped[dict[str, Any]] = mapped_column(JSON)


class UserFeedback(EntityMixin, Base):
    __tablename__ = "user_feedback"
    viewer_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viewer_profile.id"), index=True)
    media_work_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("media_work.id"))
    signal: Mapped[dict[str, Any]] = mapped_column(JSON)
