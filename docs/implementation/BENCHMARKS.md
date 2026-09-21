# Benchmarks

Measured on DEV-01. Every number here was observed, not estimated. Where something was not
measured, it says so rather than carrying a plausible-looking figure.

## Machine

Intel Core i7-4770 (4 cores / 8 threads, Haswell, 2013) · 23.94 GiB RAM · AMD Radeon RX 580
(no CUDA) · Windows 10 Pro 19045 · WSL2 Ubuntu 24.04.

Two characteristics dominate everything below:

- **No GPU acceleration is available.** All extraction and inference is CPU-only.
- **Storage is the bottleneck, and it is on USB.** The only volume with free space (D:,
  3 TB) is an external **USB** Seagate, so the WSL ext4 virtual disk, Docker's overlayfs,
  PostgreSQL and every build all run across USB. The internal SSD (C:) has ~13 GiB free and
  the internal HDD (N:) is labelled `Failing` by the owner.

Measured during an image build: the virtual disk was **100% busy while completing roughly
270 KB of writes in 20 seconds**, with requests queued. No I/O errors, no USB resets, all
disks SMART-healthy — simply slow. Treat every build and first-run figure below as a
property of *this storage*, not of the software.

## Test fixtures

| Fixture | Size | Duration | Content |
|---|---:|---:|---|
| `synthetic-av.mp4` | 145,955 B | 4.00 s | `testsrc2` video + 440 Hz sine, no speech |
| `synthetic-speech.mp4` | 206,978 B | 8.58 s | `testsrc2` then `smptebars` (one genuine cut) + synthesised speech |

Both are fully synthetic and generated locally. SHA-256 of `synthetic-av.mp4` is
`d115e13e…646435`, reproduced exactly by the ingest hash — the first concrete evidence that
the hashing path is correct.

Generating the speech fixture takes **5 m 40 s**, almost all of it `apt-get` installing
espeak-ng in a throwaway container on this storage. The MP2 image supplies ffmpeg for the
mux stage; installing ffmpeg in the throwaway container instead pushed this past 20 minutes.

## Image build

| Stage | Observation |
|---|---|
| Full cold build (cache pruned) | **~25 min.** apt alone ran 1,300 s for ~190 packages (ffmpeg pulls the bulk), then the pip layer (librosa/numba/llvmlite/OpenCV/CTranslate2) |
| apt layer | ~7 s per package during unpack — the clearest signal of the storage bottleneck |
| Whisper `tiny` weights | ~75 MB, downloaded once at build time |
| Runtime image | 3.06 GB |

**The Dockerfile was restructured because of this.** Originally the source tree was copied
*before* `pip install`, so editing a single Python file invalidated the dependency layer and
forced a full reinstall — tens of minutes for a one-line change. Dependencies are now
installed from `pyproject.toml` alone, before any source is copied, and the application is
installed afterwards with `--no-deps`. The dependency list is read out of `pyproject.toml`
at build time rather than duplicated, so the two cannot drift.

Effect: a code-only rebuild now re-runs just the `COPY` and `pip install --no-deps .` steps
instead of the whole dependency stack.

**Practical guidance:** a cold build here is a coffee-break operation. Do not prune the
build cache unless something is genuinely corrupt. Moving the repository from `/mnt/d` (9p
over USB) onto the Linux filesystem would materially improve build and test times.

## Test suite

Run inside the runtime image via `infra/scripts/mp2.sh test`.

| Suite | Result | Wall clock |
|---|---|---:|
| Unit + reproducibility (`mp2.sh test`) | **37 passed, 10 skipped** | 48.9 s |
| Integration against the live stack (`mp2.sh test-integration`) | **10 passed** | 17.7–21.3 s |
| `ruff check` | clean | seconds |
| `mypy --strict` (19 source files) | clean | seconds |

The 10 skips in the first row are the integration tests, which skip when no stack is
reachable; `test-integration` runs them on the `app` network against `http://api:8000`.

Time is dominated by numba JIT warm-up inside librosa and by `calcOpticalFlowFarneback` in
the visual extractor.

## Stack startup

| Operation | Wall clock |
|---|---:|
| `mp2.sh up` — six core services to healthy (images present) | ~60 s |
| PostgreSQL healthy | ~10 s |
| Temporal healthy (after PostgreSQL) | 50–56 s |
| API healthy | ~30 s |
| `alembic upgrade head` — 20 tables + pgvector + constraints | < 5 s |
| `mp2.sh backup` — full `pg_dump -Fc` after one analysis run | < 5 s, 64,654 B |

Temporal needs a `start_period` of 180 s in its health check on this machine: schema setup
plus first start regularly exceeds a naive retry budget, and without it Compose declares the
container unhealthy and refuses to start the worker.

## WSL relocation

| Operation | Wall clock | Effect |
|---|---:|---|
| `wsl --manage Ubuntu-24.04 --move D:\WSL\Ubuntu-24.04` (8.87 GB) | 80 s | C: free 0.21 GiB → 12.98 GiB |

Roughly 110 MB/s, consistent with a sustained sequential mechanical transfer.

## Ingest and analysis

| Operation | Observation |
|---|---|
| Register work, hash, ffprobe, store to SeaweedFS, create run, dispatch workflow | Sub-second for a 207 KB fixture |
| SHA-256 | Matched the fixture's independently computed hash exactly |

Full analysis of `synthetic-speech.mp4` (8.58 s, 320×180, h264 + aac), ten activities from
`ValidateSource` to `AssembleEvidence`:

| Run | Wall clock |
|---|---:|
| First run (cold numba JIT in a fresh container) | **55 s** |
| Repeat run during the G5 test | **~20 s** |

Output of one run: 2 shot segments, 17 measurements across 7 extractors, 2 scene packets,
8 evidence claims, 2 model executions, 274,510 B of normalized audio. PySceneDetect placed
the cut at 4,292 ms — the exact midpoint of the two-half fixture.

ASR (Whisper `tiny`, CPU, int8) detected `en` at p=0.974 and returned 2 utterances. The
transcript is recognisable but imperfect, as expected from `tiny`:
"The quick brown fox jump over the rainy dog. Media personality projection analyzer scene,
hot people." Accuracy is a model-size choice, not a pipeline defect.

## Resource envelope

WSL is allocated 12 GB RAM / 6 threads / 4 GB swap by `.wslconfig` (in force after the next
`wsl --shutdown`; 11 GiB / 3 GiB was observed before that).

Observed while the core stack was idle: ~1.8 GiB used, ~6.0 GiB free, ~4.4 GiB cache, swap
essentially untouched. The core profile is comfortable inside a 12 GB allocation.

Compose limits: postgres/api/model-gateway 1 GB each, temporal 1.5 GB, seaweedfs 1.2 GB,
worker-cpu 4 GB / 3 CPUs. The worker is capped at 4 activity threads
(`MP2_ACTIVITY_THREADS`) and `OMP_NUM_THREADS=3` so numeric libraries do not oversubscribe
8 threads.

## Not measured

Stated explicitly so nothing here is over-read:

- **Full-length feature analysis.** Only short synthetic fixtures have been processed. Do
  not extrapolate per-second costs from an 8-second clip — shot detection and optical flow
  scale with resolution and shot count, not just duration.
- **Generative inference.** No LLM weights are installed; the llama.cpp path has never run.
- **GPU throughput.** No CUDA device exists on this machine.
- **Embedding generation.** sentence-transformers is not installed.
- **Concurrent analysis runs.** Only one run at a time has been exercised.
- **Corpus-scale storage growth.** No estimate should be quoted until a full-length work has
  been analysed end to end.

## How to reproduce

```bash
infra/scripts/mp2.sh up
infra/scripts/mp2.sh migrate
infra/scripts/mp2.sh fixture
infra/scripts/mp2.sh test
time infra/scripts/mp2.sh down && time infra/scripts/mp2.sh up
```
