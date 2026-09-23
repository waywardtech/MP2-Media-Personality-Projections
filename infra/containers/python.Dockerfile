FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# ffmpeg/ffprobe are first-class MP2 extractors, not conveniences.
# libgl1 + libglib2.0-0 are OpenCV runtime dependencies; libsndfile1 backs soundfile/librosa.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libgl1 libglib2.0-0 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependencies ---------------------------------------------------------------------------
# Third-party dependencies install from pyproject.toml ALONE, before any source is copied,
# so editing MP2 code does not invalidate this layer. That matters: reinstalling librosa,
# numba and OpenCV takes tens of minutes on DEV-01's storage. The dependency list is read
# out of pyproject.toml rather than duplicated here, so the two cannot drift apart.
COPY pyproject.toml /app/
# Which optional groups to install. Override to build a slimmer image, e.g.
#   --build-arg MP2_EXTRAS=extractors     (no telemetry)
#   --build-arg MP2_EXTRAS=               (API/gateway only, no extraction stack)
ARG MP2_EXTRAS=extractors,observability
ENV MP2_EXTRAS=${MP2_EXTRAS}
RUN python -c "import tomllib, os; p=tomllib.load(open('pyproject.toml','rb'))['project']; \
extras=p.get('optional-dependencies',{}); deps=list(p['dependencies']); \
[deps.extend(extras.get(n,[])) for n in os.environ['MP2_EXTRAS'].split(',') if n]; \
open('/tmp/requirements.txt','w').write(chr(10).join(deps)+chr(10))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# --- Local ASR weights ----------------------------------------------------------------------
# Baked at build time so the runtime never reaches the network: local-only mode must work
# with egress blocked. Whisper weights are MIT (OpenAI); the CTranslate2 conversion and
# faster-whisper runtime are MIT. Recorded in docs/implementation/LICENSES_AND_MODELS.md.
# Build with --build-arg MP2_WHISPER_REPO= to produce an image with no model weights.
ARG MP2_WHISPER_REPO=Systran/faster-whisper-tiny
ENV MP2_WHISPER_MODEL_DIR=/opt/mp2/models/whisper
RUN if [ -n "$MP2_WHISPER_REPO" ]; then \
      python -c "from huggingface_hub import snapshot_download; \
snapshot_download('$MP2_WHISPER_REPO', local_dir='/opt/mp2/models/whisper')" \
      && rm -rf /root/.cache/huggingface; \
    else mkdir -p /opt/mp2/models/whisper; fi

# --- Application ------------------------------------------------------------------------------
# Source last, installed with --no-deps: only these cheap layers rebuild on a code change.
COPY apps /app/apps
COPY services /app/services
COPY packages /app/packages
COPY migrations /app/migrations
COPY alembic.ini /app/
RUN pip install --no-cache-dir --no-deps .

# librosa's numba kernels JIT-compile on first use and try to cache next to site-packages,
# which is not writable by the unprivileged runtime user. Point the cache somewhere writable
# so audio extraction works as mp2 rather than failing on first call.
ENV NUMBA_CACHE_DIR=/var/lib/mp2/numba-cache \
    MPLCONFIGDIR=/var/lib/mp2/mpl-cache \
    XDG_CACHE_HOME=/var/lib/mp2/cache

RUN addgroup --system mp2 && adduser --system --ingroup mp2 mp2 \
    && mkdir -p /var/lib/mp2/objects /var/lib/mp2/work /var/lib/mp2/numba-cache \
                /var/lib/mp2/mpl-cache /var/lib/mp2/cache \
    && chown -R mp2:mp2 /var/lib/mp2 /opt/mp2
USER mp2

# --- Test stage -------------------------------------------------------------------------------
# Deployable images must not ship a test runner. `--target test` adds one on top of the
# exact runtime image, so the suite runs against what actually ships.
FROM runtime AS test
USER root
RUN pip install --no-cache-dir "pytest>=8.3,<9" "pytest-asyncio>=0.25,<1" \
      "ruff>=0.9,<1" "mypy>=1.14,<2"
USER mp2
