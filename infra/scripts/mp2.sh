#!/usr/bin/env bash
# MP2 DEV-01 operations entrypoint. Run inside the Linux reference environment
# (WSL2 Ubuntu 24.04). Every service runs in a container; nothing installs into Windows.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/infra/compose"
# Remember where the caller was: this script cd's away, so any relative path an operator
# passes on the command line must be resolved against their directory, not ours.
INVOCATION_DIR="$PWD"
cd "$COMPOSE_DIR"

resolve_path() {
  # Absolute paths are used as given; a relative path is tried against the caller's
  # directory first, then the repository root.
  case "$1" in
    /*) echo "$1" ;;
    *)
      if [ -e "$INVOCATION_DIR/$1" ]; then
        echo "$INVOCATION_DIR/$1"
      else
        echo "$REPO_ROOT/$1"
      fi
      ;;
  esac
}

# Compose interpolates ${VAR} in compose.yml from ITS OWN directory's .env, not from a
# service's env_file. Without --env-file the repository .env is ignored for interpolation
# and every ${VAR} silently falls back to its default - including POSTGRES_PASSWORD and the
# llama model selection. Always point compose at the real project .env.
dc() {
  if [ -f "$REPO_ROOT/.env" ]; then
    docker compose --env-file "$REPO_ROOT/.env" "$@"
  else
    docker compose "$@"
  fi
}

build_test_image() {
  # Deployable images ship no test runner; the test target adds one on top of the
  # identical runtime layers so the suite exercises what actually ships.
  docker build -q -f "$REPO_ROOT/infra/containers/python.Dockerfile" --target test -t mp2-test:latest "$REPO_ROOT" >/dev/null
}

wait_for_docker() {
  local i
  for i in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "docker daemon did not become ready" >&2
  return 1
}

wait_healthy() {
  # wait_healthy <seconds> <service>...
  local timeout="$1"; shift
  local deadline=$(( $(date +%s) + timeout ))
  local svc status all_ok
  while [ "$(date +%s)" -lt "$deadline" ]; do
    all_ok=1
    for svc in "$@"; do
      status="$(docker inspect "mp2-${svc}-1" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || echo missing)"
      case "$status" in
        healthy|running) ;;
        *) all_ok=0 ;;
      esac
    done
    [ "$all_ok" = 1 ] && return 0
    sleep 3
  done
  echo "timed out waiting for: $*" >&2
  return 1
}

CMD="${1:-help}"
shift || true

case "$CMD" in
  up)
    wait_for_docker
    dc --profile core up -d
    wait_healthy 300 postgres seaweedfs temporal api model-gateway worker-cpu
    dc ps --format 'table {{.Service}}\t{{.Status}}'
    ;;
  # ui/admin services depend on core services, so their profiles are enabled together:
  # Compose rejects a project whose enabled service depends on a disabled one.
  up-ui)     wait_for_docker; dc --profile core --profile ui up -d ;;
  up-gpu)    wait_for_docker; dc --profile gpu up -d ;;
  up-llm)    wait_for_docker; dc --profile llm up -d ;;
  up-ops)    wait_for_docker; dc --profile ops up -d ;;
  up-admin)  wait_for_docker; dc --profile core --profile admin up -d ;;
  stop)      dc --profile core --profile ui --profile gpu --profile llm --profile ops --profile admin stop ;;
  down)      dc --profile core --profile ui --profile gpu --profile llm --profile ops --profile admin down ;;
  restart)   dc restart "$@" ;;
  status)    dc ps -a --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}' ;;
  logs)      dc logs --tail "${2:-80}" "${1:?usage: mp2.sh logs <service> [lines]}" ;;

  migrate)          dc exec -T api alembic upgrade head ;;
  migration-status) dc exec -T api alembic current ;;
  revision)         dc exec -T api alembic revision --autogenerate -m "${1:?message required}" ;;
  psql)             dc exec -T postgres psql -U mp2 -d mp2 "$@" ;;

  test)  build_test_image; docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint python mp2-test:latest -m pytest -q "$@" ;;
  lint)  build_test_image; docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint ruff mp2-test:latest check . ;;

  test-integration)
    # Integration tests need to reach the live API, so the runner joins the app
    # network and addresses the service by name rather than via a published port.
    build_test_image
    docker run --rm --network mp2_app -e MP2_API_URL=http://api:8000 -v "$REPO_ROOT:/repo" -w /repo --entrypoint python mp2-test:latest -m pytest tests/integration -q "$@"
    ;;
  typecheck) build_test_image; docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint mypy mp2-test:latest ;;
  # $0 is unreliable here: the script cd's to the compose directory on startup, so a
  # relative invocation path no longer resolves. Always re-enter by absolute path.
  check)
    self="$REPO_ROOT/infra/scripts/mp2.sh"
    bash "$self" lint && bash "$self" typecheck && bash "$self" test
    ;;

  fixture)
    docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint bash mp2-api:latest tools/corpus/generate_synthetic_fixture.sh tests/gold/synthetic-av.mp4
    cp "$REPO_ROOT/tests/gold/synthetic-av.mp4" "$REPO_ROOT/data/ingest/synthetic-av.mp4"
    echo "fixture ready at data/ingest/synthetic-av.mp4"
    ;;

  bench-fixtures)
    # Synthetic ladder varying duration, resolution and shot count independently.
    docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint python mp2-api:latest tools/benchmark/generate_scaling_fixtures.py data/ingest
    ;;

  speech-fixture)
    bash "$REPO_ROOT/tools/corpus/generate_speech_fixture.sh" tests/gold/synthetic-speech.mp4
    cp "$REPO_ROOT/tests/gold/synthetic-speech.mp4" "$REPO_ROOT/data/ingest/synthetic-speech.mp4"
    echo "fixture ready at data/ingest/synthetic-speech.mp4"
    ;;

  benchmark)
    # Scaling measurement across the fixture ladder. Needs the core stack up and
    # `mp2.sh bench-fixtures` already run.
    docker run --rm --network mp2_app -e MP2_API_URL=http://api:8000 -e MP2_S3_ENDPOINT=http://seaweedfs:8333 -e MP2_S3_ACCESS_KEY="${MP2_S3_ACCESS_KEY:-mp2-dev}" -e MP2_S3_SECRET_KEY="${MP2_S3_SECRET_KEY:-change-me}" -e MP2_S3_REGION=us-east-1 -v "$REPO_ROOT:/repo" -w /repo --entrypoint python mp2-api:latest tools/benchmark/run_scaling_benchmark.py "$@"
    ;;

  objects)
    dc exec -T api python - <<'PY'
import os

import boto3

c = boto3.client("s3", endpoint_url=os.environ["MP2_S3_ENDPOINT"],
                 aws_access_key_id=os.environ["MP2_S3_ACCESS_KEY"],
                 aws_secret_access_key=os.environ["MP2_S3_SECRET_KEY"],
                 region_name=os.environ["MP2_S3_REGION"])
for b in sorted(x["Name"] for x in c.list_buckets()["Buckets"]):
    objs = c.list_objects_v2(Bucket=b).get("Contents", [])
    print(f"{b}: {len(objs)} object(s)")
    for o in objs[:20]:
        print(f"   {o['Size']:>12,}  {o['Key']}")
PY
    ;;

  doctor)
    bash "$REPO_ROOT/infra/scripts/doctor.sh" "$@"
    ;;

  sbom)
    # Works with no external tooling: enumerates what actually shipped in the image.
    docker run --rm -v "$REPO_ROOT:/repo" -w /repo --entrypoint python mp2-api:latest tools/admin/generate_sbom.py --image mp2-api:latest --out build/sbom/mp2-api.json
    ;;

  image-lock)
    bash "$REPO_ROOT/infra/scripts/lock-images.sh"
    ;;

  backup)
    mkdir -p "$REPO_ROOT/data/backup"
    out="$REPO_ROOT/data/backup/mp2-$(date -u +%Y%m%dT%H%M%SZ).dump"
    dc exec -T postgres pg_dump -U mp2 -d mp2 -Fc > "$out"
    echo "wrote $out ($(stat -c%s "$out") bytes)"
    ;;

  restore)
    src="$(resolve_path "${1:?usage: mp2.sh restore <dump-file>}")"
    [ -f "$src" ] || { echo "no such dump file: $src" >&2; exit 1; }
    echo "restoring from $src"
    dc exec -T postgres pg_restore -U mp2 -d mp2 --clean --if-exists < "$src"
    echo "restored from $src"
    ;;

  clean-derived)
    # Disposable intermediates only. Never touches mp2-raw or PostgreSQL.
    dc exec -T api python - <<'PY'
import os

import boto3

c = boto3.client("s3", endpoint_url=os.environ["MP2_S3_ENDPOINT"],
                 aws_access_key_id=os.environ["MP2_S3_ACCESS_KEY"],
                 aws_secret_access_key=os.environ["MP2_S3_SECRET_KEY"],
                 region_name=os.environ["MP2_S3_REGION"])
for b in ("mp2-normalized", "mp2-derived"):
    n = 0
    for o in c.list_objects_v2(Bucket=b).get("Contents", []):
        c.delete_object(Bucket=b, Key=o["Key"])
        n += 1
    print(f"deleted {n} object(s) from {b}")
PY
    ;;

  workflows) dc exec -T temporal temporal workflow list --address temporal:7233 --namespace default ;;
  workflow)  dc exec -T temporal temporal workflow show --address temporal:7233 --namespace default --workflow-id "${1:?usage: mp2.sh workflow <workflow-id>}" ;;
  queues)    dc exec -T temporal temporal task-queue describe --address temporal:7233 --namespace default --task-queue "${1:-mp2-io}" ;;

  local-only)
    echo "gateway policy:"
    dc exec -T model-gateway python -c "import os; print('  MP2_EXTERNAL_MODELS_ENABLED =', os.getenv('MP2_EXTERNAL_MODELS_ENABLED', 'false'))"
    echo "gateway readiness:"
    dc exec -T api python -c "import urllib.request; print(' ', urllib.request.urlopen('http://model-gateway:8080/ready').read().decode())"
    echo "worker egress to a public model provider (expect failure):"
    dc exec -T worker-cpu python -c "
import urllib.request
try:
    urllib.request.urlopen('https://api.openai.com', timeout=5)
    print('  REACHABLE - investigate: compute network should have no egress route')
except Exception as exc:
    print('  blocked:', type(exc).__name__)
"
    ;;

  ports)
    echo "MP2 published host listeners (expect 127.0.0.1 only):"
    dc ps --format '{{.Service}}	{{.Ports}}'
    echo
    echo "any 0.0.0.0 or :: listener below is a G7 finding:"
    ss -ltn 2>/dev/null | awk 'NR>1 {print "  " $4}' | sort -u
    ;;

  help|*)
    cat <<'EOF'
MP2 DEV-01 operations

  Stack      up | up-ui | up-llm | up-gpu | up-ops | up-admin | stop | down | restart | status
             logs <service> [lines]
  Database   migrate | migration-status | revision <msg> | psql [args]
             backup | restore <dump-file>
  Storage    objects | clean-derived
  Supply     sbom | image-lock
  Benchmark  bench-fixtures | benchmark
  Workflows  workflows | workflow <workflow-id> | queues [task-queue]
  Testing    test [pytest args] | test-integration | lint | typecheck | check
             fixture | speech-fixture | bench-fixtures
  Security   local-only | ports
  Health     doctor [--repair]

All service and admin ports bind to 127.0.0.1 only. Only the edge profile is ever
externally exposed. Raw object storage is never reachable from the web tier.
EOF
    ;;
esac
