# services/worker_cpu

Deployment shape for CPU extraction workers.

The worker implementation is `services/orchestrator/mp2_orchestrator/worker.py`; this
directory exists so CPU, GPU and IO workers remain separately deployable and separately
scalable, which is the point of the `mp2-io` / `mp2-cpu` / `mp2-gpu` / `mp2-semantic`
queue split.

Configured in `infra/compose/compose.yml` as the `worker-cpu` service (core profile).
