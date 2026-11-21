#!/usr/bin/env python3
"""Generate a ladder of synthetic fixtures for scaling measurements.

Analysis cost has several independent drivers — duration, resolution and shot count — and
a single short clip cannot separate them. This builds fixtures that vary one at a time so
per-driver cost can be attributed rather than guessed.

Everything is synthesised with ffmpeg lavfi sources. No third-party media is involved, and
nothing here is redistributed.

Run inside the MP2 image (it needs ffmpeg):

    docker run --rm -v "$PWD:/repo" -w /repo --entrypoint python mp2-api:latest \\
        tools/benchmark/generate_scaling_fixtures.py data/ingest
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Spec:
    name: str
    seconds: int
    width: int
    height: int
    shots: int

    @property
    def filename(self) -> str:
        return f"{self.name}.mp4"


# Chosen so each pair isolates one variable against bench-60s-360p-12 as the reference:
#   30s vs 60s      -> duration
#   360p vs 720p    -> resolution
#   12 vs 60 shots  -> shot count / segment fan-out
SPECS = [
    Spec("bench-30s-360p-06", 30, 640, 360, 6),
    Spec("bench-60s-360p-12", 60, 640, 360, 12),
    Spec("bench-60s-720p-12", 60, 1280, 720, 12),
    Spec("bench-300s-360p-60", 300, 640, 360, 60),
]


def build(spec: Spec, outdir: Path) -> Path:
    target = outdir / spec.filename
    if target.is_file():
        print(f"exists: {target} ({target.stat().st_size:,} bytes)")
        return target

    shot_len = spec.seconds / spec.shots
    args: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]

    # Alternate two visually distinct sources so PySceneDetect sees a genuine cut at every
    # boundary; otherwise shot count is not actually being varied.
    for index in range(spec.shots):
        source = "testsrc2" if index % 2 == 0 else "smptebars"
        args += ["-f", "lavfi",
                 "-i", f"{source}=size={spec.width}x{spec.height}:rate=24:duration={shot_len}"]

    # A tone bed so the audio extractors and ASR have real signal to process.
    args += ["-f", "lavfi",
             "-i", f"sine=frequency=330:sample_rate=16000:duration={spec.seconds}"]

    concat_inputs = "".join(f"[{i}:v]" for i in range(spec.shots))
    args += [
        "-filter_complex", f"{concat_inputs}concat=n={spec.shots}:v=1:a=0[v]",
        "-map", "[v]", "-map", f"{spec.shots}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(target),
    ]

    subprocess.run(args, check=True, capture_output=True, text=True)
    try:
        target.chmod(0o666)
    except OSError:
        # A 9p/DrvFs bind mount refuses chmod from the container user. The file is written
        # and readable, which is all that matters; permissions come from the mount.
        pass
    print(f"built:  {target} ({target.stat().st_size:,} bytes) "
          f"{spec.seconds}s {spec.width}x{spec.height} {spec.shots} shots")
    return target


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/ingest")
    outdir.mkdir(parents=True, exist_ok=True)
    for spec in SPECS:
        try:
            build(spec, outdir)
        except subprocess.CalledProcessError as exc:
            print(f"FAILED {spec.name}: {exc.stderr[-400:]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
