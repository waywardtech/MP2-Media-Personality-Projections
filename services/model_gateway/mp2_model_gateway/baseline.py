"""Deterministic local baseline adapter.

This adapter satisfies the MP2 Model Gateway contract without any model weights. It does
not generate prose and it does not guess: every claim it emits is a direct, auditable
restatement of deterministic measurements already present in the scene packet, with a
confidence derived from how much measurement coverage backed it.

Why it exists
-------------
1. It keeps the semantic boundary exercisable and testable on a machine with no local
   generative weights installed, so evidence linkage can be verified end to end.
2. It is the control arm. When a generative adapter is enabled, its output is compared
   against this baseline rather than against nothing.
3. It proves model independence: two local adapters, one provider-neutral contract.

It is explicitly NOT a psychological interpreter. It produces descriptive scene evidence
only. Genome calculation and psychometric mapping are out of scope for M0-M4.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from mp2_schemas.gateway import ModelGatewayRequest, ModelGatewayResponse

ADAPTER_ID = "mp2-deterministic-baseline"
ADAPTER_VERSION = "1"


def _band(value: float, low: float, high: float) -> str:
    if value <= low:
        return "low"
    return "high" if value >= high else "moderate"


def _claims(packet: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    visual = packet.get("visual_measurements") or {}
    audio = packet.get("audio_measurements") or {}
    dialogue = packet.get("dialogue_measurements") or {}
    scene = packet.get("scene") or {}
    warnings: list[str] = []
    claims: list[dict[str, Any]] = []

    duration_s = max((int(scene.get("end_ms", 0)) - int(scene.get("start_ms", 0))), 0) / 1000.0
    ref = f"mp2:segment/{scene.get('segment_id')}"

    if visual.get("frames_sampled"):
        luminance = float(visual.get("luminance_mean") or 0.0)
        saturation = float(visual.get("saturation_mean") or 0.0)
        edges = float(visual.get("edge_density") or 0.0)
        motion = visual.get("motion_magnitude_mean")
        claims.append({
            "claim_type": "scene.visual_character",
            "structured_value": {
                "brightness_band": _band(luminance, 60.0, 170.0),
                "luminance_mean": luminance,
                "saturation_band": _band(saturation, 40.0, 150.0),
                "saturation_mean": saturation,
                "visual_complexity_band": _band(edges, 0.03, 0.15),
                "edge_density": edges,
                "motion_band": (_band(float(motion), 0.5, 3.0)
                                if motion is not None else "unmeasured"),
                "basis": "opencv-visual deterministic measurements",
            },
            # Confidence reflects sampling coverage, not model certainty.
            "confidence": min(0.4 + 0.1 * int(visual.get("frames_sampled", 0)), 0.75),
            "evidence_refs": [ref, "mp2:measurement/visual.segment_measures"],
        })
    else:
        warnings.append("no visual measurements in packet")

    if audio.get("samples"):
        silence = float(audio.get("silence_ratio") or 0.0)
        centroid = float(audio.get("spectral_centroid_mean") or 0.0)
        claims.append({
            "claim_type": "scene.audio_character",
            "structured_value": {
                "silence_band": _band(silence, 0.1, 0.6),
                "silence_ratio": silence,
                "loudness_rms_mean": float(audio.get("rms_mean") or 0.0),
                "spectral_brightness_band": _band(centroid, 800.0, 3000.0),
                "spectral_centroid_mean": centroid,
                "tempo_candidate": audio.get("tempo_candidate"),
                "dynamic_range_proxy": audio.get("dynamic_range_proxy"),
                "basis": "librosa deterministic measurements",
            },
            "confidence": 0.6,
            "evidence_refs": [ref, "mp2:measurement/audio.segment_measures"],
        })
    else:
        warnings.append("no audio measurements in packet")

    density = dialogue.get("dialogue_density")
    claims.append({
        "claim_type": "scene.dialogue_character",
        "structured_value": {
            "utterance_count": int(dialogue.get("utterance_count") or 0),
            "word_count": int(dialogue.get("word_count") or 0),
            "dialogue_density": density,
            "words_per_minute": dialogue.get("words_per_minute"),
            "speech_presence": bool(dialogue.get("utterance_count")),
            "transcript_present": bool(packet.get("transcript_text")),
            "basis": "ASR-derived utterance statistics; no speaker identity derived",
        },
        "confidence": 0.5 if dialogue else 0.25,
        "evidence_refs": [ref, "mp2:measurement/dialogue.segment_measures"],
    })

    claims.append({
        "claim_type": "scene.descriptor",
        "structured_value": {
            "segment_index": scene.get("index"),
            "duration_s": duration_s,
            "measurement_families_present": sorted(
                name for name, present in (
                    ("visual", bool(visual.get("frames_sampled"))),
                    ("audio", bool(audio.get("samples"))),
                    ("dialogue", bool(dialogue)),
                ) if present
            ),
            "interpretation_tier": "deterministic-baseline",
        },
        "confidence": 0.9,
        "evidence_refs": [ref],
    })
    return claims, warnings


class DeterministicBaselineAdapter:
    """Local, weightless, D0-repeatable semantic adapter."""

    async def execute(self, request: ModelGatewayRequest) -> ModelGatewayResponse:
        if "local" not in request.allowed_provider_classes:
            raise PermissionError("request does not allow local providers")
        started = time.perf_counter()
        packet = request.bounded_input or {}
        claims, warnings = _claims(packet)
        output = {"schema_version": request.output_schema_id, "claims": claims}
        canonical = json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
        return ModelGatewayResponse(
            structured_output=output,
            provider_adapter=ADAPTER_ID,
            model_tool_id=ADAPTER_ID,
            model_tool_version=ADAPTER_VERSION,
            execution_location="local",
            parameters={"deterministic": True, "capability": request.capability,
                        "repeatability_class": "D0"},
            usage={"scene_packets": 1, "claims": len(claims)},
            estimated_cost=0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            confidence=None,
            warnings=warnings,
            run_id=str(uuid.uuid4()),
            content_hash=hashlib.sha256(canonical).hexdigest(),
        )
