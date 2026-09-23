#!/usr/bin/env python3
"""Generate a CycloneDX SBOM for an MP2 deployable image, with no external tooling.

`infra/scripts/generate-sbom.sh` uses Syft when it is available and produces a richer
document. This exists so an SBOM can always be produced — on a machine with no Syft, no
network and no package manager beyond what the image already carries, which is exactly the
situation on DEV-01.

It enumerates Python distributions via importlib.metadata and Debian packages via dpkg,
both from inside the image, so it describes what actually shipped rather than what the
manifest intended.

Run inside the image whose SBOM you want:

    docker run --rm -v "$PWD:/repo" -w /repo --entrypoint python mp2-api:latest \\
        tools/admin/generate_sbom.py --image mp2-api:latest --out build/sbom/mp2-api.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Licenses that require review before an image containing them is distributed.
COPYLEFT_HINTS = ("GPL", "AGPL", "LGPL", "MPL", "EUPL", "CDDL", "EPL")


def _short_license(value: str) -> str:
    """Some distributions embed an entire license text in the License field.

    Keep the first meaningful line so the SBOM stays a manifest rather than a corpus of
    license texts. The full text remains in the wheel metadata for anyone who needs it.
    """
    first = next((ln.strip() for ln in (value or "").splitlines() if ln.strip()), "")
    if len(first) > 120:
        first = first[:117] + "..."
    return first or "UNKNOWN"


def python_components() -> list[dict[str, Any]]:
    from importlib.metadata import distributions

    out: list[dict[str, Any]] = []
    for dist in distributions():
        meta = dist.metadata
        name = meta.get("Name")
        if not name:
            continue
        licence = meta.get("License") or ""
        if not licence:
            classifiers = meta.get_all("Classifier") or []
            licence = "; ".join(
                c.split("::")[-1].strip() for c in classifiers if c.startswith("License ::")
            )
        out.append({
            "type": "library",
            "name": name,
            "version": dist.version or "unknown",
            "purl": f"pkg:pypi/{name.lower()}@{dist.version}",
            "licenses": [{"license": {"name": _short_license(licence)}}],
        })
    return sorted(out, key=lambda c: c["name"].lower())


def debian_components() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
            capture_output=True, text=True, check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        name, version = line.split("\t", 1)
        out.append({
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:deb/debian/{name}@{version}",
            "licenses": [{"license": {"name": "UNKNOWN"}}],
        })
    return sorted(out, key=lambda c: c["name"].lower())


def model_components() -> list[dict[str, Any]]:
    """Model weights are supply chain too, and are the easiest thing to forget."""
    out: list[dict[str, Any]] = []
    try:
        from mp2_extractors import asr

        directory = asr.model_directory()
    except Exception:
        directory = None
    if directory is not None:
        size = sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
        out.append({
            "type": "machine-learning-model",
            "name": f"whisper-{directory.name}",
            "version": "ct2",
            "licenses": [{"license": {"name": "MIT"}}],
            "properties": [
                {"name": "mp2:bytes", "value": str(size)},
                {"name": "mp2:path", "value": str(directory)},
                {"name": "mp2:source", "value": "Systran/faster-whisper-tiny"},
            ],
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="unknown", help="image reference being described")
    parser.add_argument("--out", default="build/sbom/mp2.json")
    args = parser.parse_args()

    components = python_components() + debian_components() + model_components()
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
            "tools": [{"vendor": "MP2", "name": "generate_sbom.py", "version": "1"}],
            "component": {"type": "container", "name": args.image, "version": "latest"},
        },
        "components": components,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    flagged = [
        c for c in components
        if any(h in str(c.get("licenses", "")).upper() for h in COPYLEFT_HINTS)
    ]
    def count(prefix: str) -> int:
        return sum(1 for c in components
                   if "purl" in c and str(c["purl"]).startswith(prefix))

    models = sum(1 for c in components if c["type"] == "machine-learning-model")
    print(f"wrote {out}")
    print(f"  python packages : {count('pkg:pypi')}")
    print(f"  debian packages : {count('pkg:deb')}")
    print(f"  model weights   : {models}")
    print(f"  total           : {len(components)}")
    if flagged:
        print(f"\n  {len(flagged)} component(s) declare a copyleft-family license and need "
              f"review before distribution:")
        for c in flagged[:20]:
            name = c["licenses"][0]["license"]["name"]
            print(f"    {c['name']} {c['version']} -> {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
