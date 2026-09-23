# ADR 0001: Production-shaped monorepo

Status: accepted for M0–M4.

Use one repository and Compose deployment while retaining explicit API, workflow, compute, model, storage, domain, and schema boundaries. This keeps DEV-01 operable without premature orchestration while preserving later horizontal separation. Vendor objects are confined to adapters.
