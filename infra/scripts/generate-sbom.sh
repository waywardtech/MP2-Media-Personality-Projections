#!/usr/bin/env bash
set -euo pipefail
command -v syft >/dev/null || { echo "Install Syft before generating SBOMs" >&2; exit 2; }
mkdir -p build/sbom
docker compose -f infra/compose/compose.yml --profile core images -q | sort -u | while read -r image; do
  syft "$image" -o spdx-json="build/sbom/${image#sha256:}.spdx.json"
done
