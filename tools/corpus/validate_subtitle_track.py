"""Validate the subtitle parser against the real CFWMN track.

Reports structure only. The dialogue itself is the owner's copyrighted work and is not
printed here; MP2 stores it as evidence, it does not need to be echoed to an operator.
"""
from pathlib import Path

from mp2_extractors import SUBTITLE_SRT, content_hash, subtitles

path = Path("/src/SUBTITLES/audio.srt")
track = subtitles.parse_srt(path, SUBTITLE_SRT)
summary = subtitles.track_summary(track)

print("extractor       :", SUBTITLE_SRT.identity, f"[{SUBTITLE_SRT.repeatability_class}]")
print("encoding        :", track.encoding)
print("utterances      :", summary["utterance_count"])
print("words           :", summary["word_count"])
print(f"first cue       : {summary['first_cue_s']:.2f}s")
print(f"last cue end    : {summary['last_cue_end_s']:.2f}s "
      f"({summary['last_cue_end_s'] / 60:.1f} min)")
print(f"mean cue length : {summary['mean_cue_duration_s']:.2f}s")

# Integrity checks against a real file rather than a fixture.
bad_order = [u for u in track.utterances if u["end_s"] < u["start_s"]]
overlaps = sum(1 for a, b in zip(track.utterances, track.utterances[1:], strict=False)
               if b["start_s"] < a["end_s"])
empty = [u for u in track.utterances if not str(u["text"]).strip()]
numeric = [u for u in track.utterances if str(u["text"]).strip().isdigit()]

print()
print("inverted cues       :", len(bad_order))
print("overlapping cues    :", overlaps, "(normal in dual-speaker subtitling)")
print("empty utterances    :", len(empty))
print("numeric-only bodies :", len(numeric), "(should be 0 - would mean cue numbers leaked in)")
print("D0 rerun stable     :", content_hash(subtitles.parse_srt(path).utterances)
      == content_hash(track.utterances))

longest = max(track.utterances, key=lambda u: len(str(u["text"])))
print("longest cue chars   :", len(str(longest["text"])))
