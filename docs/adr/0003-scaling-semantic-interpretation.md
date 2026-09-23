# ADR 0003 — Scaling semantic interpretation to corpus volume

- Status: Proposed — needs a decision on options 1 and 3
- Date: 2026-09-23
- Supersedes nothing. Extends ADR 0002.

## Context

Local generative interpretation works (ADR 0002, proven 2026-09-23) but is slow on DEV-01:

| Adapter | Per request | 1,320 requests/feature | 50-work corpus |
|---|---:|---:|---:|
| Deterministic baseline | 0.2–1.1 s | ~15 min | ~12 h |
| Qwen2.5-1.5B (local CPU) | 30.4 s | ~11 h | ~23 days |
| Qwen2.5-7B (local CPU) | 116.9 s | ~43 h | ~89 days |

Deterministic extraction is **not** the problem — it runs at 0.15× realtime and would finish
a 50-work corpus in about twelve hours on this machine. The entire scaling question is
about the generative pass.

The obvious response is "rent a GPU". Before doing that it is worth asking why there are
1,320 requests in the first place.

## The primary finding: the granularity is wrong, not the compute

MP2 segments by **shot** and then issues one semantic request per shot. A shot is a
*cutting* unit, not a *dramatic* unit. For the first real work in the corpus, an 85-minute
feature, this means on the order of a thousand requests, each interpreting a fragment that
is a few seconds long and frequently has no dialogue at all.

That is expensive **and** it produces worse evidence. A single shot, stripped of the scene
around it, is close to uninterpretable: a model asked what it means will either hedge or
invent, and the 1.5B demonstrably invented. Narrative meaning lives at the scene, sequence
and act level — of which an 85-minute feature has perhaps 40 to 80, not 1,320.

Aggregating shots into narrative scenes before interpretation changes the arithmetic by
roughly 20–40×:

| Granularity | Requests/feature | 50 works | Local (1.5B) | Local (7B) |
|---|---:|---:|---:|---:|
| Per shot (today) | ~1,320 | 66,000 | ~23 days | ~89 days |
| **Per scene (~50)** | **~50** | **2,500** | **~21 h** | **~81 h** |

**At scene granularity the corpus becomes tractable on the existing hardware**, and the
evidence gets better rather than worse. Shot-level deterministic measurements are still
produced for every shot and still stored — nothing is lost. Only the *interpretation* moves
up a level, and it gains the context it needs.

Scene boundaries can be derived from signal MP2 already has: gaps in the subtitle/ASR
timeline, shot-similarity clustering over the existing visual measurements, and audio
energy discontinuities. No new extractor class is required.

## Options

### Option 1 — Aggregate to scene granularity (recommended, do this first)

Add a `SegmentScenes` activity between `SegmentShots` and `BuildScenePackets` that groups
shots into narrative scenes. Scene packets are built per scene and carry the aggregated
shot measurements.

- Cost: engineering only, no spend, no third party.
- Makes the corpus viable locally, and makes remote inference ~26× cheaper if it is still
  wanted afterwards.
- Improves claim quality by giving the model context.
- Risk: scene-boundary detection is a heuristic and will sometimes be wrong. Mitigated by
  keeping shot boundaries as the canonical segmentation and treating scenes as a derived
  grouping, so a better algorithm later re-groups without re-extracting.

### Option 2 — Rent GPU compute and self-host the model

Run llama.cpp or vLLM on a rented GPU, still behind the MP2 Model Gateway.

- MP2 stays the only thing that sees the work; the provider sees a VM, not an API payload.
- Continuous batching is the real win: local runs one request at a time, a GPU serves tens
  concurrently, so wall clock improves far more than raw token rate suggests.
- Order of magnitude: an L4/A10-class instance is a low-single-digit dollars per hour, and
  the corpus is tens of GPU-hours at scene granularity. **Re-check current pricing before
  committing — the figures move.**
- Exit path: clean. Same GGUF, same adapter, same contract; move the endpoint back.
- Risk: the source material must not leave the machine. Scene packets may, subject to the
  rights decision below; **media bytes must not**, and MP2's architecture already prevents
  it — a packet contains measurements and text, never media.

### Option 3 — Hosted model API through the gateway

Use a hosted inference provider via the existing OpenAI-compatible adapter.

- Lowest operational effort; no infrastructure.
- Order of magnitude at scene granularity: 2,500 requests × ~900 tokens ≈ 2.3M tokens.
  Even at frontier-model pricing this is small; at hosted-open-weights pricing it is
  negligible. At *shot* granularity it is ~59M tokens, which is where it starts to matter —
  another reason to do Option 1 first.
- Exit path: acceptable but weaker. The adapter is provider-neutral and the contract is
  MP2's, so switching providers is a config change. The dependency is on *availability and
  terms*, not on a data model.
- **This is the option with a rights decision attached.** See below.

## The rights constraint is binding, and it is already encoded

The first real work in the corpus is registered `rights_access_class = private`. That class
travels into every scene packet as `privacy_class`, and the Model Gateway receives it on
every request.

This is not advisory. Today `MP2_EXTERNAL_MODELS_ENABLED=false`, the `compute` and `model`
networks are `internal: true`, and a worker cannot reach a public provider at all —
verified. Enabling Option 3 for `private` material is a **deliberate rights decision by the
owner**, not a configuration change, because it sends derived content about a copyrighted
work to a third party.

Two things make that decision easier if it is taken:

1. A packet contains measurements, time ranges and transcript text. **Never media bytes.**
   That is structural, not policy.
2. The gateway can enforce the class rather than trusting the caller: refuse `external`
   routing for any request whose `privacy_class` is `private`, so the policy is mechanical.

**Recommendation: implement that enforcement before enabling any external provider**, so
the safe default cannot be lost to a future config mistake.

## Decision

Proposed:

1. **Do Option 1 now.** It is free, it improves quality, and it is the difference between a
   23-day corpus and a 21-hour one.
2. **Then re-measure.** If scene-granularity local inference is fast enough, stop; no
   external dependency is incurred and principle 10 is satisfied without argument.
3. **If more is still wanted, prefer Option 2 over Option 3** for `private` material,
   because renting compute does not disclose the work to a model provider. Reserve Option 3
   for `redistributable` or `synthetic` material, or for material the owner explicitly
   releases.
4. **Add privacy-class enforcement to the gateway** regardless of which option is taken.

Nothing here has been purchased or enabled. Items 2 and 3 need the owner's decision.

## Consequences

- The corpus becomes achievable on existing hardware, which removes the only hard blocker
  to a first index at volume.
- Shot-level measurements remain canonical, so re-grouping into better scenes later is a
  re-run of one activity rather than a re-extraction.
- Evidence gains context, which is the actual product; the cost saving is a side effect.
- If external routing is ever enabled, the gateway — not the caller, and not a reviewer —
  is what keeps private works off third-party infrastructure.
