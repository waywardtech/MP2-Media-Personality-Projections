"""Local ASR adapter (faster-whisper / CTranslate2).

MP2 owns the transcript contract; faster-whisper is a replaceable instrument behind it.
No speaker identity, diarisation or voiceprint is produced or stored.

The model is resolved from a local directory only. Nothing is downloaded at runtime, so
the adapter works unchanged in local-only mode. If no weights are present the adapter
reports itself unavailable instead of failing the analysis run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import FASTER_WHISPER, ExtractorSpec


class AsrUnavailable(RuntimeError):
    """Raised when no local ASR weights are installed."""


@dataclass(frozen=True)
class AsrResult:
    language: str | None
    language_probability: float | None
    duration_s: float
    utterances: list[dict[str, Any]]
    model_directory: str
    metadata: dict[str, Any]

    @property
    def text(self) -> str:
        return " ".join(str(u["text"]).strip() for u in self.utterances).strip()


def model_directory() -> Path | None:
    """Local weights directory, or None when ASR is not installed."""
    configured = os.getenv("MP2_WHISPER_MODEL_DIR", "/opt/mp2/models/whisper")
    path = Path(configured)
    return path if path.is_dir() and any(path.iterdir()) else None


def is_available() -> bool:
    if model_directory() is None:
        return False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(wav_path: Path, spec: ExtractorSpec = FASTER_WHISPER,
               word_timestamps: bool = True) -> AsrResult:
    directory = model_directory()
    if directory is None:
        raise AsrUnavailable(
            "no local Whisper weights at MP2_WHISPER_MODEL_DIR; ASR is disabled"
        )
    from faster_whisper import WhisperModel

    model = WhisperModel(str(directory), device="cpu",
                         compute_type=str(spec.parameters.get("compute_type", "int8")))
    segments, info = model.transcribe(
        str(wav_path),
        beam_size=int(spec.parameters.get("beam_size", 1)),
        temperature=float(spec.parameters.get("temperature", 0.0)),
        vad_filter=bool(spec.parameters.get("vad_filter", False)),
        word_timestamps=word_timestamps,
    )

    utterances: list[dict[str, Any]] = []
    for segment in segments:
        entry: dict[str, Any] = {
            "index": int(segment.id),
            "start_s": float(segment.start),
            "end_s": float(segment.end),
            "text": str(segment.text).strip(),
            "avg_logprob": float(segment.avg_logprob),
            "no_speech_probability": float(segment.no_speech_prob),
        }
        if word_timestamps and segment.words:
            entry["words"] = [
                {"word": str(w.word).strip(), "start_s": float(w.start),
                 "end_s": float(w.end), "probability": float(w.probability)}
                for w in segment.words
            ]
        utterances.append(entry)

    return AsrResult(
        language=getattr(info, "language", None),
        language_probability=(float(info.language_probability)
                              if getattr(info, "language_probability", None) is not None else None),
        duration_s=float(getattr(info, "duration", 0.0)),
        utterances=utterances,
        model_directory=str(directory),
        metadata={
            "model_name": directory.name,
            "beam_size": spec.parameters.get("beam_size"),
            "temperature": spec.parameters.get("temperature"),
            "vad_filter": spec.parameters.get("vad_filter"),
            "compute_type": spec.parameters.get("compute_type"),
            "word_timestamps": word_timestamps,
            "speaker_identity": "not-derived",
        },
    )
