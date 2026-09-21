"""Versioned extractor registry.

Every MP2 extractor is declared here with a stable identity, a version, a repeatability
class and a license record. Nothing in this package imports a model-provider SDK; adapters
return plain data and the orchestrator is responsible for persistence and lineage.

Repeatability classes
---------------------
D0  bit-identical output for identical input, parameters and adapter version.
D1  numerically stable within a documented tolerance (e.g. threaded float reductions).
D2  not guaranteed repeatable (generative / sampled output).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExtractorSpec:
    extractor_id: str
    version: str
    repeatability_class: str
    input_schema_version: str
    output_schema_version: str
    parameters: dict[str, Any] = field(default_factory=dict)
    license_record: str = "unrecorded"
    hardware_class: str = "cpu"

    def with_parameters(self, **overrides: Any) -> ExtractorSpec:
        merged = {**self.parameters, **overrides}
        return ExtractorSpec(**{**asdict(self), "parameters": merged})

    @property
    def identity(self) -> str:
        return f"{self.extractor_id}@{self.version}"


def content_hash(value: Any) -> str:
    """Canonical hash of an extractor output, used for D0 rerun comparison."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _installed_version(module_name: str, fallback: str) -> str:
    """Record the version actually installed, not the one we hoped for.

    A measurement is only reproducible if the extractor version stored beside it is the
    one that produced it, so this resolves at import time rather than being hard-coded.
    """
    try:
        from importlib.metadata import version

        return version(module_name)
    except Exception:
        return fallback


def _tool_version(binary: str, args: list[str], fallback: str) -> str:
    """Version of an external binary such as ffmpeg."""
    import re
    import shutil
    import subprocess

    if shutil.which(binary) is None:
        return fallback
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True,
                             check=False, timeout=10).stdout
        match = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", out)
        return match.group(1) if match else fallback
    except Exception:
        return fallback


_FFMPEG_VERSION = _tool_version("ffmpeg", ["-version"], "unknown")
_OPENCV_VERSION = _installed_version("opencv-python-headless", "unknown")
_SCENEDETECT_VERSION = _installed_version("scenedetect", "unknown")
_LIBROSA_VERSION = _installed_version("librosa", "unknown")
_FASTER_WHISPER_VERSION = _installed_version("faster-whisper", "unknown")


# --- Deterministic / specialist extractors -------------------------------------------------

FFPROBE = ExtractorSpec(
    extractor_id="ffprobe",
    version=f"ffmpeg-{_FFMPEG_VERSION}",
    repeatability_class="D0",
    input_schema_version="media-file/1",
    output_schema_version="ffprobe-json/1",
    parameters={"show_format": True, "show_streams": True},
    license_record="FFmpeg LGPL-2.1-or-later (Debian build); see LICENSES/THIRD_PARTY",
    hardware_class="cpu",
)

FFMPEG_NORMALIZE_AUDIO = ExtractorSpec(
    extractor_id="ffmpeg-normalize-audio",
    version=f"ffmpeg-{_FFMPEG_VERSION}",
    repeatability_class="D0",
    input_schema_version="media-file/1",
    output_schema_version="pcm-wav-16k-mono/1",
    parameters={"sample_rate": 16000, "channels": 1, "codec": "pcm_s16le"},
    license_record="FFmpeg LGPL-2.1-or-later (Debian build)",
    hardware_class="cpu",
)

PYSCENEDETECT = ExtractorSpec(
    extractor_id="pyscenedetect-content",
    version=_SCENEDETECT_VERSION,
    repeatability_class="D0",
    input_schema_version="media-file/1",
    output_schema_version="shot-boundaries/1",
    parameters={"detector": "ContentDetector", "threshold": 27.0, "min_scene_len": 12},
    license_record="BSD-3-Clause",
    hardware_class="cpu",
)

OPENCV_VISUAL = ExtractorSpec(
    extractor_id="opencv-visual",
    version=_OPENCV_VERSION,
    repeatability_class="D0",
    input_schema_version="media-file/1",
    output_schema_version="visual-measures/1",
    parameters={"frames_per_segment": 3, "hue_bins": 18, "canny_low": 100, "canny_high": 200},
    license_record="Apache-2.0",
    hardware_class="cpu",
)

LIBROSA_AUDIO = ExtractorSpec(
    extractor_id="librosa-audio",
    version=_LIBROSA_VERSION,
    repeatability_class="D1",
    input_schema_version="pcm-wav-16k-mono/1",
    output_schema_version="audio-measures/1",
    parameters={"n_mfcc": 13, "silence_rms_threshold": 0.01},
    license_record="ISC",
    hardware_class="cpu",
)

FASTER_WHISPER = ExtractorSpec(
    extractor_id="faster-whisper",
    version=f"{_FASTER_WHISPER_VERSION}-tiny",
    repeatability_class="D1",
    input_schema_version="pcm-wav-16k-mono/1",
    output_schema_version="asr-transcript/1",
    parameters={"beam_size": 1, "temperature": 0.0, "vad_filter": False, "compute_type": "int8"},
    license_record="Model: OpenAI Whisper MIT. Runtime: CTranslate2 MIT, faster-whisper MIT.",
    hardware_class="cpu",
)

# Declared but intentionally unavailable in the M0-M4 image: sentence-transformers pulls
# PyTorch (~2.5 GB). The adapter boundary exists so embeddings can be enabled later.
SENTENCE_EMBEDDING = ExtractorSpec(
    extractor_id="sentence-transformer-embedding",
    version="all-MiniLM-L6-v2",
    repeatability_class="D1",
    input_schema_version="utterance-text/1",
    output_schema_version="embedding-vector/1",
    parameters={"dimensions": 384, "normalize": True},
    license_record="Model: Apache-2.0 (all-MiniLM-L6-v2). Runtime: Apache-2.0.",
    hardware_class="cpu-or-gpu",
)

TEXT_STATISTICS = ExtractorSpec(
    extractor_id="dialogue-statistics",
    version="1",
    repeatability_class="D0",
    input_schema_version="asr-transcript/1",
    output_schema_version="dialogue-measures/1",
    parameters={},
    license_record="MP2 proprietary",
    hardware_class="cpu",
)

ALL_EXTRACTORS: tuple[ExtractorSpec, ...] = (
    FFPROBE,
    FFMPEG_NORMALIZE_AUDIO,
    PYSCENEDETECT,
    OPENCV_VISUAL,
    LIBROSA_AUDIO,
    FASTER_WHISPER,
    TEXT_STATISTICS,
    SENTENCE_EMBEDDING,
)

BY_ID = {spec.extractor_id: spec for spec in ALL_EXTRACTORS}
