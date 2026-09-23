# DEV-01 machine audit

Audit date: 2026-09-20. All values were read live from the running machine with
non-destructive commands (Windows CIM/PowerShell, and Linux-native tooling inside WSL).
Nothing here is inferred; anything not directly observed is marked as such.

> **The historical hardware profile in the project brief does not match this machine.**
> The brief describes an HP h8-1534 with an AMD FX-8300 and an NVIDIA GTX 1070 (8 GB VRAM).
> DEV-01 is in fact a Dell OptiPlex 9020 with an Intel i7-4770 and an **AMD** Radeon RX 580.
> There is no NVIDIA GPU. The live machine is treated as authoritative, and the NVIDIA/CUDA
> path in the brief is therefore not available here.

## Host

| Item | Detected value |
|---|---|
| Windows | Microsoft Windows 10 Pro, 10.0.19045, build 19045.6466 |
| Machine | Dell Inc. OptiPlex 9020 |
| CPU | Intel Core i7-4770 @ 3.40 GHz — 4 cores / 8 threads |
| Installed RAM | 25,709,211,648 bytes (23.94 GiB) |
| GPU | AMD Radeon RX 580 2048SP, 4,293,918,720 adapter bytes (~4 GB), driver 31.0.21923.1000 |
| Secondary display adapter | Microsoft Remote Display Adapter (RDP session) |
| NVIDIA | **None.** No NVIDIA adapter and no `nvidia-smi` on the host or in WSL |
| CPU virtualization firmware flag | CIM reports `VirtualizationFirmwareEnabled = False` and `SLAT = False`, yet WSL2 runs correctly. The CIM flags are unreliable on this platform; WSL2 operation is the authoritative signal. |

## Storage

### Physical disks

| # | Device | Media | Bus | Size | Health | Hosts |
|---|---|---|---|---:|---|---|
| 0 | KingFast | **SSD** | RAID | 238 GB | Healthy | C: |
| 1 | Hitachi HUS724030ALE641 | HDD | RAID | 2,795 GB | Healthy (but see below) | N: |
| 2 | Seagate Portable | Unspecified | **USB** | 4,658 GB | Healthy | D: |

### Volumes

Measured at the end of the session, after the WSL relocation described below.

| Volume | Label | Filesystem | Size | Free | Note |
|---|---|---|---:|---:|---|
| C: | Windows | NTFS | 237.48 GiB | 12.98 GiB | **The only SSD.** Was **0.21 GiB** mid-session before remediation. |
| D: | SeagateExFat5Tb | NTFS | 4,657.33 GiB | 3,077.87 GiB | **External USB drive.** Repository, WSL distro and all MP2 data |
| G: | Google Drive | FAT32 | 237.48 GiB | 6.15 GiB | Virtual/sync drive; not used by MP2 |
| N: | **Failing** | NTFS | 2,794.50 GiB | 8.16 GiB | Labelled `Failing` by the owner. **Must not be used.** |

### The storage problem on this machine

This is the single biggest practical constraint on DEV-01, and it has no clean solution
with the current hardware:

- **C: is the only SSD, and it has ~13 GiB free.** Too small to host the WSL distro plus
  Docker images and volumes, which already exceed 20 GB.
- **N: is a 2.8 TB internal HDD, but the owner has labelled it `Failing`** and it has 8 GiB
  free. It must not be used for any MP2 data.
- **D: has 3 TB free but is an external USB drive.** It is the only volume with room, so
  the WSL distro, Docker storage and the repository all live there.

Running WSL2's ext4 virtual disk — with Docker overlayfs, PostgreSQL and image builds on
top — over USB produces poor random-write performance. Measured during an image build:
the virtual disk was **100% busy while completing roughly 270 KB of writes in 20 seconds**,
with several requests queued. Nothing is failing; `dmesg` shows no I/O errors and no USB
resets, and SMART reports all three disks healthy. It is simply slow.

Consequences, all of them performance rather than correctness:

- A cold image build takes tens of minutes, dominated by apt unpack (~7 s per package).
- The test suite takes about three minutes.
- Runtime behaviour of the stack is fine; this hurts builds and first-run JIT, not serving.

**Recommendation (needs owner action, not something to work around in code):** free space on
the internal SSD, or add an internal SSD, and host the WSL distro and Docker storage there.
Keep bulk corpus media on D:. This would be the single largest improvement to iteration
speed on this machine.

### Storage incident and remediation

At the start of the session C: had 6.48 GiB free and the `Ubuntu-24.04` distro's
`ext4.vhdx` (8.87 GB) lived on C:. During the session C: fell to **0.21 GiB free**, which
is a Windows-stability risk and almost certainly caused the corrupted Docker layer observed
later (`unpigz: crc32 mismatch` on a base-image layer written while the volume was full).

Remediation, with the owner's approval:

```
wsl --shutdown
wsl --manage Ubuntu-24.04 --move D:\WSL\Ubuntu-24.04
```

This is non-destructive and reversible. It completed in 80 seconds and returned C: to
12.98 GiB free. The distro now reports 1007 GiB total / ~935 GiB available on `/`, backed
by D:. The corrupted Docker build cache was then pruned (12.56 GB) and the base image
re-pulled.

**Storage is no longer an MVP blocker.** C: remains tight in absolute terms and should not
host container or corpus data.

## WSL / Linux

| Item | Detected value |
|---|---|
| WSL | 2.7.14.0, kernel 6.18.33.2-microsoft-standard-WSL2, default version 2 |
| Distributions | `Ubuntu` (on C:), `Ubuntu-24.04` (**on D:**, MP2 reference), `docker-desktop` |
| Reference distro | Ubuntu 24.04.4 LTS |
| systemd | `running` — available and used to supervise the Docker daemon |
| CPU visible to WSL | 8 threads |
| Memory / swap | 11 GiB / 3 GiB observed before configuration; now set explicitly (below) |
| Docker Engine | **29.1.3** (Ubuntu package `29.1.3-0ubuntu3~24.04.2`), inside Ubuntu-24.04 |
| Docker Compose | v2.40.3 (plugin, Ubuntu package) |
| Docker permissions | session user is in the `docker` group; no sudo needed for container work |
| Git | 2.53.0.windows.3 (Windows), 2.43.0 (Ubuntu) |
| Git identity in WSL | **unset** — `user.name` / `user.email` are not configured. Required before committing from Linux. |
| Python | 3.12.3 (Ubuntu, with `venv`); Windows has 3.11.9 default and 3.12.x available |
| Node / npm | v24.15.0 / 11.12.1 (Windows) |

Docker Engine runs **inside Linux**, not via Docker Desktop. Docker Desktop is installed and
its `docker-desktop` distro runs, but MP2 does not depend on it.

### WSL resource policy

WSL previously used its defaults, which happened to land near the target but were not
reproducible. `C:\Users\DTeix\.wslconfig` now states the policy explicitly:

```ini
[wsl2]
memory=12GB          # ~50% of 23.94 GiB; leaves ample RAM for Windows
processors=6         # most, not all, of 8 threads
swap=4GB
swapFile=D:\\WSL\\swap.vhdx   # keep swap off the small C: volume
vmIdleTimeout=-1     # do not auto-terminate the VM (see below)
[experimental]
sparseVhd=true       # return freed space to the host
```

**This file takes effect only after the next `wsl --shutdown`.** Until then the previous
11 GiB / 3 GiB allocation remains in force.

`vmIdleTimeout=-1` addresses a concrete failure observed during this session: with no
`.wslconfig`, WSL terminated the distro between commands, which stopped the Docker daemon
and silently exited every running container (all six exited with status 0 simultaneously).
Long-running stacks are not viable without it.

## GPU

- The GPU is an **AMD Radeon RX 580**, not the NVIDIA GTX 1070 in the historical profile.
- `nvidia-smi` is absent on Windows and in WSL. There is no CUDA path on this machine.
- The `gpu` Compose profile is therefore **not enabled by default** and is untested here.
- All MP2 work runs CPU-only. llama.cpp remains the declared local generative target and is
  configured CPU-first; vLLM is not required and is not used.
- No Linux NVIDIA kernel driver was installed inside WSL (explicitly prohibited, and moot).

## Ports

No listeners were present on 5432, 7233, 8233, 8333, 9333, 8000, 5173, 4317, 4318, 9090,
3000 or 8080 before MP2 started.

One pre-existing listener was found on `*:80` and `[::1]:80`, owned by `com.docker.backend`
(Docker Desktop) and `wslrelay`. **It does not belong to MP2** and is recorded here so it is
not mistaken for an MP2 exposure during a G7 review.

## Classification

**AMBER.**

Sufficient for the intended local vertical slice, with real constraints:

- 4-core/8-thread Haswell-era CPU; extraction is slow but correct (the test suite takes
  roughly three minutes, dominated by numba JIT and optical flow).
- No NVIDIA GPU, so the GPU profile and any CUDA-dependent work are unavailable.
- 12 GiB allocated to WSL, so heavy Compose profiles must stay opt-in rather than default.
- C: remains small; all container, model and corpus data must stay on D:.
- The N: volume is labelled `Failing` and must not be used for any MP2 data.

Not GREEN because of the CPU class, the absent GPU path and the C: headroom. Not RED
because every blocking requirement for M0–M4 is satisfied and was exercised end to end.

## Verified during this audit

- WSL2 + Ubuntu 24.04 with systemd: working.
- Docker Engine + Compose inside Linux: working, full core profile healthy.
- PostgreSQL 18.6 with pgvector 0.8.6: working, migrations applied.
- Data persistence across container removal, a cross-volume distro move and a WSL restart:
  verified with matching row counts and an unchanged SHA-256.

## Unverified / not applicable

- Motherboard virtualization configuration beyond the conflicting CIM flag (reading it
  would require entering firmware setup, which is out of scope).
- GPU acceleration of any kind — no supported path exists on this hardware.
- The `ops` and `ui` Compose profiles were not started during this audit.
