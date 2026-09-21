"""Temporal activity implementations for AnalyzeMediaWorkflow.

Contract for every activity in this module:

* Input and output are small JSON state dictionaries of IDs and references.
  Media bytes never travel through Temporal.
* Activities are idempotent. Each one recomputes from durable inputs and writes through
  get-or-update helpers keyed on natural identity, so a retry after a worker crash
  converges on the same canonical rows rather than duplicating them.
* Measurements record their extractor identity, version, parameters and a content hash,
  so a D0 rerun can be compared mechanically.
* Generative interpretation runs last and writes evidence claims only. It never writes
  canonical genome values.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from temporalio import activity

from mp2_domain.enums import RunStatus
from mp2_domain.models import (
    AnalysisRun,
    EvidenceClaim,
    Measurement,
    ModelExecution,
    Segment,
    SourceAsset,
)
from mp2_extractors import (
    FASTER_WHISPER,
    FFMPEG_NORMALIZE_AUDIO,
    FFPROBE,
    LIBROSA_AUDIO,
    OPENCV_VISUAL,
    PYSCENEDETECT,
    TEXT_STATISTICS,
    asr,
    audio_measures,
    content_hash,
    detect_shots,
    dialogue_statistics,
    has_audio_stream,
    normalize_audio,
    probe_media,
    probe_summary,
    visual_measures,
)
from mp2_observability import telemetry
from mp2_schemas.scene_packet import (
    EVIDENCE_SCHEMA_VERSION,
    ScenePacket,
    SceneTimeRange,
)

from .runtime import (
    DERIVED_BUCKET,
    NORMALIZED_BUCKET,
    RAW_BUCKET,
    materialize,
    object_store,
    session_scope,
    upsert_measurement,
    upsert_segment,
    work_dir,
)

log = logging.getLogger("mp2.activities")

State = dict[str, Any]


def instrumented(name: str) -> Callable[[Callable[[State], State]], Callable[[State], State]]:
    """Time an activity, record the duration in state, and emit a span.

    Durations are carried in state and land in `run.evidence_summary`, so per-stage cost
    is recoverable from the database alone. That matters on a machine with no telemetry
    backend running: profiling must not depend on the `ops` profile being up.

    The span carries IDs, counts and durations only — never evidence payloads.
    """

    def decorate(fn: Callable[[State], State]) -> Callable[[State], State]:
        @functools.wraps(fn)
        def wrapper(state: State) -> State:
            started = time.perf_counter()
            with telemetry.span(
                f"activity.{name}",
                analysis_run_id=state.get("analysis_run_id"),
                source_asset_id=state.get("source_asset_id"),
                segment_count=len(state.get("segment_ids", [])),
            ):
                result = fn(state)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            timings = dict(result.get("activity_timings_ms", {}))
            # A retried activity reports the attempt that actually completed.
            timings[name] = elapsed_ms
            log.info("activity %s completed in %d ms", name, elapsed_ms)
            return {**result, "activity_timings_ms": timings}

        return wrapper

    return decorate


def _done(state: State, name: str, **extra: Any) -> State:
    completed = list(state.get("completed", []))
    if name not in completed:
        completed.append(name)
    return {**state, **extra, "completed": completed}


def _warn(state: State, message: str) -> list[str]:
    warnings = list(state.get("warnings", []))
    if message not in warnings:
        warnings.append(message)
    return warnings


def _run_id(state: State) -> uuid.UUID:
    return uuid.UUID(state["analysis_run_id"])


def _asset_id(state: State) -> uuid.UUID:
    return uuid.UUID(state["source_asset_id"])


def _load_asset(session: Any, state: State) -> SourceAsset:
    asset = session.get(SourceAsset, _asset_id(state))
    if asset is None:
        raise ValueError(f"source asset {state['source_asset_id']} not found")
    return asset


def _set_status(session: Any, run_id: uuid.UUID, status: RunStatus,
                error: str | None = None) -> None:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise ValueError(f"analysis run {run_id} not found")
    run.status = status.value
    if error is not None:
        run.error = error


def _source_path(state: State, asset: SourceAsset) -> Path:
    suffix = Path(asset.object_key).suffix or ".bin"
    return work_dir(state["analysis_run_id"]) / f"source{suffix}"


def _normalized_path(state: State) -> Path:
    return work_dir(state["analysis_run_id"]) / "normalized-audio.wav"


# --- 1. ValidateSource ---------------------------------------------------------------------

@activity.defn(name="ValidateSource")
@instrumented("ValidateSource")
def validate_source(state: State) -> State:
    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        if not store.exists(RAW_BUCKET, asset.object_key):
            raise ValueError(f"raw object missing: {RAW_BUCKET}/{asset.object_key}")
        _set_status(session, _run_id(state), RunStatus.RUNNING)
        return _done(state, "ValidateSource",
                     object_key=asset.object_key,
                     expected_sha256=asset.sha256,
                     rights_access_class=asset.rights_access_class,
                     provenance_class=asset.provenance_class,
                     retention_class=asset.retention_class)


# --- 2. ProbeAndHash -----------------------------------------------------------------------

@activity.defn(name="ProbeAndHash")
@instrumented("ProbeAndHash")
def probe_and_hash(state: State) -> State:
    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        local = materialize(store, RAW_BUCKET, asset.object_key,
                            _source_path(state, asset), asset.sha256)

        probe = probe_media(local)
        summary = probe_summary(probe)
        run_id = _run_id(state)
        upsert_measurement(session, run_id, FFPROBE, "media.probe_summary", summary,
                           source_ref=f"s3://{RAW_BUCKET}/{asset.object_key}")
        upsert_measurement(session, run_id, FFPROBE, "media.probe_raw", probe,
                           source_ref=f"s3://{RAW_BUCKET}/{asset.object_key}")

        return _done(state, "ProbeAndHash",
                     duration_s=summary.get("duration_s"),
                     has_audio=has_audio_stream(probe),
                     has_video=summary.get("video_codec") is not None)


# --- 3. NormalizeAsset ---------------------------------------------------------------------

@activity.defn(name="NormalizeAsset")
@instrumented("NormalizeAsset")
def normalize_asset(state: State) -> State:
    if not state.get("has_audio"):
        return _done(state, "NormalizeAsset", normalized_key=None,
                     warnings=_warn(state, "source has no audio stream; audio path skipped"))

    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        local = materialize(store, RAW_BUCKET, asset.object_key,
                            _source_path(state, asset), asset.sha256)
        target = _normalized_path(state)
        if not target.is_file():
            normalize_audio(local, target, FFMPEG_NORMALIZE_AUDIO)

        key = f"{state['analysis_run_id']}/normalized-audio.wav"
        info = store.put_file(NORMALIZED_BUCKET, key, target)
        upsert_measurement(
            session, _run_id(state), FFMPEG_NORMALIZE_AUDIO, "audio.normalized_asset",
            {"sample_rate": FFMPEG_NORMALIZE_AUDIO.parameters["sample_rate"],
             "channels": FFMPEG_NORMALIZE_AUDIO.parameters["channels"],
             "size_bytes": info.size_bytes, "sha256": info.sha256},
            source_ref=f"s3://{NORMALIZED_BUCKET}/{key}",
        )
        return _done(state, "NormalizeAsset", normalized_key=key)


# --- 4. SegmentShots -----------------------------------------------------------------------

@activity.defn(name="SegmentShots")
@instrumented("SegmentShots")
def segment_shots(state: State) -> State:
    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        local = materialize(store, RAW_BUCKET, asset.object_key,
                            _source_path(state, asset), asset.sha256)

        if not state.get("has_video"):
            duration_ms = int(round(float(state.get("duration_s") or 0) * 1000))
            shots = [{"index": 0, "start_ms": 0, "end_ms": duration_ms,
                      "transition_candidate": "none"}]
        else:
            shots = detect_shots(local, PYSCENEDETECT)

        run_id = _run_id(state)
        segment_ids: list[str] = []
        for shot in shots:
            segment = upsert_segment(session, asset.id, "shot",
                                     int(shot["start_ms"]), int(shot["end_ms"]))
            segment_ids.append(str(segment.id))
            upsert_measurement(
                session, run_id, PYSCENEDETECT, "shot.boundary",
                {"index": shot["index"], "start_ms": shot["start_ms"],
                 "end_ms": shot["end_ms"],
                 "duration_ms": int(shot["end_ms"]) - int(shot["start_ms"]),
                 "transition_candidate": shot["transition_candidate"]},
                source_ref=f"s3://{RAW_BUCKET}/{asset.object_key}",
                segment_id=segment.id,
            )
        upsert_measurement(
            session, run_id, PYSCENEDETECT, "shot.summary",
            {"shot_count": len(shots),
             "mean_shot_duration_ms": (
                 sum(int(s["end_ms"]) - int(s["start_ms"]) for s in shots) / len(shots))
             if shots else 0},
            source_ref=f"s3://{RAW_BUCKET}/{asset.object_key}",
        )
        return _done(state, "SegmentShots", segment_ids=segment_ids, shot_count=len(shots))


# --- 5. ExtractVisual ----------------------------------------------------------------------

@activity.defn(name="ExtractVisual")
@instrumented("ExtractVisual")
def extract_visual(state: State) -> State:
    if not state.get("has_video"):
        return _done(state, "ExtractVisual",
                     warnings=_warn(state, "source has no video stream; visual path skipped"))

    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        local = materialize(store, RAW_BUCKET, asset.object_key,
                            _source_path(state, asset), asset.sha256)
        run_id = _run_id(state)
        count = 0
        for segment_id in state.get("segment_ids", []):
            segment = session.get(Segment, uuid.UUID(segment_id))
            if segment is None:
                continue
            values = visual_measures(local, segment.start_ms, segment.end_ms, OPENCV_VISUAL)
            upsert_measurement(session, run_id, OPENCV_VISUAL, "visual.segment_measures",
                               values, source_ref=f"s3://{RAW_BUCKET}/{asset.object_key}",
                               segment_id=segment.id)
            count += 1
        return _done(state, "ExtractVisual", visual_segments=count)


# --- 6. ExtractAudio -----------------------------------------------------------------------

@activity.defn(name="ExtractAudio")
@instrumented("ExtractAudio")
def extract_audio(state: State) -> State:
    key = state.get("normalized_key")
    if not key:
        return _done(state, "ExtractAudio",
                     warnings=_warn(state, "no normalized audio; audio measures skipped"))

    with session_scope() as session:
        asset = _load_asset(session, state)
        store = object_store()
        wav = materialize(store, NORMALIZED_BUCKET, key, _normalized_path(state))
        run_id = _run_id(state)

        whole = audio_measures(wav, None, None, LIBROSA_AUDIO)
        upsert_measurement(session, run_id, LIBROSA_AUDIO, "audio.work_measures", whole,
                           source_ref=f"s3://{NORMALIZED_BUCKET}/{key}")

        count = 0
        for segment_id in state.get("segment_ids", []):
            segment = session.get(Segment, uuid.UUID(segment_id))
            if segment is None:
                continue
            values = audio_measures(wav, segment.start_ms, segment.end_ms, LIBROSA_AUDIO)
            upsert_measurement(session, run_id, LIBROSA_AUDIO, "audio.segment_measures",
                               values, source_ref=f"s3://{NORMALIZED_BUCKET}/{key}",
                               segment_id=segment.id)
            count += 1
        _ = asset
        return _done(state, "ExtractAudio", audio_segments=count)


# --- 7. Transcribe -------------------------------------------------------------------------

@activity.defn(name="Transcribe")
@instrumented("Transcribe")
def transcribe(state: State) -> State:
    key = state.get("normalized_key")
    if not key:
        return _done(state, "Transcribe", asr_available=False,
                     warnings=_warn(state, "no normalized audio; ASR skipped"))
    if not asr.is_available():
        return _done(state, "Transcribe", asr_available=False,
                     warnings=_warn(state, "local ASR weights unavailable; ASR skipped"))

    with session_scope() as session:
        store = object_store()
        wav = materialize(store, NORMALIZED_BUCKET, key, _normalized_path(state))
        result = asr.transcribe(wav, FASTER_WHISPER, word_timestamps=True)
        run_id = _run_id(state)

        upsert_measurement(
            session, run_id, FASTER_WHISPER, "asr.transcript",
            {"language": result.language,
             "language_probability": result.language_probability,
             "duration_s": result.duration_s,
             "utterance_count": len(result.utterances),
             "utterances": result.utterances,
             "text": result.text,
             "metadata": result.metadata},
            source_ref=f"s3://{NORMALIZED_BUCKET}/{key}",
        )

        # Per-segment dialogue statistics, attributed to the shot they fall in.
        count = 0
        for segment_id in state.get("segment_ids", []):
            segment = session.get(Segment, uuid.UUID(segment_id))
            if segment is None:
                continue
            start_s, end_s = segment.start_ms / 1000.0, segment.end_ms / 1000.0
            local_utterances = [
                u for u in result.utterances
                if float(u["end_s"]) > start_s and float(u["start_s"]) < end_s
            ]
            stats = dialogue_statistics(local_utterances, max(end_s - start_s, 0.0),
                                        TEXT_STATISTICS)
            upsert_measurement(session, run_id, TEXT_STATISTICS, "dialogue.segment_measures",
                               stats, source_ref=f"s3://{NORMALIZED_BUCKET}/{key}",
                               segment_id=segment.id)
            count += 1

        return _done(state, "Transcribe", asr_available=True,
                     asr_language=result.language,
                     asr_utterances=len(result.utterances),
                     dialogue_segments=count)


# --- 8. BuildScenePackets ------------------------------------------------------------------

def _measurement_value(session: Any, run_id: uuid.UUID, metric: str,
                       segment_id: uuid.UUID | None) -> dict[str, Any]:
    stmt = select(Measurement).where(
        Measurement.analysis_run_id == run_id, Measurement.metric == metric,
        Measurement.segment_id.is_(None) if segment_id is None
        else Measurement.segment_id == segment_id,
    )
    row = session.scalar(stmt)
    return dict(row.value) if row is not None else {}


@activity.defn(name="BuildScenePackets")
@instrumented("BuildScenePackets")
def build_scene_packets(state: State) -> State:
    with session_scope() as session:
        run_id = _run_id(state)
        store = object_store()
        transcript = _measurement_value(session, run_id, "asr.transcript", None)
        utterances = list(transcript.get("utterances", []))

        packet_keys: list[str] = []
        for index, segment_id in enumerate(state.get("segment_ids", [])):
            segment = session.get(Segment, uuid.UUID(segment_id))
            if segment is None:
                continue
            start_s, end_s = segment.start_ms / 1000.0, segment.end_ms / 1000.0
            local_utterances = [
                u for u in utterances
                if float(u["end_s"]) > start_s and float(u["start_s"]) < end_s
            ]
            packet = ScenePacket(
                analysis_run_id=str(run_id),
                source_asset_id=state["source_asset_id"],
                scene=SceneTimeRange(segment_id=str(segment.id), index=index,
                                     start_ms=segment.start_ms, end_ms=segment.end_ms),
                transcript_text=" ".join(str(u.get("text", "")).strip()
                                         for u in local_utterances).strip(),
                utterances=local_utterances,
                visual_measurements=_measurement_value(
                    session, run_id, "visual.segment_measures", segment.id),
                audio_measurements=_measurement_value(
                    session, run_id, "audio.segment_measures", segment.id),
                dialogue_measurements=_measurement_value(
                    session, run_id, "dialogue.segment_measures", segment.id),
                known_character_aliases=list(state.get("known_character_aliases", [])),
            )

            key = f"{run_id}/scene-packets/{index:05d}-{segment.id}.json"
            path = work_dir(state["analysis_run_id"]) / "packets" / f"{index:05d}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
            store.put_file(DERIVED_BUCKET, key, path)
            packet_keys.append(key)

            upsert_measurement(
                session, run_id, TEXT_STATISTICS, "scene.packet_ref",
                {"object_key": key, "schema_version": packet.schema_version,
                 "content_hash": content_hash(packet.model_dump(mode="json"))},
                source_ref=f"s3://{DERIVED_BUCKET}/{key}", segment_id=segment.id,
            )

        return _done(state, "BuildScenePackets", scene_packet_keys=packet_keys,
                     scene_packet_count=len(packet_keys))


# --- 9. InterpretSemantics -----------------------------------------------------------------

@activity.defn(name="InterpretSemantics")
@instrumented("InterpretSemantics")
def interpret_semantics(state: State) -> State:
    """Route scene packets through the MP2 Model Gateway.

    The gateway is provider-neutral. If no local generative model is installed the run
    continues with deterministic evidence only; MP2 never fabricates semantic claims to
    fill the gap.
    """
    import httpx

    base_url = os.getenv("MP2_MODEL_GATEWAY_URL", "http://model-gateway:8080")
    keys = list(state.get("scene_packet_keys", []))
    if not keys:
        return _done(state, "InterpretSemantics", semantic_claims=0,
                     warnings=_warn(state, "no scene packets to interpret"))

    store = object_store()
    created, failures = 0, 0
    # One client for the whole activity; a client per packet would churn connections.
    with httpx.Client(timeout=180) as client, session_scope() as session:
        run_id = _run_id(state)
        for key in keys:
            path = work_dir(state["analysis_run_id"]) / "packets" / Path(key).name
            packet = json.loads(materialize(store, DERIVED_BUCKET, key, path)
                                .read_text(encoding="utf-8"))
            segment_id = uuid.UUID(packet["scene"]["segment_id"])

            request = {
                "request_id": str(uuid.uuid5(
                    uuid.NAMESPACE_URL, f"mp2:{run_id}:semantics:{segment_id}")),
                "capability": "scene_semantic_interpretation",
                "capability_schema_version": "1",
                "bounded_input": packet,
                "privacy_class": state.get("rights_access_class", "private"),
                "quality_tier": "draft",
                "maximum_cost": 0,
                "determinism_requirement": "preferred",
                "allowed_provider_classes": ["local"],
                "output_schema_id": EVIDENCE_SCHEMA_VERSION,
                "trace_id": str(run_id),
            }
            try:
                response = client.post(f"{base_url}/v1/execute", json=request)
                if response.status_code != 200:
                    log.warning("gateway returned %s for segment %s",
                                response.status_code, segment_id)
                    failures += 1
                    continue
                body = response.json()
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                # Unreachable gateway, malformed JSON or a missing contract field. The run
                # keeps its deterministic evidence rather than inventing a claim.
                log.warning("semantic interpretation failed for segment %s: %s",
                            segment_id, type(exc).__name__)
                failures += 1
                continue

            execution = session.scalar(
                select(ModelExecution).where(
                    ModelExecution.request_id == uuid.UUID(request["request_id"]))
            )
            if execution is None:
                execution = ModelExecution(
                    analysis_run_id=run_id,
                    request_id=uuid.UUID(request["request_id"]),
                    provider_adapter=body["provider_adapter"],
                    model_tool_id=body["model_tool_id"],
                    model_tool_version=body["model_tool_version"],
                    execution_location=body["execution_location"],
                    parameters=body.get("parameters", {}),
                    usage=body.get("usage", {}),
                    estimated_cost=body.get("estimated_cost", 0),
                    latency_ms=body.get("latency_ms", 0),
                    confidence=body.get("confidence"),
                    warnings=body.get("warnings", []),
                    content_hash=body["content_hash"],
                )
                session.add(execution)
                session.flush()

            for claim in body.get("structured_output", {}).get("claims", []):
                existing = session.scalar(
                    select(EvidenceClaim).where(
                        EvidenceClaim.analysis_run_id == run_id,
                        EvidenceClaim.segment_id == segment_id,
                        EvidenceClaim.claim_type == claim["claim_type"],
                    )
                )
                refs = list(claim.get("evidence_refs", [])) or [
                    f"s3://{DERIVED_BUCKET}/{key}"]
                if existing is not None:
                    existing.structured_value = claim["structured_value"]
                    existing.confidence = float(claim["confidence"])
                    existing.evidence_refs = refs
                    existing.model_execution_id = execution.id
                    continue
                session.add(EvidenceClaim(
                    analysis_run_id=run_id, segment_id=segment_id,
                    model_execution_id=execution.id, claim_type=claim["claim_type"],
                    structured_value=claim["structured_value"],
                    confidence=float(claim["confidence"]), evidence_refs=refs,
                    schema_version=EVIDENCE_SCHEMA_VERSION,
                ))
                created += 1

    warnings = state.get("warnings", [])
    if failures:
        warnings = _warn(state,
                         f"semantic interpretation unavailable for {failures} scene packet(s); "
                         "deterministic evidence retained")
    return _done(state, "InterpretSemantics", semantic_claims=created,
                 semantic_failures=failures, warnings=warnings)


# --- 10. AssembleEvidence ------------------------------------------------------------------

@activity.defn(name="AssembleEvidence")
@instrumented("AssembleEvidence")
def assemble_evidence(state: State) -> State:
    with session_scope() as session:
        run_id = _run_id(state)
        measurements = session.scalars(
            select(Measurement).where(Measurement.analysis_run_id == run_id)).all()
        claims = session.scalars(
            select(EvidenceClaim).where(EvidenceClaim.analysis_run_id == run_id)).all()

        by_metric: dict[str, int] = {}
        for row in measurements:
            by_metric[row.metric] = by_metric.get(row.metric, 0) + 1

        summary = {
            "measurement_count": len(measurements),
            "measurements_by_metric": by_metric,
            "evidence_claim_count": len(claims),
            "segment_count": len(state.get("segment_ids", [])),
            "scene_packet_count": state.get("scene_packet_count", 0),
            "asr_available": state.get("asr_available", False),
            "warnings": state.get("warnings", []),
            "activity_timings_ms": state.get("activity_timings_ms", {}),
            "activity_total_ms": sum(state.get("activity_timings_ms", {}).values()),
        }
        upsert_measurement(session, run_id, TEXT_STATISTICS, "run.evidence_summary", summary,
                           source_ref=f"mp2:analysis-run/{run_id}")
        _set_status(session, run_id, RunStatus.EVIDENCE_READY)

    return _done(state, "AssembleEvidence", evidence_summary=summary)


# --- Deferred boundaries (M5+) --------------------------------------------------------------
# These exist so the workflow's later shape is fixed now. They must not invent psychometric
# values; genome calculation and projection design are explicitly out of scope for M0-M4.

@activity.defn(name="CalculateGenome")
@instrumented("CalculateGenome")
def calculate_genome(state: State) -> State:
    return _done(state, "CalculateGenome", genome="deferred-to-M5")


@activity.defn(name="BuildProjections")
@instrumented("BuildProjections")
def build_projections(state: State) -> State:
    return _done(state, "BuildProjections", projections="deferred-to-M6")


@activity.defn(name="ValidateAnalysis")
@instrumented("ValidateAnalysis")
def validate_analysis(state: State) -> State:
    return _done(state, "ValidateAnalysis")


@activity.defn(name="PublishVersion")
@instrumented("PublishVersion")
def publish_version(state: State) -> State:
    return _done(state, "PublishVersion", published="deferred-to-M5")


# --- Compensation --------------------------------------------------------------------------

@activity.defn(name="MarkRunFailed")
def mark_run_failed(payload: dict[str, Any]) -> dict[str, Any]:
    """Terminal failure marker. Idempotent: re-marking a failed run is a no-op."""
    with session_scope() as session:
        run_id = uuid.UUID(payload["analysis_run_id"])
        _set_status(session, run_id, RunStatus.FAILED, error=payload.get("error"))
    log.error("analysis run %s marked failed: %s",
              payload["analysis_run_id"], payload.get("error"))
    return {"analysis_run_id": payload["analysis_run_id"], "status": RunStatus.FAILED.value}


ALL_ACTIVITIES = [validate_source, probe_and_hash, normalize_asset, segment_shots,
                  extract_visual, extract_audio, transcribe, build_scene_packets,
                  interpret_semantics, assemble_evidence, calculate_genome,
                  build_projections, validate_analysis, publish_version, mark_run_failed]
