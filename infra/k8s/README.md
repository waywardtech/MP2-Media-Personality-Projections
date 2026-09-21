# infra/k8s

Reserved for Kubernetes manifests.

**Empty by design.** Principle 8 is to build the smallest production-shaped thing: Docker
Compose with profiles, not premature Kubernetes. The production *boundaries* are already in
place - separate services, separate task queues, internal networks, health checks and
resource limits - so this can be filled in when horizontal scale is actually needed rather
than anticipated.
