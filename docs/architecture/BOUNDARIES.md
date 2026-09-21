# Architecture boundaries

The rules that keep MP2 portable, auditable and model-independent. Where a rule can be
mechanically enforced, it is — a boundary that depends on discipline alone will erode.

## Ownership

| Concern | Owner | Never owned by |
|---|---|---|
| Canonical meaning | `packages/domain`, `packages/schemas` | Any provider SDK or extractor |
| Provenance, rights, retention, lineage, relationships | PostgreSQL | Object storage |
| Bytes | Object storage (`mp2-raw` authoritative) | PostgreSQL |
| Durable execution | Temporal | Application code |
| Model routing | `services/model_gateway` | Callers |
| Measurement production | `packages/extractor_adapters` | The orchestrator |
| Persistence and lineage | `services/orchestrator` | Extractors |

## Rule 1 — canonical packages import no provider SDK

`packages/domain` and `packages/schemas` must not import `openai`, `anthropic`, `google`,
`boto3` or `temporalio`. They define what MP2 means by a measurement, a segment, an
evidence claim and a gateway contract; if a vendor object reached them, every stored row
would inherit that vendor's lifecycle.

**Enforced** by `tests/unit/test_architecture.py`, which parses the AST of every module in
those packages and fails on a forbidden import.

Corollary: no stored value should require vendor-specific knowledge to interpret. A
`model_execution` row records `provider_adapter`, `model_tool_id` and `model_tool_version`
as **plain strings describing what ran**, not as provider objects.

## Rule 2 — extractors compute, the orchestrator persists

`packages/extractor_adapters` takes a path and an `ExtractorSpec` and returns
JSON-serialisable data. It does not open database sessions, does not write to object
storage and does not know what an `analysis_run` is.

This keeps extractors independently testable, lets them run on any worker class, and means
adding an extractor never touches persistence logic.

## Rule 3 — every extractor is self-describing

An `ExtractorSpec` carries `extractor_id`, `version`, `repeatability_class`,
`input_schema_version`, `output_schema_version`, `parameters`, `license_record` and
`hardware_class`. Versions resolve from the **installed** package at runtime, never
hard-coded, so a measurement records what actually produced it.

**Enforced** by `tests/unit/test_extractor_registry.py`, which fails on a missing license
record, an invalid repeatability class or a duplicate identity.

## Rule 4 — measure before interpreting

Deterministic and specialist extraction runs before generative interpretation, and
interpretation consumes their output. A generative model cannot be the first thing that
looks at a work.

Structurally: `InterpretSemantics` is activity 9 of 10 and receives only a scene packet
built from measurements produced by activities 2–8.

## Rule 5 — the scene packet is the only thing a model sees

No media bytes, no provider names, no vendor fields. This is what makes "raw source media
is not the product" enforceable rather than aspirational: even with an external provider
enabled, it could not receive the media.

## Rule 6 — semantic output is evidence, never canonical genome values

A model writes `evidence_claim` rows linked to a segment, its measurements and the
`model_execution` that produced them. Genome calculation consumes evidence in a later
milestone.

This keeps the measurement layer and the interpretation layer independently auditable: you
can always ask "what did we measure?" separately from "what did a model conclude?".

## Rule 7 — workflows carry references, not payloads

Temporal inputs and activity state are small dictionaries of IDs and version references.
Every activity re-materializes what it needs from object storage. This keeps event
histories small, lets any worker pick up any activity, and keeps media out of the workflow
engine.

## Rule 8 — activities are idempotent

Every write is keyed on natural identity and upserts. Semantic requests use a deterministic
`uuid5` request id. Migration `0002` backs these with database constraints, using
`UNIQUE NULLS NOT DISTINCT` because work-level rows carry a NULL `segment_id`.

A worker killed mid-run must resume without duplicate canonical output.

## Rule 9 — compute classes stay separable

Task queues `mp2-io`, `mp2-cpu`, `mp2-gpu`, `mp2-semantic` and `mp2-review` exist so IO,
CPU, GPU and model work can move to separate hosts without changing the workflow. A single
development worker polling all of them is a deployment choice, not an architectural one.

## Rule 10 — the web tier reaches nothing but the API

`web` attaches only to the `edge` network. It holds no object-store credentials and has no
database access. Raw media is unreachable from the browser tier by network topology.

## Network boundaries

`edge` is the only non-internal network. `app`, `compute`, `model` and `ops` are
`internal: true`, so a worker has no egress route to a public model provider regardless of
what any code attempts.

## Where lock-in is accepted

Per principle 10, deliberate coupling requires measured benefit and an exit path.

| Dependency | Coupling | Exit path |
|---|---|---|
| Temporal | Workflow/activity decorators in `services/orchestrator` | Activities are plain async functions doing the real work; the workflow is a thin ordering layer |
| PostgreSQL + pgvector | SQLAlchemy models; `UNIQUE NULLS NOT DISTINCT` needs PG15+ | Behind SQLAlchemy; the NULLS clause is the only genuinely PG-specific DDL |
| S3 API | `S3ObjectStore` | `ObjectStore` protocol with a working local adapter |
| FFmpeg | Subprocess only, never linked | Replaceable behind `ExtractorSpec` |

pgvector is used only for embedding data that needs vector search. **The canonical
psychological genome is not stored solely as an opaque embedding** — it is structured,
versioned, evidence-linked data.
