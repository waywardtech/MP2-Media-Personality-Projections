# MP2 — Media Personality Projection

Private, proprietary media-psychology analysis and recommendation system built on open
foundations.

MP2 analyses audiovisual works, records **measured, reproducible evidence** about them with
full lineage, and — in later milestones — projects that evidence into psychological
dimensions used for recommendation. This repository is the M0–M4 foundation: environment,
infrastructure, data model, ingest, workflow, deterministic extraction and the evidence
boundary.

> **Status: M0–M4.** The system ingests media, extracts deterministic and specialist
> measurements, builds scene packets and records evidence claims with lineage. Genome
> calculation, psychometric mapping and recommendation are deliberately **not** implemented
> — see [IMPLEMENTATION_STATUS.md](docs/implementation/IMPLEMENTATION_STATUS.md).

## Principles

These are constraints, not aspirations, and the code is arranged so that violating them is
difficult:

1. **MP2 owns the domain.** Provider- and model-specific objects never enter canonical
   schemas. A test fails the build if `packages/domain` or `packages/schemas` imports a
   provider SDK.
2. **Open foundations, private advantage.** Infrastructure is open source; the ontology,
   derived corpus, calibration data and recommendation logic are proprietary.
3. **Model independence is mandatory.** Every model is a replaceable instrument behind the
   MP2 Model Gateway. Callers describe a capability, never a model.
4. **Measure before interpreting.** Deterministic extractors run first; generative
   interpretation consumes their output and cannot bypass them.
5. **Reproducibility before eloquence.** Every measurement carries extractor identity,
   version, parameters, a repeatability class and a content hash.
6. **Raw source media is not the product.** Raw, normalized, derived and canonical data are
   separated, and a generative model only ever sees a scene packet.
7. **Published analysis history is immutable.** New versions supersede; they never silently
   overwrite.
8. **Build the smallest production-shaped thing.** Docker Compose with profiles, not
   Kubernetes — but with production service boundaries already in place.

## Architecture

```
apps/api            FastAPI: ingest, analysis runs, evidence read models
apps/web            React + Vite console
services/
  orchestrator      Temporal workflow + activities (the analysis pipeline)
  model_gateway     Provider-neutral model boundary; local adapters
  worker_cpu/gpu    Worker deployment shapes
packages/
  domain            SQLAlchemy models + enums     (no provider SDKs)
  schemas           Pydantic contracts            (no provider SDKs)
  extractor_adapters  Versioned extractor registry and implementations
  storage           ObjectStore interface + S3/local adapters
  ontology, scoring, evaluation                   (M5+)
infra/
  compose           Profiled Compose stack
  containers        Dockerfiles and service configs
  scripts           mp2.sh — the operations entrypoint
migrations          Alembic
tests               unit, integration, gold, reproducibility
docs                architecture, ADRs, implementation
tools               corpus fixtures, benchmarks, admin
```

**Stack.** PostgreSQL 18 + pgvector · Temporal (self-hosted) · SeaweedFS (S3-compatible) ·
FastAPI + Pydantic + SQLAlchemy + Alembic · React + TypeScript + Vite · Docker Compose ·
FFmpeg, PySceneDetect, OpenCV, librosa, faster-whisper · llama.cpp as the local generative
target.

## Quick start

Everything runs inside WSL2 Ubuntu 24.04. Nothing installs into Windows.
Full instructions: [BOOTSTRAP.md](docs/implementation/BOOTSTRAP.md).

```bash
wsl -d Ubuntu-24.04
cd "/mnt/d/GitHub/MP2 - Media Personality Projections"
cp .env.example .env

cd infra/compose && docker compose --profile core build && cd ../..
infra/scripts/mp2.sh up
infra/scripts/mp2.sh migrate
```

Run the vertical slice on a synthetic fixture:

```bash
infra/scripts/mp2.sh fixture
infra/scripts/mp2.sh test
```

Then register a work, attach the asset and launch an analysis — see
[OPERATIONS.md](docs/implementation/OPERATIONS.md).

`infra/scripts/mp2.sh` with no arguments lists every operation.

## The analysis pipeline

`AnalyzeMediaWorkflow` carries IDs only; media bytes never enter Temporal.

```
ValidateSource → ProbeAndHash → NormalizeAsset → SegmentShots
  → ExtractVisual → ExtractAudio → Transcribe → BuildScenePackets
  → InterpretSemantics → AssembleEvidence     ← M0-M4 ends here (EvidenceReady)
  → CalculateGenome → BuildProjections → ValidateAnalysis → PublishVersion   (M5+, declared)
```

Activities are idempotent, keyed on natural identity and backed by database constraints, so
a worker killed mid-run resumes without producing duplicate canonical output.

## Evidence, not opinions

Deterministic extractors produce `measurement` rows with full lineage. Scene packets bundle
those measurements with transcript text and time ranges, and are the **only** thing a
generative model sees. Semantic output becomes `evidence_claim` rows linked to their
segment, measurements and model execution.

A semantic model never writes canonical genome values. When no model is available, MP2
records a warning and keeps the deterministic evidence rather than fabricating claims.

## Local-first

External model routing is **disabled by default** and no API key is required. The `compute`
and `model` Docker networks are internal, so there is no egress route from a worker to a
public provider. Whisper weights are baked into the image at build time, so ASR works with
egress blocked.

```bash
infra/scripts/mp2.sh local-only
```

## Documentation

| Document | Contents |
|---|---|
| [DEV-01-AUDIT.md](docs/implementation/DEV-01-AUDIT.md) | Live hardware audit and classification |
| [BOOTSTRAP.md](docs/implementation/BOOTSTRAP.md) | Windows → WSL2 → running stack |
| [OPERATIONS.md](docs/implementation/OPERATIONS.md) | Every operational command |
| [TROUBLESHOOTING.md](docs/implementation/TROUBLESHOOTING.md) | Real failures and their fixes |
| [IMPLEMENTATION_STATUS.md](docs/implementation/IMPLEMENTATION_STATUS.md) | What is done, tested, and not |
| [SECURITY.md](docs/implementation/SECURITY.md) | Exposure, secrets, and honest gaps |
| [DATA_FLOW.md](docs/implementation/DATA_FLOW.md) | How media becomes evidence |
| [LICENSES_AND_MODELS.md](docs/implementation/LICENSES_AND_MODELS.md) | Licenses and model provenance |
| [BENCHMARKS.md](docs/implementation/BENCHMARKS.md) | Measured performance on DEV-01 |
| [BOUNDARIES.md](docs/architecture/BOUNDARIES.md) | Package boundary rules |

## Licensing

Proprietary. All rights reserved. Third-party dependencies, their licenses and all model
weights are recorded in
[LICENSES_AND_MODELS.md](docs/implementation/LICENSES_AND_MODELS.md).

No third-party copyrighted media is stored in this repository. Test fixtures are fully
synthetic and generated locally.
