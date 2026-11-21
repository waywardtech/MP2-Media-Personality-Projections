# Troubleshooting

Every entry here is a failure that actually occurred on DEV-01, with the diagnosis and the
fix that worked. Entries are ordered roughly by how likely you are to hit them.

---

## The whole stack exits at once, every container with status 0

**Symptom.** All six core containers show `Exited (0)` with nearly identical timestamps.
Nothing in any container log explains it. `wsl --list --running` shows `Ubuntu-24.04` is
not running.

**Cause.** WSL terminated the distro. When the last `wsl` session ends and no `.wslconfig`
sets an idle timeout, WSL shuts the VM down, which stops the Docker daemon, which stops
every container. Exit status 0 everywhere is the signature: this is a clean shutdown, not
a crash.

**Fix.** `C:\Users\DTeix\.wslconfig` sets `vmIdleTimeout=-1`. Confirm it is in effect:

```powershell
Get-Content "$env:USERPROFILE\.wslconfig"
wsl --list --running
```

`.wslconfig` changes require `wsl --shutdown` before they apply. If you must keep the distro
alive before that, hold a session open, or run the stack from a terminal you leave open.

---

## `connection refused` to an IP no service occupies

**Symptom.** After a WSL restart the worker crash-loops with:

```
Failed client connect: ... tcp connect error, 172.19.0.5:7233, ConnectionRefused
```

Temporal's own log shows it started normally and is listening — on a *different* address
(for example `172.22.0.5`). Temporal's healthcheck fails against the same stale IP.

**Cause.** Containers with `restart: unless-stopped` came back after the Docker daemon
restarted, but the Compose networks were recreated with different subnets. The restarted
containers hold stale name resolution.

**Fix.** Recreate the containers so they attach to the current networks:

```bash
infra/scripts/mp2.sh down
infra/scripts/mp2.sh up
```

`down` removes containers and networks but **not** named volumes, so no data is lost.
Restarting individual containers is not enough; they must be recreated.

---

## Temporal is healthy in its logs but every client gets `connection refused`

**Symptom.** Temporal's own log shows the frontend started and task queues running, yet the
health check fails and the worker crash-loops with `connection refused` to an address like
`172.22.0.4:7233`. Compose then refuses to start `worker-cpu` with
`dependency failed to start: container mp2-temporal-1 is unhealthy`.

**Cause.** `temporalio/auto-setup` binds the frontend to **one auto-detected interface
address**, not `0.0.0.0`. Inside the container:

```
tcp  0  0  172.21.0.3:7233  0.0.0.0:*  LISTEN
```

If the service is attached to two Compose networks, it has two addresses. Docker DNS can
resolve the service name to the address it is **not** bound to, and every connection is
refused even though the server is perfectly healthy.

**Fix.** Keep `temporal` on a single network. It is now on `app` only, which the api,
workers and temporal-ui all share. The health check also targets `temporal:7233` rather
than `127.0.0.1:7233`, because the server does not listen on loopback either.

If you add a network to `temporal`, expect this to come back.

---

## Temporal marked unhealthy on a slow machine

Schema setup plus first start can exceed a naive retry budget on constrained storage. The
health check carries `start_period: 180s` for exactly this reason. If Temporal is declared
unhealthy during startup on an even slower machine, raise `start_period` rather than
lowering the interval — the server is starting, not failing.

---

## `service "web" depends on undefined service "api": invalid compose project`

**Cause.** `web` is in the `ui` profile but depends on `api`, which is in `core`. Enabling
only `ui` leaves the dependency undefined, and Compose rejects the whole project. The same
applies to `temporal-ui` in the `admin` profile, which depends on `temporal`.

**Fix.** Enable both profiles together. `mp2.sh up-ui` and `mp2.sh up-admin` already do
this (`--profile core --profile ui`). Running `docker compose --profile ui up` by hand will
fail.

---

## Containers die mid-run and every long job fails "transiently"

**Symptom.** Long-running work — a large ingest, a full analysis, a model server — dies
part way through with no error of its own. Individual commands work, so each failure looks
like a one-off. Services report `Up 20 seconds` when they should have been up for an hour.

**Diagnosis.** Ask how often the daemon has been stopping:

```bash
journalctl --since "1 hour ago" | grep -E "Stop(ping)? docker.service|Started docker.service"
```

On DEV-01 this showed `Started` / `Stopping` pairs every one to five minutes for hours. Each
cycle kills every container. The `dockerd` PID changes each time, which distinguishes it
from a container-level restart.

**Cause.** The WSL distro was being torn down between commands, taking the Docker daemon
with it. `uptime` is misleading here: WSL2 runs all distros in **one VM with one kernel**,
so `/proc/uptime` reflects the VM — which another distro (`docker-desktop`) was keeping
alive — not this distro.

**Fix.** `%USERPROFILE%\.wslconfig` sets `vmIdleTimeout=-1`, but it only applies after:

```powershell
wsl --shutdown
```

Confirm it took effect by checking that the configured limits are in force —
`nproc` should report the configured `processors`, not the host's thread count:

```bash
nproc; free -h | head -2
```

Then confirm the distro stops cycling: sample `uptime -s` twice with a minute of no WSL
session in between. The boot time must not change.

`infra/scripts/mp2.sh doctor` checks all of this.

---

## After a force-quit of Docker Desktop, or an abrupt WSL shutdown

Run the health check before trusting anything:

```bash
infra/scripts/mp2.sh doctor            # read-only
infra/scripts/mp2.sh doctor --repair   # act on findings
```

It extracts every MP2 and infrastructure image, because **there is no `docker fsck`** and a
truncated layer stays invisible until something next extracts it. It also checks WSL
resource policy, daemon stability, disk headroom and the named volumes.

PostgreSQL will replay WAL on first start after an abrupt stop; on DEV-01 that took about
80 seconds, during which it reports `the database system is starting up`. That is recovery
working, not damage. Verify afterwards with orphan and index checks — a clean database has
zero orphaned rows and no invalid indexes.

---

## `unpigz: corrupted -- crc32 mismatch` during a build

**Symptom.** A build fails extracting a base-image layer, and fails again identically on
retry.

**Cause.** The layer was written to Docker's content store while the volume backing the
WSL disk was full. On DEV-01, C: reached 0.21 GiB free and the write was truncated. Docker
does not detect this until the layer is next used.

**Fix.** Drop the corrupted content and re-pull:

```bash
docker builder prune -af
docker rmi python:3.12-slim-bookworm
docker pull python:3.12-slim-bookworm
```

Then address the underlying space problem — the distro should live on D:, not C:. See
DEV-01-AUDIT.md.

---

## `RuntimeError: cannot cache function ... no locator available`

**Symptom.** Audio extraction fails inside librosa with a numba caching error pointing at
`site-packages/librosa/core/notation.py`.

**Cause.** librosa's numba kernels JIT-compile on first use and try to write their cache
next to `site-packages`, which the unprivileged `mp2` container user cannot write to.

**Fix.** Already applied: the image sets `NUMBA_CACHE_DIR=/var/lib/mp2/numba-cache` (plus
`MPLCONFIGDIR` and `XDG_CACHE_HOME`) to writable paths. If you build a variant image, keep
these, or audio extraction fails the first time it runs as a non-root user.

---

## Analysis run stays at `created` forever

**Symptom.** `POST /analyze` returns 202 and a `workflow_id`, but the run never leaves
`created`. Temporal may even show the workflow as `Completed`.

**Diagnosis.** Check, in order:

```bash
infra/scripts/mp2.sh workflows                 # did the workflow run at all?
infra/scripts/mp2.sh queues mp2-io             # is a poller attached?
infra/scripts/mp2.sh logs worker-cpu 100       # is the worker healthy?
```

**Causes seen.**

- *No poller on the queue.* The worker is down or crash-looping — see the stale-IP entry.
- *Workflow completes but the run status never changes.* This was a real defect: the
  activities were no-op stubs that never wrote to PostgreSQL. Activities now persist
  measurements and set the run to `evidence_ready` in `AssembleEvidence`. If you see this
  again, the worker image is stale — rebuild with
  `docker compose --profile core build` and recreate.

---

## `alembic` reports no such table, or migrations look already applied

Run migrations inside the api container, never from Windows:

```bash
infra/scripts/mp2.sh migration-status
infra/scripts/mp2.sh migrate
```

`alembic.ini` carries a throwaway default URL; `MP2_DATABASE_URL` from the environment
always overrides it. If `migration-status` prints nothing, no migration has ever run
against that database.

Note that migration `0001` builds the schema with `Base.metadata.create_all` rather than
explicit DDL. A database created by `0001` matches the models at that revision, but
`--autogenerate` comparisons against later model edits can be noisy. See
IMPLEMENTATION_STATUS.md.

---

## `ffprobe failed` when registering an asset

**Symptom.** `POST /v1/media/{id}/assets` returns 422 with ffprobe output.

**Cause.** The file is not decodable media, or is not where the container thinks it is.

**Fix.** The path must be inside the container's ingest root. `data/ingest/` on the host is
mounted read-only at `/media`, so a host file at `data/ingest/clip.mp4` is
`/media/clip.mp4` to the API. Paths outside `/media` are refused with 403 by design.

---

## `local_path is outside the controlled ingest root` (HTTP 403)

Working as intended. Ingest is restricted to the mounted ingest root so the API cannot be
used to read arbitrary files from the container filesystem. Put the file in `data/ingest/`
and reference it as `/media/<name>`.

---

## Rights metadata rejected (HTTP 422)

Also working as intended. `provenance_class`, `rights_access_class` and `retention_class`
are required on every asset. MP2 never infers or defaults rights metadata. Valid values are
in `packages/domain/mp2_domain/enums.py`.

---

## No evidence claims on a completed run

**Symptom.** The run reaches `evidence_ready` with measurements but zero
`evidence_claims`, and the evidence summary carries a warning.

**Cause.** The Model Gateway could not be reached, or returned a non-200.

**This is deliberate behaviour, not a silent failure.** MP2 records the warning and keeps
the deterministic evidence rather than fabricating semantic claims. Check:

```bash
infra/scripts/mp2.sh local-only
infra/scripts/mp2.sh logs model-gateway 50
```

With no generative weights installed, the gateway routes to the deterministic baseline
adapter, which always returns schema-valid claims. If you get none, the worker could not
reach `model-gateway:8080` at all.

---

## ASR produces no utterances

If the source has no speech — the default `synthetic-av.mp4` fixture is a sine tone — zero
utterances is the correct result. Use `infra/scripts/mp2.sh speech-fixture` for a fixture
that actually contains speech.

If ASR is skipped entirely, the run's warnings will say so. `mp2_extractors.asr` resolves
weights from a local directory only and reports itself unavailable when they are missing:

```bash
docker run --rm --entrypoint python mp2-api:latest \
  -c "from mp2_extractors import asr; print(asr.is_available(), asr.model_directory())"
```

---

## Quoting breaks when driving WSL from Windows

**Symptom.** Commands sent as `wsl -d Ubuntu-24.04 -- bash -lc '...'` fail with empty
variables or mangled paths, especially with a repository path containing spaces.

**Fix.** Put anything non-trivial in a script file and invoke the file. When calling from
Git Bash, set `MSYS_NO_PATHCONV=1` so Git Bash does not rewrite `/mnt/...` arguments into
Windows paths.

---

## Shell scripts fail with `set: pipefail: invalid option name`

**Cause.** The script has CRLF line endings, so bash sees `pipefail\r`. This happens easily
when a file is rewritten by a Windows tool — Python's `write_text` translates `\n` to
`\r\n` on Windows.

**Fix.** Convert to LF. `.gitattributes` sets `* text=auto`, so Git normalizes on commit,
but a file edited in place on disk can still be CRLF:

```bash
sed -i 's/\r$//' infra/scripts/mp2.sh
```

---

## Disk fills up

```bash
docker system df
docker builder prune -af
infra/scripts/mp2.sh clean-derived
```

Keep everything on D:. C: is the small system volume and must not host container data,
model weights or corpus material. The N: volume is labelled `Failing` and must not be used
at all.
