#!/usr/bin/env bash
# Generate a small synthetic audiovisual fixture that contains INTELLIGIBLE SPEECH, so the
# ASR path produces real utterances instead of an empty-but-passing result.
#
# Two stages, deliberately split:
#   1. espeak-ng in a throwaway debian container synthesises speech to WAV.
#   2. the MP2 image (which already ships ffmpeg) muxes the audiovisual fixture.
# Installing ffmpeg into the throwaway container would pull ~170 packages and take far
# longer than the rest of this script put together.
#
# Licensing note: espeak-ng is GPL-3.0. It is never installed into an MP2 deployable image,
# and the generated audio is program output, not a derivative of the synthesiser. The
# resulting fixture is MP2-owned test data. See docs/implementation/LICENSES_AND_MODELS.md.
#
# Usage: generate_speech_fixture.sh [output.mp4]
set -euo pipefail

out="${1:-tests/gold/synthetic-speech.mp4}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="$repo_root/.fixture-work"
mkdir -p "$repo_root/$(dirname "$out")" "$work"
trap 'rm -rf "$work"' EXIT

LINE1="The quick brown fox jumps over the lazy dog."
LINE2="Media personality projection analyses scenes, not people."

echo "[1/2] synthesising speech with espeak-ng..."
docker run --rm -v "$work:/work" debian:bookworm-slim bash -euc "
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends espeak-ng >/dev/null
  espeak-ng -v en-us -s 145 -w /work/line1.wav '$LINE1'
  espeak-ng -v en-us -s 145 -w /work/line2.wav '$LINE2'
  chmod 0666 /work/*.wav
"

echo "[2/2] muxing fixture with ffmpeg (MP2 image)..."
docker run --rm -v "$repo_root:/repo" -v "$work:/work" -w /repo \
  --entrypoint bash mp2-api:latest -euc "
  # A beat of silence between the two lines so utterance segmentation is testable.
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i anullsrc=r=22050:cl=mono -t 0.6 /work/gap.wav
  ffmpeg -hide_banner -loglevel error -y -i /work/line1.wav -i /work/gap.wav -i /work/line2.wav \
    -filter_complex '[0:a][1:a][2:a]concat=n=3:v=0:a=1[out]' -map '[out]' \
    -ar 16000 -ac 1 /work/speech.wav

  dur=\$(ffprobe -v error -show_entries format=duration -of csv=p=0 /work/speech.wav)
  half=\$(python3 -c \"print(f'{float('\$dur')/2:.3f}')\")

  # Two visually distinct halves so PySceneDetect reports a genuine cut.
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i \"testsrc2=size=320x180:rate=24:duration=\$half\" \
    -f lavfi -i \"smptebars=size=320x180:rate=24:duration=\$half\" \
    -i /work/speech.wav \
    -filter_complex '[0:v][1:v]concat=n=2:v=1:a=0[v]' \
    -map '[v]' -map 2:a -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest '/repo/$out'
"

sha256sum "$repo_root/$out" | sed "s#$repo_root/##" > "$repo_root/$out.sha256"
echo "$out"
