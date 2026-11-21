# packages/evaluation

Evaluation harness: gold-set comparison, inter-version drift, calibration metrics.

**Empty by design at M0-M4.** Reproducibility checks that exist today live in
`tests/reproducibility` (D0 content-hash equality, D1 numeric tolerance). This package is
for evaluating *analysis quality*, which requires a gold corpus that does not yet exist.
