"""Authored subtitle/caption extraction.

When a work ships with a human-authored subtitle track, that track is better evidence than
machine ASR: it was written by someone who knew the material, it disambiguates proper
nouns, and it is already timed. MP2 therefore prefers it and records which source a
transcript came from, so a downstream consumer can tell authored text from a guess.

Parsing SRT is a pure text transform with no model in it, so this extractor is D0: the same
file always yields the same utterances.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import SUBTITLE_SRT, ExtractorSpec

# 00:01:02,500 --> 00:01:05,000   (also tolerates '.' as the millisecond separator)
_CUE_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
# Subtitle files frequently carry inline markup that is presentation, not content.
_TAG = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")


class SubtitleParseError(ValueError):
    """The file is not usable as a subtitle track."""


@dataclass(frozen=True)
class SubtitleTrack:
    utterances: list[dict[str, Any]]
    source_path: str
    encoding: str

    @property
    def text(self) -> str:
        return " ".join(str(u["text"]) for u in self.utterances).strip()


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _clean(line: str) -> str:
    """Strip presentation markup and speaker-position hints, keep the words."""
    line = _TAG.sub("", line)
    # A leading '- ' marks a speaker change in dual-speaker cues; it is not dialogue.
    return line.lstrip("-").strip()


def read_text(path: Path) -> tuple[str, str]:
    """Decode a subtitle file, tolerating the encodings these files arrive in."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    # latin-1 cannot fail, so reaching here means the file is not text at all.
    raise SubtitleParseError(f"{path} is not decodable as text")


def parse_srt(path: Path, spec: ExtractorSpec = SUBTITLE_SRT) -> SubtitleTrack:
    """Parse an SRT file into MP2's utterance shape.

    The output matches the ASR contract (`index`, `start_s`, `end_s`, `text`) so that
    everything downstream - dialogue statistics, scene packets, evidence - is identical
    whether the transcript was authored or machine-generated.
    """
    text, encoding = read_text(path)
    utterances: list[dict[str, Any]] = []

    # Cues are separated by blank lines, but malformed files are common; split on the
    # timing line instead of trusting the block structure.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer, index
        if current is None:
            return
        lines_out = list(buffer)
        # Cues are separated by a blank line and the NEXT cue opens with its number, so the
        # trailing blank lines and lone integer belong to the following cue, not this one.
        # Splitting on timing lines is what makes this necessary, and it is worth it:
        # blank-line block parsing breaks on the malformed files these tracks arrive as.
        while lines_out and not lines_out[-1].strip():
            lines_out.pop()
        if lines_out and lines_out[-1].strip().isdigit():
            lines_out.pop()

        body = " ".join(part for part in (_clean(b) for b in lines_out) if part).strip()
        if body:
            current["text"] = body
            current["index"] = index
            utterances.append(current)
            index += 1
        current, buffer = None, []

    for line in lines:
        match = _CUE_TIME.search(line)
        if match:
            flush()
            start = _seconds(*match.group(1, 2, 3, 4))
            end = _seconds(*match.group(5, 6, 7, 8))
            current = {"start_s": start, "end_s": max(end, start)}
            continue
        if current is None:
            continue
        buffer.append(line)
    flush()

    if not utterances:
        raise SubtitleParseError(f"{path} contained no parsable cues")
    return SubtitleTrack(utterances=utterances, source_path=str(path), encoding=encoding)


def track_summary(track: SubtitleTrack) -> dict[str, Any]:
    """Structural summary of a subtitle track. Carries no dialogue text itself."""
    spans = [float(u["end_s"]) - float(u["start_s"]) for u in track.utterances]
    words = sum(len(str(u["text"]).split()) for u in track.utterances)
    return {
        "utterance_count": len(track.utterances),
        "word_count": words,
        "first_cue_s": float(track.utterances[0]["start_s"]),
        "last_cue_end_s": float(track.utterances[-1]["end_s"]),
        "mean_cue_duration_s": (sum(spans) / len(spans)) if spans else 0.0,
        "encoding": track.encoding,
    }
