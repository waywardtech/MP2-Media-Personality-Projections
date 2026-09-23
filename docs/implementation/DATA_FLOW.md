# Data flow

How a piece of media becomes evidence, and what is authoritative at each step.

## Separation of concerns

MP2 keeps four things logically separate, because conflating them is what makes an
analysis system impossible to audit later:

| Layer | Lives in | Authoritative for |
|---|---|---|
| Raw source media | `mp2-raw` (object storage) | The bytes as received. Never modified. |
| Normalized intermediates | `mp2-normalized` | Deterministic derivations (PCM audio, mezzanines). Disposable. |
| Derived evidence artifacts | `mp2-derived` | Scene packets and derived files. Disposable. |
| Canonical product data | PostgreSQL | Provenance, rights, retention, hashes, relationships, measurements, evidence, lineage. |

PostgreSQL is the system of record for **everything except bytes**. Object storage holds
bytes; the database holds what they mean, where they came from and what may be done with
them. Anything in `mp2-normalized` or `mp2-derived` can be deleted and regenerated — that
is what `mp2.sh clean-derived` does.

## Ingest

```
client → POST /v1/media                  → media_work + media_edition
client → POST /v1/media/{id}/assets      → source_asset
client → POST /v1/media/{id}/analyze     → analysis_run + Temporal workflow
```

`POST /assets` is the rights boundary. It requires `provenance_class`,
`rights_access_class` and `retention_class` and fails with 422 if any is missing. **MP2
never infers or defaults rights metadata**, because a wrong default is indistinguishable
from a deliberate claim once it is stored.

The request carries a `local_path`, which must resolve inside the controlled ingest root
(`/media`, mounted read-only). Anything else is refused with 403, so the endpoint cannot be
used to read arbitrary container files. The API then computes SHA-256, probes with ffprobe,
copies the object into `mp2-raw`, and records size, mime type, hash, object key and the
full probe document.

## Analysis workflow

`AnalyzeMediaWorkflow` carries **IDs and version references only**. Media bytes never enter
Temporal. Each activity re-materializes what it needs from object storage into per-run
scratch, so any activity can run on any worker and can be retried independently.

| # | Activity | Queue | Produces |
|---|---|---|---|
| 1 | `ValidateSource` | `mp2-io` | Confirms the raw object exists; run → `running` |
| 2 | `ProbeAndHash` | `mp2-io` | `media.probe_summary`, `media.probe_raw` |
| 3 | `NormalizeAsset` | `mp2-cpu` | 16 kHz mono PCM → `mp2-normalized` |
| 4 | `SegmentShots` | `mp2-cpu` | `segment` rows, `shot.boundary`, `shot.summary` |
| 5 | `ExtractVisual` | `mp2-cpu` | `visual.segment_measures` per shot |
| 6 | `ExtractAudio` | `mp2-cpu` | `audio.work_measures`, `audio.segment_measures` |
| 7 | `Transcribe` | `mp2-cpu` | `asr.transcript`, `dialogue.segment_measures` |
| 8 | `BuildScenePackets` | `mp2-cpu` | Scene packets → `mp2-derived`, `scene.packet_ref` |
| 9 | `InterpretSemantics` | `mp2-semantic` | `model_execution` + `evidence_claim` rows |
| 10 | `AssembleEvidence` | `mp2-cpu` | `run.evidence_summary`; run → `evidence_ready` |

**M0–M4 stops here, at evidence readiness.** The later boundaries —
`CalculateGenome`, `BuildProjections`, `ValidateAnalysis`, `PublishVersion` — are declared
in the workflow and implemented as explicit deferrals, so M5 extends the pipeline instead
of reshaping it.

On failure the workflow invokes `MarkRunFailed`, so a terminal failure is visible through
the API rather than leaving a run stuck at `running`.

### Idempotency

Activities are retry-safe by construction:

- `materialize()` re-downloads only if the local copy is missing or its hash differs.
- `upsert_segment` keys on `(source_asset_id, kind, start_ms, end_ms)`.
- `upsert_measurement` keys on `(analysis_run_id, segment_id, extractor_definition_id, metric)`
  and overwrites in place.
- Semantic requests use a deterministic `uuid5` request id, so a retry reuses the same
  `model_execution` row.

Migration `0002` enforces these as database constraints (`UNIQUE NULLS NOT DISTINCT`, which
matters because work-level rows carry a NULL `segment_id`). Application-level upserts plus
structural constraints mean a worker killed mid-run cannot produce duplicate canonical
output when the workflow resumes.

## Measurement lineage

Every `measurement` row records:

- the `analysis_run` it belongs to and the `segment` it describes (NULL = work-level),
- the `extractor_definition` — extractor id, version, repeatability class, parameters,
  license record and hardware class,
- a `source_ref` naming the exact object it was computed from,
- a `content_hash` over the canonical JSON of the value.

The content hash is what makes "reproducible" checkable rather than aspirational: rerun a
D0 extractor on the same input and the hash must be identical.

Extractor versions are resolved **at runtime** from the installed packages, not hard-coded,
so a measurement always records the version that actually produced it.

### Repeatability classes

| Class | Meaning | Extractors |
|---|---|---|
| D0 | Bit-identical on rerun | ffprobe, ffmpeg normalization, PySceneDetect, OpenCV visual, dialogue statistics |
| D1 | Numerically stable within tolerance | librosa audio, faster-whisper, embeddings |
| D2 | Not guaranteed repeatable | generative model output |

`tests/reproducibility/test_d0_rerun.py` asserts D0 by content hash and D1 by numeric
tolerance, including one check that runs extraction in separate processes so in-process
caching cannot make a rerun merely *look* stable.

## Scene packets and the semantic boundary

A scene packet is the **only** thing a generative model ever sees. It contains the time
range, transcript text and utterances, the selected deterministic measurements, optional
representative-frame references, context summaries, and any operator-supplied character
aliases — plus a schema instruction.

It contains no media bytes, no provider names and no vendor-specific fields. This is what
makes "raw source media is not the product" enforceable rather than a policy statement: even
if an external provider were enabled, it could not receive the media itself.

Requests cross the **Model Gateway**, which is provider-neutral in both directions. Callers
describe a capability, a privacy class, a determinism requirement, a cost ceiling and the
permitted provider classes. They never name a model. The gateway alone decides routing.

Semantic output becomes `evidence_claim` rows linked to the segment, the measurements and
the `model_execution` that produced them. **Semantic models never write canonical genome
values.** Genome calculation consumes evidence in a later milestone, which keeps the
measurement layer and the interpretation layer independently auditable.

When no model is available, MP2 records a warning and keeps the deterministic evidence.
It does not invent claims to fill the gap.

## Immutability

`analysis_run` carries a `version`, and the workflow id embeds it
(`analyze-media-<run-id>-v<version>`). Published analysis history is superseded by new
versions rather than overwritten; `RunStatus.SUPERSEDED` exists for that transition. Within
a single run, measurements are upserted so that retries converge, but across runs each
version stands on its own.

## What the web tier can see

The console talks only to the API. It holds no object-store credentials and never reads raw
media. `GET /v1/analysis-runs/{id}/evidence` returns measurements, claims, model executions
and segments as canonical JSON with lineage attached, so nothing is displayed without its
provenance.
