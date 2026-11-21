"""Subtitle parsing contract.

An authored subtitle track is preferred over machine ASR, so its parser has to be at least
as trustworthy as the ASR path it displaces: deterministic, tolerant of the encodings and
malformations these files actually arrive in, and producing exactly the ASR utterance
shape so nothing downstream can tell the difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mp2_extractors import SUBTITLE_SRT, content_hash, subtitles

BASIC = (
    "1\n"
    "00:00:01,000 --> 00:00:03,500\n"
    "First line of dialogue.\n"
    "\n"
    "2\n"
    "00:00:04,000 --> 00:00:06,000\n"
    "Second line,\n"
    "continued across a wrap.\n"
    "\n"
)


def _write(tmp_path: Path, text: str, encoding: str = "utf-8") -> Path:
    path = tmp_path / "track.srt"
    path.write_bytes(text.encode(encoding))
    return path


def test_parses_cues_into_the_asr_utterance_shape(tmp_path: Path) -> None:
    track = subtitles.parse_srt(_write(tmp_path, BASIC))

    assert len(track.utterances) == 2
    for index, utterance in enumerate(track.utterances):
        # Exactly the keys the ASR path emits, so scene packets are source-agnostic.
        assert set(utterance) >= {"index", "start_s", "end_s", "text"}
        assert utterance["index"] == index
        assert utterance["end_s"] >= utterance["start_s"]

    assert track.utterances[0]["start_s"] == pytest.approx(1.0)
    assert track.utterances[0]["end_s"] == pytest.approx(3.5)
    # A wrapped cue is one utterance, not two.
    assert "continued across a wrap" in track.utterances[1]["text"]


def test_parsing_is_d0_repeatable(tmp_path: Path) -> None:
    path = _write(tmp_path, BASIC)
    first = subtitles.parse_srt(path).utterances
    second = subtitles.parse_srt(path).utterances
    assert content_hash(first) == content_hash(second)


def test_strips_presentation_markup_but_keeps_words(tmp_path: Path) -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n"
        "<i>Italic</i> and <font color=\"#fff\">coloured</font> text\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n"
        "{\\an8}Positioned text\n\n"
    )
    track = subtitles.parse_srt(_write(tmp_path, text))
    assert track.utterances[0]["text"] == "Italic and coloured text"
    assert track.utterances[1]["text"] == "Positioned text"


def test_speaker_dashes_are_not_treated_as_content(tmp_path: Path) -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\n- One speaker\n- Another speaker\n\n"
    track = subtitles.parse_srt(_write(tmp_path, text))
    assert track.utterances[0]["text"] == "One speaker Another speaker"


def test_accepts_dot_millisecond_separator(tmp_path: Path) -> None:
    text = "1\n00:00:01.250 --> 00:00:02.750\nDialogue\n\n"
    track = subtitles.parse_srt(_write(tmp_path, text))
    assert track.utterances[0]["start_s"] == pytest.approx(1.25)
    assert track.utterances[0]["end_s"] == pytest.approx(2.75)


def test_decodes_non_utf8_files(tmp_path: Path) -> None:
    # Subtitle files routinely arrive as cp1252 from older authoring tools.
    text = "1\n00:00:01,000 --> 00:00:02,000\nCafé naïve\n\n"
    track = subtitles.parse_srt(_write(tmp_path, text, encoding="cp1252"))
    assert "Caf" in track.utterances[0]["text"]
    assert track.encoding in {"utf-8", "cp1252", "latin-1"}


def test_utf8_bom_is_not_treated_as_content(tmp_path: Path) -> None:
    track = subtitles.parse_srt(_write(tmp_path, BASIC, encoding="utf-8-sig"))
    assert track.utterances[0]["text"].startswith("First")


def test_blank_cues_are_dropped(tmp_path: Path) -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nReal dialogue\n\n"
    )
    track = subtitles.parse_srt(_write(tmp_path, text))
    assert len(track.utterances) == 1
    assert track.utterances[0]["index"] == 0, "indices must stay contiguous after drops"


def test_a_file_with_no_cues_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(subtitles.SubtitleParseError):
        subtitles.parse_srt(_write(tmp_path, "not a subtitle file at all\n"))


def test_summary_describes_structure_without_carrying_dialogue(tmp_path: Path) -> None:
    track = subtitles.parse_srt(_write(tmp_path, BASIC))
    summary = subtitles.track_summary(track)

    assert summary["utterance_count"] == 2
    assert summary["word_count"] > 0
    assert summary["first_cue_s"] == pytest.approx(1.0)
    assert summary["last_cue_end_s"] == pytest.approx(6.0)
    # The summary is metadata about the track; it must not embed the dialogue itself.
    serialised = str(summary)
    assert "First line of dialogue" not in serialised


def test_registered_as_a_deterministic_extractor() -> None:
    assert SUBTITLE_SRT.repeatability_class == "D0"
    # Same output contract as ASR, so downstream consumers are source-agnostic.
    assert SUBTITLE_SRT.output_schema_version == "asr-transcript/1"
    assert SUBTITLE_SRT.license_record != "unrecorded"
