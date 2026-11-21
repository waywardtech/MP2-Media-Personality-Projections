#!/usr/bin/env bash
set -euo pipefail
compose="infra/compose/compose.yml"
docker compose -f "$compose" --profile core --profile ui --profile gpu --profile ops pull
python3 - <<'PY'
import json, subprocess
ids = subprocess.check_output(["docker", "compose", "-f", "infra/compose/compose.yml", "--profile", "core", "--profile", "ui", "--profile", "gpu", "--profile", "ops", "images", "-q"], text=True).split()
records=[]
for image_id in dict.fromkeys(ids):
    item=json.loads(subprocess.check_output(["docker", "image", "inspect", image_id], text=True))[0]
    records.append({"id": item["Id"], "repoDigests": item.get("RepoDigests", [])})
open("infra/compose/image-lock.json", "w", encoding="utf-8").write(json.dumps(records, indent=2)+"\n")
PY
