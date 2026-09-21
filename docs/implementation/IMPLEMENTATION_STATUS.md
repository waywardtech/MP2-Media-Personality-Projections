# Implementation status

Updated 2026-09-21.

This file distinguishes three things deliberately, because conflating them is how a project
starts believing its own scaffolding:

- **Implemented** — the code exists.
- **Verified** — it was run on DEV-01 and observed to work.
- **Not tested** — it exists but has never been exercised. Treated as unproven.

## Acceptance gates

All results below were produced on DEV-01 on 2026-09-21 against the built images and a
running stack. Nothing is marked verified on the strength of the code alone.

| Gate | State | Evidence actually observed |
|---|---|---|
| **G0** Environment | **Verified** | Live audit (DEV-01-AUDIT.md). WSL2 + Ubuntu 24.04 + systemd; Docker Engine 29.1.3 and Compose v2.40.3 inside Linux. `ruff` clean, `mypy --strict` clean on 19 files, 37 unit/reproducibility tests pass. |
| **G1** Infrastructure | **Verified** | Six core services healthy (~60 s). Data survived `down`, a cross-volume WSL distro move and a full WSL restart: row counts and SHA-256 unchanged. Migrations `0001`+`0002` applied; all three natural-key constraints present. `pg_dump` produced a 64,654 B dump. |
| **G2** Ingest | **Verified** | Work registered, asset hashed (SHA-256 matched the fixture exactly), ffprobe probed, object stored in `mp2-raw`, run created, workflow dispatched. Rights metadata enforced (**422**); ingest-root containment enforced (**403** on `/etc/hostname`). Asserted by 3 integration tests. |
| **G3** Extraction | **Verified** | `synthetic-speech.mp4` (8.58 s) → 2 shot segments (cut at 4,292 ms, the exact fixture midpoint), 17 measurements from 7 extractors. ASR returned `en` at p=0.974 with 2 utterances of real transcribed speech. D0 rerun equality by content hash and D1 by numeric tolerance, including a cross-process check. |
| **G4** Evidence | **Verified for the local path** | 2 scene packets written to `mp2-derived`; 8 evidence claims, every one linked to a segment, a `model_execution` and non-empty evidence refs; 2 model executions, `execution_location=local`, `estimated_cost=0`. External routing disabled. **The generative (llama.cpp) path is untested** — see deviations. |
| **G5** Resilience | **Verified** | Worker SIGKILLed while the run was in progress; a new worker resumed and the run reached `evidence_ready`. Totals equalled distinct natural keys exactly: measurements 17/17, segments 2/2, claims 8/8, executions 2/2. **Zero duplicate canonical rows.** |
| **G6** Portability | **Verified** | AST test rejects provider SDK imports in `packages/domain` and `packages/schemas`. Scene packet asserted to contain no genome, projection or provider fields, no media bytes, and to reject unknown fields. No vendor-specific field is needed to read stored evidence. |
| **G7** Security | **Verified for this stage** | `.env` gitignored and untracked; no credentials in the tree. Worker egress to a public model provider **blocked** (`URLError`) by internal networks. Gateway reports `external_models_enabled=false`, routing to `mp2-deterministic-baseline`. Only `127.0.0.1` publishing. Significant gaps remain — see SECURITY.md. |

### Test results

```
mp2.sh test               45 passed, 10 skipped   47.7 s
mp2.sh test-integration   10 passed               12.4 s   (against the live stack)
mp2.sh lint               clean
mp2.sh typecheck          clean (mypy --strict)
```

The 10 skips are the integration tests skipping when no stack is reachable; they pass under
`test-integration`, which joins the `app` network.

### Scaling measured

A synthetic fixture ladder (varying duration, resolution and shot count independently) was
analysed end to end. Full results and corpus projections: BENCHMARKS.md.

- **Steady state is faster than realtime**: 300 s of video analysed in 44.4 s (0.15×).
- **Resolution is the dominant driver.** 4× the pixels costs 3.84× in `ExtractVisual` —
  essentially linear. Duration scales sub-linearly.
- **Cold start is ~40 s** of numba JIT in the first `ExtractAudio` of a fresh worker
  process, then 0.7–6.8 s. One-off per worker, not per work.
- MP2 adds ~1.96 MB of storage per media-minute, ~98% of it uncompressed normalized audio.
- Projected 50-work corpus: ~338k measurement rows, ~10.8 GB MP2-added storage, and
  ~13.6 h (360p) to ~52 h (1080p) of single-worker wall clock.

### Also verified

- **Web console** (`ui` profile) builds, serves on `127.0.0.1:5173`, proxies `/api/` through
  nginx, and renders real data: live health, 5 analysis runs, and an evidence browser
  showing measurements with extractor@version, repeatability class and content hashes,
  claims with confidence and evidence refs, and the local model execution.
- **Backup** (`mp2.sh backup`) produces a restorable custom-format dump.

## Milestones

| Milestone | State | Notes |
|---|---|---|
| M0 repository | Complete | Production-shaped monorepo, Python 3.12 packaging, web lockfile, ruff clean, docs, license record. |
| M1 infrastructure | Complete | Profiled Compose stack, five named networks (four internal), health checks, resource limits, named volumes. |
| M1/M2 data foundation | Complete | 20 canonical tables, UUID + version lineage, pgvector 0.8.6, `ObjectStore` protocol with S3 and local adapters, six buckets. |
| M2 ingest | Complete | Rights-gated, hash-verified, ffprobe-probed, object-stored, workflow-dispatched. |
| M3 deterministic extraction | Complete | ffprobe, ffmpeg normalization, PySceneDetect, OpenCV, librosa, faster-whisper, dialogue statistics — all versioned, licensed and lineage-bearing. |
| M4 gateway + evidence | Complete for the local path | Provider-neutral contracts, scene packets, evidence claims, model executions, deterministic local adapter. Generative adapter untested. |
| M5+ genome / projections | **Not started, by design** | Declared as workflow boundaries and implemented as explicit deferrals. |

## What changed in this session

The repository previously contained a scaffold whose status file described M3/M4 as
"Partial". In fact **all fourteen Temporal activities were no-op stubs** that performed no
extraction, wrote nothing to PostgreSQL and never updated run status. A workflow could
complete while the analysis run sat at `created` forever.

Replaced with real implementations:

- **Activities now do the work** — probe, normalize, segment, extract visual/audio,
  transcribe, build packets, interpret, assemble — and persist measurements, segments,
  evidence claims and model executions with full lineage.
- **Extractor registry** with runtime-resolved versions, repeatability classes and
  mandatory license records.
- **Idempotency**: natural-key upserts plus migration `0002` constraints
  (`UNIQUE NULLS NOT DISTINCT`), so retries converge instead of duplicating.
- **Deterministic baseline adapter** behind the Model Gateway (ADR 0002), making the
  semantic boundary testable without model weights.
- **Sync activities on a thread pool.** Activities were `async def` while calling blocking
  libraries, which stalls Temporal's event loop. They are now synchronous and run on a
  bounded `ThreadPoolExecutor`.
- **Run status is persisted**, including terminal failure via `MarkRunFailed`.
- **Real tests**: extractor registry invariants, baseline adapter contract, D0/D1
  reproducibility, and an end-to-end integration suite.
- **Web console** now reads real data from the API instead of rendering placeholders.
- **Environment**: WSL distro moved off a nearly-full C:; `.wslconfig` written to make the
  resource policy explicit and stop WSL killing the stack between commands.

### Infrastructure defects found and fixed while verifying

None of these were visible without actually running the stack:

1. **Temporal was unreachable despite being healthy.** `temporalio/auto-setup` binds the
   frontend to one auto-detected interface address, not `0.0.0.0`. Attached to two
   networks, Docker DNS resolved the service name to the address it was *not* bound to, so
   every client got `connection refused` and the worker never started. Temporal is now on
   `app` only.
2. **Temporal's health check used deprecated `tctl` and had no `start_period`**, so on this
   machine's storage it was declared unhealthy before it finished starting. Now uses the
   `temporal` CLI with `start_period: 180s`.
3. **`ports:` on postgres, temporal and seaweedfs never took effect.** A container attached
   solely to internal networks cannot publish to the host. The entries were dead config
   implying an exposure that did not exist; removed and documented.
4. **Admin and ops UIs could never have been reachable** for the same reason — they were on
   internal networks only. They are now also on `edge`, and Temporal UI was confirmed
   serving on `127.0.0.1:8233`.
5. **`--profile ui` alone was an invalid Compose project** (`web` depends on `api` in
   `core`). `up-ui` and `up-admin` now enable both profiles.
6. **numba could not write its JIT cache** as the unprivileged user, failing audio
   extraction on first use. Fixed with `NUMBA_CACHE_DIR`.
7. **Every code change forced a full dependency reinstall** because source was copied
   before `pip install`. Restructured; dependency list is now derived from
   `pyproject.toml` so it cannot drift.
8. **`mp2.sh restore` could not find a relative dump path**, and `mp2.sh check` could not
   re-invoke itself, both because the script `cd`s on startup.

## Known deviations from the brief

1. **No NVIDIA GPU.** The brief's hardware profile (FX-8300, GTX 1070) does not match
   DEV-01 (i7-4770, AMD RX 580). There is no CUDA path. The `gpu` profile is defined but
   unusable here, and everything runs CPU-only. Not a deviation from the architecture —
   the GPU work is still separable.
2. **The generative local path is untested.** llama.cpp is implemented and wired, but no
   GGUF weights were approved for download, so no generative model has ever run. The
   deterministic baseline adapter fills the routing slot (ADR 0002). **Do not report the
   generative path as working.**
3. **sentence-transformers is not installed.** It pulls PyTorch (~2.5 GB). The embedding
   adapter is declared in the registry as unavailable, and no embeddings are produced.
4. **Migration `0001` uses `Base.metadata.create_all`** rather than explicit DDL. It
   produces a correct schema, but `--autogenerate` against later model changes can be
   noisy. Worth replacing with explicit DDL before the schema stabilises.
5. **OpenTelemetry is implemented but the `ops` backend is still unexercised.** The API,
   worker and gateway are instrumented (FastAPI, SQLAlchemy, httpx, plus a span per
   activity) and emit OTLP over HTTP. It is **off by default**: with no
   `OTEL_EXPORTER_OTLP_ENDPOINT` the whole path is a no-op, which is asserted by tests.
   What has *not* been done is standing up the `ops` profile and confirming spans arrive in
   Prometheus/Grafana end to end.
6. **Line-ending hazard.** Editing files from Windows can introduce CRLF, which breaks
   shell scripts. `.gitattributes` normalizes on commit; the repo was normalized to LF.

## Not implemented (deliberately out of scope for M0–M4)

- Genome calculation, psychometric mapping (Big Five / MBTI / Enneagram), projection design.
- Viewer profiles, state/context capture, recommendation logic — tables exist, no logic.
- The 50-work gold corpus. **No corpus ingestion has been performed or automated.**
- Any paid or external model processing.
- Authentication, authorization, TLS, audit logging, secret management (SECURITY.md).
- `ops`, `gpu` and `llm` Compose profiles have not been started on this machine.
  (`ui` and `admin` have been started and verified.)

## Immediate next steps

1. `wsl --shutdown` to apply `.wslconfig` (12 GB / 6 threads / swap on D:).
2. Commit and push — the working tree is still entirely untracked against
   `waywardtech/MP2-Media-Personality-Projections`. Set a Git identity in WSL first.
3. Analyse one user-supplied, legally appropriate short work end to end to get realistic
   throughput and storage numbers. **Nothing is ingested automatically.**
4. Decide whether to approve a small quantized GGUF so the generative path can be tested
   against the deterministic baseline.
5. Replace migration `0001` with explicit DDL before the schema stabilises.
