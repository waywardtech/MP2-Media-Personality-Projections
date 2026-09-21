# Licenses and models

MP2 is proprietary software built on open foundations. This file records every third-party
runtime, every model weight and every tool whose license affects what MP2 may ship or do.

Two rules govern this document:

1. **No model weights are downloaded silently.** Anything listed under "Model weights" was
   introduced with explicit approval and is recorded here with its license.
2. **A license record is mandatory.** `tests/unit/test_extractor_registry.py` fails if any
   registered extractor carries `license_record = "unrecorded"`, so this file cannot drift
   out of date without the suite going red.

## What MP2 owns

Proprietary and not derived from any dependency: the ontology, the canonical schemas, the
derived corpus, the scene-packet contract, the evidence model, the extractor registry,
calibration data, scoring and recommendation logic, and the application code in this
repository.

## Runtime dependencies (deployable images)

| Component | Version in image | License | Role |
|---|---|---|---|
| Python | 3.12 (slim-bookworm) | PSF-2.0 | Runtime |
| FastAPI | >=0.115,<1 | MIT | API + model gateway |
| Pydantic | >=2.10,<3 | MIT | Canonical schemas |
| SQLAlchemy | >=2.0,<3 | MIT | Data access |
| Alembic | >=1.14,<2 | MIT | Migrations |
| psycopg | >=3.2,<4 | LGPL-3.0-or-later | PostgreSQL driver |
| temporalio (Python SDK) | >=1.8,<2 | MIT | Durable workflows |
| boto3 | >=1.35,<2 | Apache-2.0 | S3-compatible object access |
| httpx | >=0.28,<1 | BSD-3-Clause | Gateway HTTP transport |
| uvicorn | >=0.34,<1 | BSD-3-Clause | ASGI server |
| **FFmpeg / ffprobe** | Debian bookworm build (7.x) | **LGPL-2.1-or-later** (Debian default build) | Probe + audio normalization |
| PySceneDetect | 0.7.1 | BSD-3-Clause | Shot boundaries |
| OpenCV (headless) | 5.0.0 | Apache-2.0 | Visual measures |
| librosa | 0.11.0 | ISC | Audio measures |
| numpy / scipy | 2.x / 1.x | BSD-3-Clause | Numerics |
| numba / llvmlite | current | BSD-2-Clause | librosa JIT |
| soundfile / libsndfile | >=0.12 / system | BSD-3-Clause / LGPL-2.1 | Audio I/O |
| faster-whisper | >=1.1,<2 | MIT | ASR runtime |
| CTranslate2 | pulled by faster-whisper | MIT | ASR inference engine |

### FFmpeg note

MP2 invokes `ffmpeg` and `ffprobe` as **separate processes**, never by linking against
libav*. The Debian build is LGPL and is not recompiled with `--enable-gpl` or
`--enable-nonfree`. If a GPL-only or nonfree FFmpeg build is ever substituted, the
distribution terms of any image containing it change — do not do this without review.

## Infrastructure images

| Image | Pinned tag | License |
|---|---|---|
| `pgvector/pgvector` | `pg18` | PostgreSQL License; pgvector PostgreSQL License |
| `temporalio/auto-setup` | `1.28` | MIT |
| `temporalio/ui` | `2.42.1` | MIT (admin profile only) |
| `chrislusf/seaweedfs` | `3.80` | Apache-2.0 |
| `otel/opentelemetry-collector-contrib` | `0.119.0` | Apache-2.0 |
| `prom/prometheus` | `v3.1.0` | Apache-2.0 |
| `grafana/grafana` | `11.4.0` | AGPL-3.0 — **ops profile only** |

**Grafana is AGPL-3.0.** It is an operator-facing dashboard in an optional profile, is never
linked into MP2 code, and is not part of any MP2 deployable. It must not be embedded into a
product surface offered to third parties without legal review.

## Model weights

| Model | Version | Size | License | Status |
|---|---|---|---|---|
| Whisper (CTranslate2 conversion) | `Systran/faster-whisper-tiny` | ~75 MB | **MIT** (OpenAI Whisper weights; Systran CT2 conversion) | **Installed**, baked into the image at build time with explicit approval |
| all-MiniLM-L6-v2 | — | ~90 MB | Apache-2.0 | **Not installed.** Requires PyTorch (~2.5 GB); declared in the registry as an unavailable adapter |
| llama.cpp GGUF model | — | — | model-specific | **Not installed.** No generative weights are present on DEV-01 |

### How Whisper weights are handled

They are downloaded **at image build time** into `/opt/mp2/models/whisper` and never
fetched at runtime, so local-only mode works with egress blocked. To build an image with no
model weights at all:

```bash
docker build -f infra/containers/python.Dockerfile --build-arg MP2_WHISPER_REPO= -t mp2-api .
```

`mp2_extractors.asr` resolves weights from a local directory only. If the directory is
absent, ASR reports itself unavailable and the analysis run continues with deterministic
evidence rather than failing.

### No generative model is installed

There are no LLM weights on DEV-01. The Model Gateway therefore routes local semantic
requests to the **deterministic baseline adapter**, which restates measurements as
structured claims and invents nothing. The llama.cpp adapter is implemented and wired but
**has not been exercised**, because doing so needs a GGUF model that was not approved for
download. See `docs/implementation/IMPLEMENTATION_STATUS.md` for what that leaves untested.

## Test-fixture tooling (never shipped)

| Tool | License | Use |
|---|---|---|
| espeak-ng | **GPL-3.0** | Synthesises speech for `tests/gold/synthetic-speech.mp4` |

espeak-ng runs inside a **throwaway `debian:bookworm-slim` container** in
`tools/corpus/generate_speech_fixture.sh`. It is never installed into an MP2 image and is
not a dependency of any deployable. The generated audio is program output and is MP2-owned
test data.

Both fixtures are fully synthetic (FFmpeg `testsrc2`/`smptebars`/`sine` plus synthesised
speech). **No third-party copyrighted media is stored in this repository**, and none is sent
anywhere.

## Web dependencies

| Component | Version | License |
|---|---|---|
| React / react-dom | 19.0.0 | MIT |
| Vite | 6.4.3 | MIT |
| TypeScript | 5.7.3 | Apache-2.0 |

Exact transitive versions are pinned in `apps/web/package-lock.json`.

## External model providers

External routing is **disabled**: `MP2_EXTERNAL_MODELS_ENABLED=false`. No API key is
present, none is required, and no test depends on one. The OpenAI-compatible adapter exists
as a contract boundary only — it imports no provider SDK and raises `PermissionError`
unless explicitly enabled. Enabling it is a policy decision, not a configuration change:
user media and private data must never be sent to an external service.

## SBOM

`infra/scripts/generate-sbom.sh` produces an SBOM per deployable image. Regenerate it
whenever image contents change; image digests belong in `infra/compose/image-lock.json`,
which is gitignored because it is environment-specific.
