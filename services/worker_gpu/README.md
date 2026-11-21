# services/worker_gpu

Deployment shape for GPU workers (`mp2-gpu` queue).

**Not usable on DEV-01**: the machine has an AMD Radeon RX 580 and no CUDA path. The `gpu`
Compose profile is defined but has never been started here. CPU fallback is required and is
what runs today.

See `docs/implementation/DEV-01-AUDIT.md`.
