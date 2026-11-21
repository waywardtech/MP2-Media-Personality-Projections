#!/usr/bin/env bash
set -euo pipefail
out="${1:-tests/gold/synthetic-av.mp4}"
mkdir -p "$(dirname "$out")"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=320x180:rate=24:duration=4" \
  -f lavfi -i "sine=frequency=440:sample_rate=16000:duration=4" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest "$out"
sha256sum "$out" > "$out.sha256"
echo "$out"
