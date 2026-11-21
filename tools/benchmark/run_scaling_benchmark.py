#!/usr/bin/env python3
"""Measure how analysis cost scales with duration, resolution and shot count.

Produces the numbers needed to plan corpus-scale work: wall-clock per minute of content,
per-activity breakdown, rows written per minute, and object-storage bytes per minute. A
single short clip cannot answer any of those, and extrapolating from one is how storage
and time budgets go wrong.

Run on the app network so it can reach both the API and object storage:

    docker run --rm --network mp2_app \\
      -e MP2_API_URL=http://api:8000 \\
      -e MP2_S3_ENDPOINT=http://seaweedfs:8333 \\
      -e MP2_S3_ACCESS_KEY=mp2-dev -e MP2_S3_SECRET_KEY=change-me \\
      -e MP2_S3_REGION=us-east-1 \\
      -v "$PWD:/repo" -w /repo --entrypoint python mp2-api:latest \\
      tools/benchmark/run_scaling_benchmark.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

import httpx

API = os.getenv("MP2_API_URL", "http://api:8000")
TIMEOUT_S = int(os.getenv("MP2_BENCH_TIMEOUT_S", "3600"))
TERMINAL = {"evidence_ready", "failed"}

# (fixture, label) - must already exist in the ingest root (mp2.sh bench-fixtures).
FIXTURES = [
    ("/media/synthetic-speech.mp4", "8.6s 320x180 2shot (speech)"),
    ("/media/bench-30s-360p-06.mp4", "30s 640x360 6shot"),
    ("/media/bench-60s-360p-12.mp4", "60s 640x360 12shot"),
    ("/media/bench-60s-720p-12.mp4", "60s 1280x720 12shot"),
    ("/media/bench-300s-360p-60.mp4", "300s 640x360 60shot"),
]


def bucket_bytes() -> dict[str, int]:
    """Total bytes per MP2 bucket, so storage growth can be attributed to a run."""
    try:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=os.environ["MP2_S3_ENDPOINT"],
            aws_access_key_id=os.environ["MP2_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["MP2_S3_SECRET_KEY"],
            region_name=os.environ.get("MP2_S3_REGION", "us-east-1"),
        )
        totals: dict[str, int] = {}
        for bucket in ("mp2-raw", "mp2-normalized", "mp2-derived"):
            objs = client.list_objects_v2(Bucket=bucket).get("Contents", [])
            totals[bucket] = sum(o["Size"] for o in objs)
        return totals
    except Exception as exc:  # storage accounting is a nice-to-have, not the measurement
        print(f"  (bucket accounting unavailable: {type(exc).__name__})", file=sys.stderr)
        return {}


def analyze(client: httpx.Client, path: str, label: str) -> dict[str, Any]:
    before = bucket_bytes()

    work = client.post("/v1/media", json={
        "title": f"Benchmark {label}", "work_type": "benchmark",
        "edition_label": "bench"}).json()
    asset_response = client.post(f"/v1/media/{work['id']}/assets", json={
        "local_path": path,
        "provenance_class": "synthetic",
        "rights_access_class": "redistributable",
        "retention_class": "ephemeral",
    })
    if asset_response.status_code != 201:
        return {"label": label, "error": f"ingest {asset_response.status_code}: "
                                        f"{asset_response.text[:200]}"}
    asset = asset_response.json()

    started = time.perf_counter()
    run = client.post(f"/v1/media/{work['id']}/analyze", json={
        "source_asset_id": asset["id"], "parameters": {"benchmark": label}}).json()

    status = "created"
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        time.sleep(3)
        status = client.get(f"/v1/analysis-runs/{run['id']}").json()["status"]
        if status in TERMINAL:
            break
    wall_s = time.perf_counter() - started

    evidence = client.get(f"/v1/analysis-runs/{run['id']}/evidence").json()
    summary: dict[str, Any] = {}
    duration_s = None
    for m in evidence["measurements"]:
        if m["metric"] == "run.evidence_summary":
            summary = m["value"]
        elif m["metric"] == "media.probe_summary":
            duration_s = m["value"].get("duration_s")

    after = bucket_bytes()
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in after} if after else {}

    return {
        "label": label,
        "status": status,
        "duration_s": duration_s,
        "wall_s": round(wall_s, 1),
        "realtime_factor": round(wall_s / duration_s, 2) if duration_s else None,
        "segments": len(evidence["segments"]),
        "measurements": len(evidence["measurements"]),
        "claims": len(evidence["evidence_claims"]),
        "executions": len(evidence["model_executions"]),
        "timings_ms": summary.get("activity_timings_ms", {}),
        "storage_delta": delta,
        "run_id": run["id"],
    }


def main() -> int:
    results: list[dict[str, Any]] = []
    with httpx.Client(base_url=API, timeout=60) as client:
        client.get("/health").raise_for_status()
        for path, label in FIXTURES:
            print(f"--- {label} ---", flush=True)
            result = analyze(client, path, label)
            results.append(result)
            if "error" in result:
                print(f"  SKIPPED: {result['error']}", flush=True)
            else:
                print(f"  {result['status']} in {result['wall_s']}s "
                      f"({result['realtime_factor']}x realtime), "
                      f"{result['segments']} segments, "
                      f"{result['measurements']} measurements", flush=True)

    ok = [r for r in results if "error" not in r and r["status"] == "evidence_ready"]

    print("\n" + "=" * 100)
    print("SCALING RESULTS")
    print("=" * 100)
    print(f"{'fixture':<30} {'media':>8} {'wall':>8} {'xRT':>7} "
          f"{'segs':>5} {'meas':>5} {'claims':>7}")
    print("-" * 100)
    for r in ok:
        print(f"{r['label']:<30} {r['duration_s'] or 0:>7.1f}s {r['wall_s']:>7.1f}s "
              f"{r['realtime_factor'] or 0:>6.2f}x {r['segments']:>5} "
              f"{r['measurements']:>5} {r['claims']:>7}")

    print("\nPER-ACTIVITY BREAKDOWN (seconds)")
    print("-" * 100)
    names: list[str] = []
    for r in ok:
        for n in r["timings_ms"]:
            if n not in names:
                names.append(n)
    print(f"{'activity':<22}" + "".join(f"{r['label'].split()[0]:>14}" for r in ok))
    for n in names:
        row = f"{n:<22}"
        for r in ok:
            ms = r["timings_ms"].get(n)
            row += f"{(ms / 1000):>13.1f}s" if ms is not None else f"{'-':>14}"
        print(row)

    print("\nSTORAGE PER RUN (bytes added)")
    print("-" * 100)
    for r in ok:
        d = r["storage_delta"]
        total = sum(d.values()) if d else 0
        per_min = (total / (r["duration_s"] / 60)) if r["duration_s"] else 0
        print(f"{r['label']:<30} raw={d.get('mp2-raw', 0):>10,}  "
              f"norm={d.get('mp2-normalized', 0):>10,}  "
              f"derived={d.get('mp2-derived', 0):>9,}  "
              f"total/min={per_min:>12,.0f}")

    out = "data/benchmark-results.json"
    os.makedirs("data", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote {out}")
    return 0 if len(ok) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
