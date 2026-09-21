"""D0 rerun equivalence.

A D0 extractor must produce a bit-identical content hash when rerun on identical input
with identical parameters. This is the mechanical check behind acceptance gate G3; without
it "reproducible" is an assertion rather than a measurement.

These tests need ffmpeg/OpenCV and the generated fixture, so they run inside the MP2 image
(`infra/scripts/mp2.sh test`) and skip cleanly elsewhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mp2_extractors import (
    FFMPEG_NORMALIZE_AUDIO,
    LIBROSA_AUDIO,
    OPENCV_VISUAL,
    PYSCENEDETECT,
    audio_measures,
    content_hash,
    detect_shots,
    normalize_audio,
    probe_media,
    probe_summary,
    visual_measures,
)
from mp2_storage import sha256_file

FIXTURE = Path("tests/gold/synthetic-av.mp4")

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None,
    reason="ffmpeg/ffprobe not available outside the MP2 image",
)
requires_fixture = pytest.mark.skipif(
    not FIXTURE.is_file(),
    reason="run infra/scripts/mp2.sh fixture to generate tests/gold/synthetic-av.mp4",
)


def _opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


@requires_fixture
def test_source_hash_is_stable() -> None:
    assert sha256_file(FIXTURE) == sha256_file(FIXTURE)


@requires_ffmpeg
@requires_fixture
def test_ffprobe_summary_is_d0_repeatable() -> None:
    first = probe_summary(probe_media(FIXTURE))
    second = probe_summary(probe_media(FIXTURE))
    assert content_hash(first) == content_hash(second)
    assert first["duration_s"] and first["duration_s"] > 0


@requires_ffmpeg
@requires_fixture
def test_audio_normalization_is_byte_identical(tmp_path: Path) -> None:
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    normalize_audio(FIXTURE, a, FFMPEG_NORMALIZE_AUDIO)
    normalize_audio(FIXTURE, b, FFMPEG_NORMALIZE_AUDIO)
    assert sha256_file(a) == sha256_file(b), "PCM normalization must be deterministic"


@requires_fixture
@pytest.mark.skipif(not _opencv_available(), reason="OpenCV not installed")
def test_shot_detection_is_d0_repeatable() -> None:
    first = detect_shots(FIXTURE, PYSCENEDETECT)
    second = detect_shots(FIXTURE, PYSCENEDETECT)
    assert content_hash(first) == content_hash(second)
    assert first, "shot detection must always yield at least one shot"
    for shot in first:
        assert shot["end_ms"] >= shot["start_ms"]


@requires_fixture
@pytest.mark.skipif(not _opencv_available(), reason="OpenCV not installed")
def test_visual_measures_are_d0_repeatable() -> None:
    first = visual_measures(FIXTURE, 0, 2000, OPENCV_VISUAL)
    second = visual_measures(FIXTURE, 0, 2000, OPENCV_VISUAL)
    assert content_hash(first) == content_hash(second)
    assert first["frames_sampled"] > 0


@requires_ffmpeg
@requires_fixture
def test_audio_measures_are_stable_within_d1_tolerance(tmp_path: Path) -> None:
    pytest.importorskip("librosa")
    wav = tmp_path / "audio.wav"
    normalize_audio(FIXTURE, wav, FFMPEG_NORMALIZE_AUDIO)
    first = audio_measures(wav, None, None, LIBROSA_AUDIO)
    second = audio_measures(wav, None, None, LIBROSA_AUDIO)

    # librosa is declared D1: compare numerically within tolerance rather than by hash.
    assert first.keys() == second.keys()
    for key, value in first.items():
        other = second[key]
        if isinstance(value, float):
            assert value == pytest.approx(other, rel=1e-9, abs=1e-12), key
        elif isinstance(value, list):
            assert value == pytest.approx(other, rel=1e-9, abs=1e-12), key
        else:
            assert value == other, key


@requires_ffmpeg
@requires_fixture
def test_extraction_is_reproducible_across_processes(tmp_path: Path) -> None:
    """Guards against hidden in-process caching making a rerun look stable."""
    script = (
        "import json;"
        "from mp2_extractors import probe_media, probe_summary, content_hash;"
        "print(content_hash(probe_summary(probe_media(__import__('pathlib')"
        ".Path('tests/gold/synthetic-av.mp4')))))"
    )
    runs = [
        subprocess.run(["python", "-c", script], capture_output=True, text=True, check=True)
        .stdout.strip()
        for _ in range(2)
    ]
    assert runs[0] == runs[1]
    assert len(runs[0]) == 64
