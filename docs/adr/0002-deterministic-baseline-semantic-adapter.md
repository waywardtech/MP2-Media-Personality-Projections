# ADR 0002 — A deterministic baseline adapter behind the Model Gateway

- Status: Accepted
- Date: 2026-09-20
- Milestone: M4

## Context

The Model Gateway is MP2's boundary for every generative or external model call. Acceptance
gate G4 requires that a scene packet is created, that a **local** semantic adapter returns
schema-valid evidence, that the evidence links back to source, segment, measurements and
model execution, and that external providers remain optional and disabled.

DEV-01 has no generative model weights. The declared local generative target is llama.cpp,
which needs a GGUF model. Downloading one was not approved (only Whisper `tiny` was), and
the machine has no NVIDIA GPU, so generative inference would be slow CPU work in any case.

That left three options:

1. Download a small quantized GGUF anyway. Rejected — introducing model weights without
   approval violates an explicit project constraint.
2. Leave `InterpretSemantics` unimplemented until weights exist. This would leave the entire
   evidence path — packet construction, gateway contract, `model_execution` persistence,
   `evidence_claim` linkage, idempotency of semantic retries — completely untested, and
   would make G4 unassessable.
3. Implement a second local adapter that satisfies the gateway contract without weights.

## Decision

Implement `DeterministicBaselineAdapter` in `services/model_gateway`, and make it the
default local route whenever no generative model is configured
(`MP2_LLAMA_MODEL_ID` unset or `unset`).

The adapter takes a scene packet and emits schema-valid `SceneEvidenceOutput` claims that
are **direct restatements of deterministic measurements already in the packet** — banded
brightness, saturation, visual complexity, motion, silence, spectral brightness, dialogue
density — each citing the segment and the measurement family it came from. Confidence
reflects measurement coverage, not model certainty. It is declared D0: identical input
yields an identical content hash.

Critically, it **does not interpret**. It produces descriptive scene evidence only. It
performs no psychological inference, and emits no claim about a person.

Routing is unchanged in shape: `select_adapter()` prefers llama.cpp when a model is
configured, falls back to the baseline otherwise, and refuses external providers unless
explicitly enabled.

## Consequences

Positive:

- G4 becomes genuinely testable. The packet contract, gateway request/response contract,
  `model_execution` and `evidence_claim` persistence, evidence linkage and semantic-retry
  idempotency are all exercised end to end on a machine with no model weights.
- It is the **control arm**. When a generative adapter is enabled, its output is compared
  against a deterministic baseline rather than against nothing — which is the only way to
  show that a generative model adds value over restating measurements.
- It demonstrates model independence concretely: two local adapters, one contract, the
  caller unchanged.
- It makes "measure before interpreting" operational. The baseline is what the measurement
  layer alone can support; anything beyond it must be attributed to a model.
- Missing measurements produce warnings and fewer claims, never invented ones.

Negative and accepted:

- **The llama.cpp adapter remains untested.** It is implemented and wired, but no generative
  model has run on DEV-01. This is recorded in IMPLEMENTATION_STATUS.md and must not be
  reported as a passing generative path.
- There is a risk of the baseline being mistaken for real semantic interpretation.
  Mitigated by `provider_adapter = "mp2-deterministic-baseline"` on every
  `model_execution`, `interpretation_tier: "deterministic-baseline"` inside the claims, and
  `GET /v1/capabilities` reporting `generative: false` for this adapter. Any consumer can
  tell exactly what produced a claim.
- The baseline's banding thresholds are arbitrary engineering choices, not calibrated
  values. They are descriptive conveniences and **must not** be promoted into genome
  calculation or psychometric mapping. Calibration is out of scope for M0–M4 by design.

## Alternatives rejected

- **Returning a canned/stub response from the gateway.** It would satisfy the schema while
  proving nothing about evidence linkage, and would make a fabricated claim indistinguishable
  from a real one.
- **Skipping `InterpretSemantics` entirely when no model exists.** The activity already does
  this when the gateway is unreachable, and the run records a warning. But making that the
  only path means the semantic boundary is never exercised at all.
- **Requiring an external provider for tests.** Directly contrary to the local-only
  requirement, and would send derived content about media off the machine.
