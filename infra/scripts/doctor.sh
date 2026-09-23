#!/usr/bin/env bash
# MP2 environment health check and corruption scan.
#
# Written after a hard failure mode on DEV-01: Docker's content store was corrupted by a
# disk-full event, and the damage stayed invisible until a layer was next extracted
# ("unpigz: corrupted -- crc32 mismatch"). A force-quit of Docker Desktop or an abrupt WSL
# shutdown can do the same. This surfaces that class of damage on demand instead of
# discovering it mid-build.
#
# Read-only by default. Pass --repair to act on what it finds.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPAIR=0
[ "${1:-}" = "--repair" ] && REPAIR=1

FINDINGS=0
note()  { printf '  %s\n' "$*"; }
ok()    { printf '  [ ok ] %s\n' "$*"; }
warn()  { printf '  [warn] %s\n' "$*"; FINDINGS=$((FINDINGS+1)); }
fail()  { printf '  [FAIL] %s\n' "$*"; FINDINGS=$((FINDINGS+1)); }
head_() { printf '\n== %s ==\n' "$*"; }

head_ "WSL resource policy"
MEM_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
MEM_GB=$(( MEM_KB / 1024 / 1024 ))
CPUS=$(nproc)
SWAP_KB=$(awk '/SwapTotal/{print $2}' /proc/meminfo)
note "memory ~${MEM_GB} GiB, ${CPUS} cpus, swap ~$(( SWAP_KB / 1024 / 1024 )) GiB"
if [ "$MEM_GB" -ge 10 ] && [ "$CPUS" -le 6 ]; then
  ok ".wslconfig appears applied (bounded memory and cpus)"
else
  warn ".wslconfig may not be applied; run 'wsl --shutdown' from Windows and retry"
fi

head_ "systemd and docker daemon"
SYSD=$(systemctl is-system-running 2>&1 || true)
note "systemd: $SYSD"
for _ in $(seq 1 60); do
  [ "$(systemctl is-active docker 2>&1)" = active ] && break
  sleep 5
done
if [ "$(systemctl is-active docker 2>&1)" = active ]; then
  ok "docker.service active"
else
  fail "docker.service is not active"
  [ "$REPAIR" = 1 ] && sudo systemctl start docker 2>&1 | tail -2
fi

# The daemon cycling is what silently killed long-running jobs on this machine. Count only
# stops SINCE the current daemon start: journald survives a WSL shutdown, so counting a
# fixed window reports the damage from before a fix was applied and cries wolf.
SINCE=$(systemctl show docker --property=ActiveEnterTimestamp --value 2>/dev/null)
if [ -n "$SINCE" ]; then
  RESTARTS=$(journalctl --since "$SINCE" --no-pager 2>/dev/null \
             | grep -c "Stopping docker.service" || true)
  UP_FOR=$(( $(date +%s) - $(date -d "$SINCE" +%s 2>/dev/null || date +%s) ))
  if [ "${RESTARTS:-0}" -gt 0 ]; then
    fail "docker.service stopped ${RESTARTS}x since it last started - jobs will be killed mid-run"
    note "       usual cause: the distro is torn down between commands, or Docker Desktop"
    note "       is managing this distro. MP2 does not need Docker Desktop."
    note "       fix: 'wsl --shutdown' from Windows so .wslconfig vmIdleTimeout applies."
  elif [ "$UP_FOR" -lt 300 ]; then
    note "docker.service up ${UP_FOR}s with no stops - too early to call it stable"
  else
    ok "docker.service stable for $(( UP_FOR / 60 )) min with no stops"
  fi
else
  warn "could not determine docker.service start time"
fi

head_ "storage headroom"
df -h / /mnt/d 2>/dev/null | grep -Ev '^Filesystem' | while read -r line; do note "$line"; done
AVAIL_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "${AVAIL_GB:-0}" -lt 20 ]; then
  fail "only ${AVAIL_GB} GiB free on / - a disk-full event is what corrupted the image store before"
else
  ok "${AVAIL_GB} GiB free on /"
fi

head_ "docker image integrity"
# There is no docker fsck. Extracting a layer is the only reliable way to surface the
# crc32 damage a truncated write leaves behind, so actually run each image.
for image in mp2-api:latest mp2-worker-cpu:latest mp2-model-gateway:latest; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    warn "$image not present (build with: docker compose --profile core build)"
    continue
  fi
  if out=$(docker run --rm --entrypoint true "$image" 2>&1); then
    ok "$image extracts and runs"
  else
    fail "$image is damaged: $(echo "$out" | tail -1)"
    if [ "$REPAIR" = 1 ]; then
      note "       removing so it is rebuilt from scratch"
      docker rmi -f "$image" >/dev/null 2>&1 || true
    fi
  fi
done

for image in postgres pgvector/pgvector:pg18 temporalio/auto-setup:1.28 chrislusf/seaweedfs:3.80; do
  docker image inspect "$image" >/dev/null 2>&1 || continue
  if docker run --rm --entrypoint true "$image" >/dev/null 2>&1; then
    ok "$image extracts"
  else
    fail "$image is damaged - re-pull with: docker pull $image"
    [ "$REPAIR" = 1 ] && docker rmi -f "$image" >/dev/null 2>&1 && docker pull "$image" 2>&1 | tail -1
  fi
done

head_ "named volumes"
for volume in mp2_postgres-data mp2_seaweed-data; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    ok "$volume present"
  else
    warn "$volume missing - data from previous runs is gone"
  fi
done

head_ "dangling state"
STOPPED=$(docker ps -aq --filter status=exited | wc -l)
[ "$STOPPED" -gt 0 ] && note "$STOPPED exited container(s)" || note "no exited containers"
CACHE=$(docker system df --format '{{.Type}} {{.Reclaimable}}' 2>/dev/null | grep -i "build cache" || true)
[ -n "$CACHE" ] && note "build cache reclaimable: ${CACHE#*Cache }"

head_ "summary"
if [ "$FINDINGS" -eq 0 ]; then
  echo "  no findings - environment looks healthy"
else
  echo "  $FINDINGS finding(s) above"
  [ "$REPAIR" = 0 ] && echo "  re-run with --repair to act on them"
fi
exit 0
