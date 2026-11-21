# packages/scoring

Genome calculation and scoring logic (M5+).

**Empty by design at M0-M4.** The workflow declares `CalculateGenome` as a boundary and
implements it as an explicit deferral, so this package is added without reshaping the
pipeline.

Scoring consumes `evidence_claim` and `measurement` rows. It must never be fed directly
from a generative model's output.
