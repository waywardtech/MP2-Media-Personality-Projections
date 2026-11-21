"""Deterministic media extraction adapters.

These functions are pure with respect to MP2 state: they take a local path plus an
ExtractorSpec and return JSON-serialisable measurements. Persistence, lineage and
retry semantics belong to the orchestrator.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .registry import (
    FFMPEG_NORMALIZE_AUDIO,
    FFPROBE,
    LIBROSA_AUDIO,
    OPENCV_VISUAL,
    PYSCENEDETECT,
    TEXT_STATISTICS,
    ExtractorSpec,
)


def _seconds(timecode: Any) -> float:
    """Seconds from a PySceneDetect timecode across 0.6/0.7 API versions."""
    value = getattr(timecode, "seconds", None)
    if value is not None:
        return float(value)
    return float(timecode.get_seconds())


def _finite(value: Any) -> Any:
    """Replace NaN/inf so measurements stay valid JSON for PostgreSQL."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_finite(item) for item in value]
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    return value


# --- ffprobe ------------------------------------------------------------------------------

def probe_media(path: Path, spec: ExtractorSpec = FFPROBE) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def probe_summary(probe: dict[str, Any]) -> dict[str, Any]:
    """Stable, provider-neutral summary of an ffprobe document."""
    fmt = probe.get("format", {})
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
    duration = fmt.get("duration")
    return _finite({
        "duration_s": float(duration) if duration is not None else None,
        "container": fmt.get("format_name"),
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "video_codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "frame_rate": video.get("avg_frame_rate"),
        "audio_codec": audio.get("codec_name"),
        "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
        "channels": audio.get("channels"),
    })


# --- normalization ------------------------------------------------------------------------

def normalize_audio(source: Path, target: Path,
                    spec: ExtractorSpec = FFMPEG_NORMALIZE_AUDIO) -> dict[str, Any]:
    """Extract a deterministic mono PCM track used by every audio extractor and by ASR."""
    target.parent.mkdir(parents=True, exist_ok=True)
    rate = int(spec.parameters["sample_rate"])
    channels = int(spec.parameters["channels"])
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
         "-vn", "-ac", str(channels), "-ar", str(rate), "-c:a", spec.parameters["codec"],
         str(target)],
        check=True, capture_output=True, text=True,
    )
    return {"sample_rate": rate, "channels": channels, "bytes": target.stat().st_size}


def has_audio_stream(probe: dict[str, Any]) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe.get("streams", []))


# --- shot segmentation --------------------------------------------------------------------

def detect_shots(path: Path, spec: ExtractorSpec = PYSCENEDETECT) -> list[dict[str, Any]]:
    """Return shot boundaries as millisecond ranges with transition candidates."""
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(
        threshold=float(spec.parameters["threshold"]),
        min_scene_len=int(spec.parameters["min_scene_len"]),
    ))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    if not scenes:
        # A single uninterrupted shot is a valid result, not a failure.
        duration_s = _seconds(video.duration) if video.duration else 0.0
        return [{"index": 0, "start_ms": 0, "end_ms": int(round(duration_s * 1000)),
                 "transition_candidate": "none"}]

    shots: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(scenes):
        shots.append({
            "index": index,
            "start_ms": int(round(_seconds(start) * 1000)),
            "end_ms": int(round(_seconds(end) * 1000)),
            # PySceneDetect ContentDetector reports cuts; dissolves are not distinguished.
            "transition_candidate": "cut" if index > 0 else "none",
        })
    return shots


# --- visual measures ----------------------------------------------------------------------

def visual_measures(path: Path, start_ms: int, end_ms: int,
                    spec: ExtractorSpec = OPENCV_VISUAL) -> dict[str, Any]:
    """Sample frames across a segment and reduce them to visual measurements."""
    import cv2
    import numpy as np

    samples = max(1, int(spec.parameters["frames_per_segment"]))
    hue_bins = int(spec.parameters["hue_bins"])
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"OpenCV could not open {path}")
    try:
        span = max(end_ms - start_ms, 1)
        # Deterministic sample positions strictly inside the segment.
        offsets = [start_ms + span * (i + 1) / (samples + 1) for i in range(samples)]
        frames = []
        for offset in offsets:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(offset))
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
        if not frames:
            return {"frames_sampled": 0}

        lum_means, lum_vars, sat_means, edge_densities, histograms = [], [], [], [], []
        for frame in frames:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            lum_means.append(float(gray.mean()))
            lum_vars.append(float(gray.var()))
            sat_means.append(float(hsv[:, :, 1].mean()))
            edge_densities.append(float((cv2.Canny(gray,
                int(spec.parameters["canny_low"]), int(spec.parameters["canny_high"])) > 0).mean()))
            hist = np.histogram(hsv[:, :, 0], bins=hue_bins, range=(0, 180), density=True)[0]
            histograms.append(hist)

        # Frame difference and motion magnitude need consecutive frames.
        frame_diff, motion_magnitude = None, None
        if len(frames) > 1:
            grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
            diffs = [float(np.abs(grays[i + 1].astype(np.int16)
                                  - grays[i].astype(np.int16)).mean())
                     for i in range(len(grays) - 1)]
            frame_diff = float(np.mean(diffs))
            flows = []
            for i in range(len(grays) - 1):
                flow = cv2.calcOpticalFlowFarneback(grays[i], grays[i + 1], None,
                                                    0.5, 3, 15, 3, 5, 1.2, 0)
                flows.append(float(np.linalg.norm(flow, axis=2).mean()))
            motion_magnitude = float(np.mean(flows))

        return _finite({
            "frames_sampled": len(frames),
            "luminance_mean": float(np.mean(lum_means)),
            "luminance_variance": float(np.mean(lum_vars)),
            "saturation_mean": float(np.mean(sat_means)),
            "edge_density": float(np.mean(edge_densities)),
            "spatial_density_proxy": float(np.mean(edge_densities)),
            "hue_histogram": [float(x) for x in np.mean(histograms, axis=0)],
            "frame_difference_mean": frame_diff,
            "motion_magnitude_mean": motion_magnitude,
        })
    finally:
        capture.release()


# --- audio measures -----------------------------------------------------------------------

def audio_measures(wav_path: Path, start_ms: int | None = None, end_ms: int | None = None,
                   spec: ExtractorSpec = LIBROSA_AUDIO) -> dict[str, Any]:
    import librosa
    import numpy as np

    offset = (start_ms or 0) / 1000.0
    duration = None if end_ms is None else max((end_ms - (start_ms or 0)) / 1000.0, 0.0)
    samples, sample_rate = librosa.load(str(wav_path), sr=None, mono=True,
                                        offset=offset, duration=duration)
    if samples.size == 0:
        return {"samples": 0}

    threshold = float(spec.parameters["silence_rms_threshold"])
    rms = librosa.feature.rms(y=samples)[0]
    centroid = librosa.feature.spectral_centroid(y=samples, sr=sample_rate)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=samples, sr=sample_rate)[0]
    rolloff = librosa.feature.spectral_rolloff(y=samples, sr=sample_rate)[0]
    mfcc = librosa.feature.mfcc(y=samples, sr=sample_rate, n_mfcc=int(spec.parameters["n_mfcc"]))
    chroma = librosa.feature.chroma_stft(y=samples, sr=sample_rate)
    onset = librosa.onset.onset_strength(y=samples, sr=sample_rate)
    tempo = librosa.beat.beat_track(y=samples, sr=sample_rate)[0]

    silent = rms < threshold
    # Longest consecutive run of silent frames, reported in frames and seconds.
    longest_run, run = 0, 0
    for value in silent:
        run = run + 1 if value else 0
        longest_run = max(longest_run, run)
    hop_s = 512 / sample_rate
    peak = float(np.max(np.abs(samples)))

    return _finite({
        "samples": int(samples.size),
        "sample_rate": int(sample_rate),
        "duration_s": float(samples.size / sample_rate),
        "rms_mean": float(rms.mean()),
        "rms_max": float(rms.max()),
        "energy_mean": float(np.mean(samples.astype(np.float64) ** 2)),
        "silence_ratio": float(silent.mean()),
        "longest_silence_run_s": float(longest_run * hop_s),
        "spectral_centroid_mean": float(centroid.mean()),
        "spectral_bandwidth_mean": float(bandwidth.mean()),
        "spectral_rolloff_mean": float(rolloff.mean()),
        "mfcc_mean": [float(x) for x in mfcc.mean(axis=1)],
        "chroma_mean": [float(x) for x in chroma.mean(axis=1)],
        "onset_strength_mean": float(onset.mean()),
        "tempo_candidate": float(np.asarray(tempo).reshape(-1)[0]),
        "peak_amplitude": peak,
        "dynamic_range_proxy": float(peak - rms.mean()),
    })


# --- dialogue statistics ------------------------------------------------------------------

def dialogue_statistics(utterances: list[dict[str, Any]], span_s: float,
                        spec: ExtractorSpec = TEXT_STATISTICS) -> dict[str, Any]:
    """Utterance-level statistics. No speaker identity is derived or stored."""
    words = sum(len(str(u.get("text", "")).split()) for u in utterances)
    spoken_s = sum(max(float(u.get("end_s", 0)) - float(u.get("start_s", 0)), 0.0)
                   for u in utterances)
    return _finite({
        "utterance_count": len(utterances),
        "word_count": words,
        "words_per_minute": (words / span_s * 60.0) if span_s > 0 else None,
        "dialogue_density": (spoken_s / span_s) if span_s > 0 else None,
        "mean_utterance_words": (words / len(utterances)) if utterances else 0.0,
        "spoken_duration_s": spoken_s,
    })
