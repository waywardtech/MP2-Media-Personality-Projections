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

## Scaling

Measured with `mp2.sh bench-fixtures` + `mp2.sh benchmark`, on a synthetic ladder that
varies duration, resolution and shot count **one at a time** so cost can be attributed to a
driver rather than inferred from a single clip. Raw results: `data/benchmark-results.json`.

| Fixture | Media | Wall | vs realtime | Segments | Measurements | Claims |
|---|---:|---:|---:|---:|---:|---:|
| 8.6 s 320×180, 2 shots (speech) | 8.6 s | 60.6 s | 7.06× | 2 | 17 | 8 |
| 30 s 640×360, 6 shots | 30.0 s | 15.2 s | 0.51× | 6 | 37 | 24 |
| 60 s 640×360, 12 shots | 60.0 s | 18.2 s | 0.30× | 12 | 67 | 48 |
| 60 s 1280×720, 12 shots | 60.0 s | 27.2 s | 0.45× | 12 | 67 | 48 |
| 300 s 640×360, 60 shots | 300.0 s | 44.4 s | **0.15×** | 60 | 307 | 240 |

**Steady state is faster than realtime.** The 300 s run analysed five minutes of video in
44 seconds. The apparent 7× on the first row is not a small-file penalty — see cold start.

### Per-activity breakdown (seconds)

| Activity | 8.6 s | 30 s | 60 s 360p | 60 s 720p | 300 s |
|---|---:|---:|---:|---:|---:|
| ValidateSource | 0.5 | 0.1 | 0.1 | 0.1 | 0.1 |
| ProbeAndHash | 0.3 | 0.2 | 0.2 | 0.2 | 0.2 |
| NormalizeAsset | 0.2 | 0.2 | 0.3 | 0.3 | 0.7 |
| SegmentShots | 6.6 | 0.6 | 1.2 | 3.0 | 5.5 |
| **ExtractVisual** | 0.3 | 2.6 | 2.9 | **11.2** | **15.9** |
| ExtractAudio | **40.2** | 0.7 | 1.7 | 1.3 | 6.8 |
| Transcribe | 4.8 | 3.0 | 2.2 | 2.2 | 5.9 |
| BuildScenePackets | 0.1 | 0.2 | 0.3 | 0.3 | 1.0 |
| InterpretSemantics | 0.3 | 0.2 | 0.3 | 0.3 | 1.1 |

### Cold start dominates the first run

`ExtractAudio` took **40.2 s** on the first analysis and 0.7–6.8 s on every one after. That
is librosa's numba kernels JIT-compiling on first use in a fresh worker process — a
one-off cost per worker, not per work. Anything that restarts workers frequently pays it
repeatedly, which is a reason to prefer long-lived workers over per-job containers.

### Resolution is the dominant driver, not duration

Holding duration and shot count fixed and going 640×360 → 1280×720 (**4× the pixels**):

- `ExtractVisual` 2.9 s → 11.2 s — **3.84×**, i.e. essentially linear in pixel count.
- Total wall 18.2 s → 27.2 s.

Duration scales sub-linearly (30 s → 60 s cost 15.2 s → 18.2 s), because fixed per-run work
amortises. Shot count drives segment fan-out: rows scale linearly with segments, but the
per-segment extraction cost is small compared with resolution.

**Optical flow in `ExtractVisual` is the thing to optimise first** if throughput matters. It
is currently computed between all sampled frames per segment.

### Rates (from the 300 s run — warmest and largest)

| Per media-minute | Value |
|---|---:|
| Wall clock | 8.9 s |
| Segments | 12.0 |
| Measurements | 61.4 |
| Evidence claims | 48.0 |
| Normalized audio | 1,920,220 B |
| Derived artifacts | 38,832 B |
| **MP2-added storage** | **1.96 MB** (excludes the raw source) |

Normalized audio is 16 kHz mono 16-bit PCM — exactly 1.92 MB/min by construction, and
~98% of MP2-added storage. Encoding it as FLAC would roughly halve total added storage
at no loss; it is left uncompressed for now because every audio extractor reads it directly.

## Corpus projection

Extrapolated from the measured rates for a **110-minute feature**. These are projections,
not measurements — no full-length work has been analysed.

| | 640×360 | 1280×720 | 1920×1080 |
|---|---:|---:|---:|
| Wall clock per feature | **16.3 min** | **33.7 min** | **62.8 min** |
| of which ExtractVisual | 5.8 min | 23.3 min | 52.3 min |

At 1080p, visual extraction is ~83% of total time, which follows directly from the linear
pixel scaling above.

Per feature: ~1,320 segments, ~6,750 measurement rows, ~215 MB MP2-added storage.

**A 50-work corpus** would therefore need roughly:

| | Value |
|---|---:|
| Segment rows | ~66,000 |
| Measurement rows | ~338,000 |
| Evidence claim rows | ~264,000 |
| MP2-added storage | **~10.8 GB** (plus the raw sources themselves) |
| Single-worker wall clock @ 360p | ~13.6 h |
| Single-worker wall clock @ 1080p | **~52 h** |

Those row counts are unremarkable for PostgreSQL. The constraints are wall clock at full
resolution and raw-source storage — and on DEV-01 the raw sources would land on a USB
volume. Analysis is horizontally scalable by task queue, so wall clock is the easier of
the two to fix.

## Generative interpretation cost

Measured on DEV-01 with one real scene packet (621 prompt tokens), CPU-only,
grammar-constrained decoding against MP2's `scene-evidence/1` contract.

| Adapter | Per scene | Throughput | Schema valid |
|---|---:|---:|---|
| Deterministic baseline | **0.2–1.1 s** | n/a | always (by construction) |
| Qwen2.5-1.5B Q4_K_M | **30.4 s** | 9.1 tok/s | yes |
| Qwen2.5-7B Q4_K_M | **116.9 s** | 1.8 tok/s | yes |

### What this costs at corpus scale

A 110-minute feature yields ~1,320 shot segments, and the pipeline currently issues one
semantic request per segment:

| Adapter | Per feature | 50-work corpus |
|---|---:|---:|
| Deterministic baseline | ~15 min | ~12 h |
| Qwen2.5-1.5B | **~11 h** | **~23 days** |
| Qwen2.5-7B | **~43 h** | **~89 days** |

Add ~11 minutes of model load time per `llama-server` restart for the 7B, since 4.7 GB has
to be paged off a USB volume.

**Scene-level generative interpretation is not viable for a 50-work corpus on this
machine.** That is a hardware conclusion, not a design defect — the pipeline is correct and
the contract holds — but it has an architectural consequence worth deciding deliberately:

1. **Be selective.** Run deterministic extraction over every scene, and generative
   interpretation only over scenes that the measurements flag as interesting. The
   deterministic layer already produces the signal needed to choose. This keeps the
   generative pass proportional to what it can actually add.
2. **Change the granularity.** Interpret at act/sequence level over aggregated
   measurements rather than per shot. ~1,320 requests becomes ~30.
3. **Move the work.** A GPU, or an external provider through the Model Gateway, which the
   contract already supports and policy currently disables.

Option 1 or 2 keeps everything local. Both are compatible with the existing contract, so
this is a scheduling decision rather than a rewrite.

### Quality is the reason to care about model size

Both models produced schema-valid evidence, because the grammar guarantees that. They
differed in grounding:

- **7B** cited specific measurement fields (`visual_measurements.luminance_mean`) and put
  measured numbers into `structured_value`.
- **1.5B** cited whole measurement blocks and over-read the transcript, asserting
  "fast-paced conversational pacing" with nothing measured to support it.

For an evidence system, grounding discipline is the product. Any quality gate should test
whether claims are *supported*, not merely well-formed — the schema already guarantees the
latter and it says nothing about the former.

## Not measured

Stated explicitly so nothing here is over-read:

- **No real full-length work has been analysed.** The corpus projection above is arithmetic
  on synthetic measurements. Real film differs in ways that matter: far more shots per
  minute in action sequences, real dialogue for ASR, variable bitrate, and letterboxing.
- **Generative inference at scale.** Both models were measured on a *single* scene packet,
  not across a full run. Per-scene cost should be stable, but prompt length grows with
  transcript density in real dialogue, and KV-cache reuse across scenes has not been
  explored. The `InterpretSemantics` figures in the scaling table above are the
  deterministic baseline, not a generative model.
- **ASR at scale.** Whisper `tiny` on a tone bed is not representative of a real dialogue
  track; expect `Transcribe` to grow substantially with real speech and a larger model.
- **GPU throughput.** No CUDA device exists on this machine.
- **Embedding generation.** sentence-transformers is not installed.
- **Concurrent analysis runs.** Every measurement above is a single run at a time.

## How to reproduce

```bash
infra/scripts/mp2.sh up
infra/scripts/mp2.sh migrate
infra/scripts/mp2.sh fixture
infra/scripts/mp2.sh test

# scaling measurements
infra/scripts/mp2.sh bench-fixtures
infra/scripts/mp2.sh benchmark
```
