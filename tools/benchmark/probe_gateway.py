"""Send one real scene packet through the gateway and report validity + throughput."""
import json
import time
import urllib.request

from mp2_schemas.scene_packet import SceneEvidenceOutput

PACKET = {
    "schema_version": "scene-packet/1",
    "analysis_run_id": "00000000-0000-0000-0000-000000000001",
    "source_asset_id": "00000000-0000-0000-0000-000000000002",
    "scene": {"segment_id": "00000000-0000-0000-0000-000000000003",
              "index": 0, "start_ms": 0, "end_ms": 4292},
    "transcript_text": "The quick brown fox jump over the rainy dog.",
    "utterances": [{"index": 0, "start_s": 0.1, "end_s": 3.9,
                    "text": "The quick brown fox jump over the rainy dog."}],
    "visual_measurements": {"frames_sampled": 3, "luminance_mean": 93.1,
                            "saturation_mean": 180.9, "edge_density": 0.0184,
                            "motion_magnitude_mean": 0.0000001},
    "audio_measurements": {"samples": 68672, "silence_ratio": 0.12, "rms_mean": 0.081,
                           "spectral_centroid_mean": 1642.0, "tempo_candidate": 123.0,
                           "dynamic_range_proxy": 0.42},
    "dialogue_measurements": {"utterance_count": 1, "word_count": 9,
                              "dialogue_density": 0.87, "words_per_minute": 126.0},
    "representative_frame_refs": [], "frame_embedding_refs": [],
    "preceding_context_summary": None, "following_context_summary": None,
    "known_character_aliases": [],
    "schema_instruction": "Return JSON only, matching the declared output schema.",
}

request = {
    "request_id": "11111111-1111-1111-1111-111111111111",
    "capability": "scene_semantic_interpretation",
    "capability_schema_version": "1",
    "bounded_input": PACKET,
    "privacy_class": "internal_test",
    "quality_tier": "draft",
    "maximum_cost": 0,
    "determinism_requirement": "preferred",
    "allowed_provider_classes": ["local"],
    "output_schema_id": "scene-evidence/1",
    "trace_id": "probe",
}

started = time.perf_counter()
req = urllib.request.Request(
    "http://model-gateway:8080/v1/execute",
    data=json.dumps(request).encode(),
    headers={"content-type": "application/json"},
)
body = json.load(urllib.request.urlopen(req, timeout=900))
elapsed = time.perf_counter() - started

print(f"adapter        : {body['provider_adapter']}")
print(f"model          : {body['model_tool_id']}@{body['model_tool_version']}")
print(f"location/cost  : {body['execution_location']} / {body['estimated_cost']}")
print(f"latency        : {body['latency_ms']} ms   (wall {elapsed:.1f}s)")
print(f"usage          : {body.get('usage')}")
print(f"warnings       : {body.get('warnings')}")
print(f"content_hash   : {body['content_hash'][:16]}")

usage = body.get("usage") or {}
ct = usage.get("completion_tokens")
if ct and body["latency_ms"]:
    print(f"throughput     : {ct / (body['latency_ms']/1000):.1f} tok/s output")

print("\n--- structured_output ---")
print(json.dumps(body["structured_output"], indent=2)[:2500])

# The whole point of grammar-constrained decoding: this must parse.
try:
    parsed = SceneEvidenceOutput.model_validate(body["structured_output"])
    print(f"\nSCHEMA VALIDATION: PASS ({len(parsed.claims)} claims)")
    for claim in parsed.claims:
        print(f"  {claim.claim_type:<28} conf={claim.confidence:.2f} "
              f"refs={len(claim.evidence_refs)}")
except Exception as exc:
    print(f"\nSCHEMA VALIDATION: FAIL {type(exc).__name__}: {exc}")
