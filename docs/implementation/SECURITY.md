# Security

Scope: the MP2 development environment on DEV-01 (M0–M4). This describes what is enforced
today, and is explicit about what is *not* yet enforced so nothing here reads as a stronger
guarantee than the code provides.

## Threat model for this stage

DEV-01 is a single-operator development machine. The assets worth protecting are:

1. **Private and copyrighted source media.** Must not leave the machine.
2. **Proprietary MP2 logic** — ontology, schemas, scoring, calibration.
3. **Provenance and rights metadata.** If this is wrong, every downstream decision is wrong.
4. **Credentials.** Must never reach Git.

Not in scope yet: multi-tenant isolation, authentication and authorization, TLS between
services, secret rotation, audit logging of operator actions. All are required before
anything is exposed beyond loopback. See "Not yet implemented".

## Network exposure

Only services attached to a **non-internal** network can publish a port to the host at all.
This is structural, not a setting: a container attached solely to `internal: true` networks
has no route to the host bridge, so a `ports:` entry on it is silently ineffective.

MP2 uses that as the exposure boundary. The result, **verified by connecting from the WSL
host**:

| Service | Reachable from host | Profile |
|---|---|---|
| api | `127.0.0.1:8000` ✅ | core |
| web | `127.0.0.1:5173` ✅ | ui |
| temporal-ui | `127.0.0.1:8233` ✅ | admin |
| **postgres** | **no — not reachable at all** | core |
| **temporal** | **no — not reachable at all** | core |
| **seaweedfs** | **no — not reachable at all** | core |
| **model-gateway** | **no — publishes nothing** | core |
| **worker-cpu** | **no — publishes nothing** | core |
| prometheus / grafana / otel | `127.0.0.1:9090 / 3000 / 4317 / 4318` | ops |

Verified directly:

```
127.0.0.1:8000  OPEN
127.0.0.1:5432  connection refused
127.0.0.1:7233  connection refused
```

The database, the workflow engine and raw object storage are **unreachable from the host**,
not merely bound to loopback. Reach them with `mp2.sh psql`, `mp2.sh workflows` and
`mp2.sh objects`, which go through `docker compose exec`.

Port entries were removed from those three services rather than left in place: a `ports:`
line that cannot take effect is dead configuration that implies an exposure which does not
exist.

If you genuinely want a host GUI client against PostgreSQL, attach it to `edge`
deliberately and document it — do not assume the existing config does so.

Verify at any time:

```bash
infra/scripts/mp2.sh ports
```

Any listener on `0.0.0.0` or `::` belonging to an MP2 container is a finding. This machine
has a pre-existing `*:80` listener owned by Docker Desktop (`com.docker.backend`) which is
**not** MP2.

### Internal networks

Five named networks, four of them `internal: true` (no route off the host):

| Network | Internal | Members |
|---|---|---|
| `edge` | no | api, web, temporal-ui, prometheus, grafana, otel-collector |
| `app` | **yes** | postgres, temporal, seaweedfs, api, worker-cpu, temporal-ui |
| `compute` | **yes** | api, worker-cpu, model-gateway |
| `model` | **yes** | worker-cpu, model-gateway, llama-server |
| `ops` | **yes** | otel-collector, prometheus, grafana |

`edge` membership is what makes a published port reachable; binding to `127.0.0.1` is what
keeps it local-only. Both are required.

Because `worker-cpu` is on `app`, `compute` and `model` — none of which is `edge` — it has
**no egress route to the internet**. This was confirmed by attempting to reach
`https://api.openai.com` from inside the worker, which failed with `URLError`.

Admin and ops UIs are on `edge` so their loopback publishing works. That gives those
containers outbound network access, which is an accepted trade-off for opt-in profiles and
is worth revisiting before any of them runs routinely.

### Web tier cannot reach raw storage

The `web` service is attached to `edge` only. It has no route to `app`, no object-store
credentials, and no direct database access. It reads canonical JSON from the API. Raw media
in `mp2-raw` is unreachable from the browser tier by network topology, not by convention.

## Secrets

- `.env` is gitignored; `.env.example` is committed and contains only placeholders.
- No API keys, tokens or passwords appear anywhere in the repository.
- `alembic.ini` carries a development-only default URL that is always overridden by
  `MP2_DATABASE_URL` at runtime.
- Development passwords (`mp2-dev-only`, `change-me`) are obviously non-production and
  reach only loopback-bound services.

**Before any non-local deployment**, every development credential must be replaced and moved
into a real secret store. The current values are placeholders, not secrets.

Check before committing:

```bash
git status --porcelain          # .env must never appear
git diff --cached | grep -iE 'api[_-]?key|secret|password|token'
```

## Container hardening

- Images run as the unprivileged `mp2` user, not root.
- `data/ingest` is mounted **read-only** into the api container.
- No container runs privileged, and none mounts the Docker socket.
- Base images are pinned by major version; resolved digests belong in
  `infra/compose/image-lock.json` (gitignored — environment-specific).
- Compose resource limits keep any one service from exhausting a 12 GiB WSL allocation.
- The deployable image ships **no test runner**; pytest exists only in the `test` build
  target.

## Ingest boundary

`POST /v1/media/{id}/assets` is the only path that reads from the filesystem, and it is
constrained twice:

1. **Path containment.** The resolved path must be the ingest root or inside it. This is
   checked after `Path.resolve()`, so `..` traversal and symlink escapes are rejected.
   Anything else returns 403.
2. **Mandatory rights metadata.** `provenance_class`, `rights_access_class` and
   `retention_class` are required; a request without them returns 422. MP2 never infers or
   defaults rights metadata.

Both behaviours were verified live against the running API.

## External model policy

External routing is **off**: `MP2_EXTERNAL_MODELS_ENABLED=false`.

- The OpenAI-compatible adapter imports no provider SDK and raises `PermissionError`.
- The gateway refuses a non-zero `maximum_cost` unless external models are enabled.
- Requests declare `allowed_provider_classes`; local-only is the default everywhere.
- `compute` and `model` are internal networks, so there is no egress route regardless.
- No test requires an external provider.

Even if external routing were enabled, a provider would receive only a **scene packet** —
measurements, transcript text and time ranges. Never media bytes. Enabling external routing
for anything other than synthetic fixtures is a policy decision requiring explicit approval,
because it would send derived content about private media off the machine.

```bash
infra/scripts/mp2.sh local-only
```

## Model weights

Whisper weights are baked in at build time and loaded from a local directory only. Nothing
is downloaded at runtime, so the system works with egress blocked. No generative model
weights are installed. Model provenance and licenses: LICENSES_AND_MODELS.md.

## Data protection

- `mp2-raw` is the authoritative store for source bytes and is never modified in place.
- SHA-256 is computed at ingest and re-verified whenever an object is materialized for
  processing; a mismatch aborts the activity.
- Rights and retention classes are stored per asset and travel into the scene packet's
  `privacy_class`, so downstream routing decisions can honour them.
- `mp2-normalized` and `mp2-derived` are disposable and regenerable.

## Verified on DEV-01 (2026-09-21)

- All MP2 published ports bound to `127.0.0.1`; `model-gateway` and `worker-cpu` publish
  nothing at all.
- **Worker egress to a public model provider is blocked** — `mp2.sh local-only` attempted
  `https://api.openai.com` from inside `worker-cpu` and got `URLError`, because `compute`
  and `model` are internal networks.
- Gateway reports `external_models_enabled=false`, `local_generative_configured=false`,
  routing to `mp2-deterministic-baseline`.
- Ingest path containment enforced (**403** on `/etc/hostname`), asserted by an integration
  test, not just by inspection.
- Rights metadata enforced (**422** when omitted), likewise asserted by a test.
- No credentials in the repository; `.env` gitignored and untracked.
- Containers run as non-root; the deployable image ships no test runner.
- Recorded evidence contains no provider-specific fields (asserted in
  `tests/unit/test_contracts.py`).

The only non-loopback listener observed on the machine is `*:80`, owned by Docker Desktop
(`com.docker.backend`). It is not MP2.

## Not yet implemented

Honest gaps at M0–M4. None of these are acceptable beyond a loopback-only dev machine:

- **No authentication or authorization on the API.** Any process that can reach
  `127.0.0.1:8000` has full control. There are no users, roles or tokens.
- **No TLS anywhere.** All internal traffic is plaintext.
- **No rate limiting or request size limits.**
- **No audit log** of who ingested or analyzed what.
- **No encryption at rest** beyond whatever the host volume provides.
- **No secret management** — credentials come from `.env`.
- **No signed images or SBOM attestation** (SBOM generation is scaffolded, not enforced).
- **Grafana is AGPL-3.0** and must not be embedded in a third-party-facing product surface
  without legal review.
