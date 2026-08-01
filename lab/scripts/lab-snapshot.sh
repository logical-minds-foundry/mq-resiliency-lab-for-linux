#!/usr/bin/env bash
# lab/scripts/lab-snapshot.sh - capture the BUILT + PROVISIONED state of running lab
# domains as host-durable, standalone qcow2 goldens, so the lab can be restored
# without re-running the lengthy RHEL build + provision (#218). (The ~2h figure was the
# macOS-era TCG-emulated build; on an x86 host these RHEL arms build under native KVM and
# are faster — the build cost is host-dependent, not fixed at TCG timings.)
#
# Goldens are written under the MAIN worktree's host-mounted build/state/snapshots/ (durable
# across an ephemeral Vergil-VM rebuild, unlike /var/lib/libvirt/images). The domains
# are cleanly shut down for a consistent capture, then restarted (the lab is left
# running). See docs/specs/2026-06-16-lab-snapshot-restore-design.md.
#
# Usage: lab-snapshot.sh [KEY=distributed-rdqm-rhel] [domain ...]
#   default domains = the RDQM distributed set (rdqm-a1/2/3, svc-sim, app-client)
set -euo pipefail
v() { virsh -c qemu:///system "$@"; }
KEY="${1:-distributed-rdqm-rhel}"
shift || true
DOMAINS=("$@")
if [ "${#DOMAINS[@]}" -eq 0 ]; then
  DOMAINS=(lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3 lab_svc-sim lab_app-client)
fi

SCRIPT_ABS=$(cd "$(dirname "$0")" && pwd)
case "$SCRIPT_ABS" in
  */.worktrees/*) MAIN_ROOT="${SCRIPT_ABS%%/.worktrees/*}" ;;
  *)              MAIN_ROOT="${SCRIPT_ABS%/lab/scripts}" ;;
esac
SNAP_ROOT="${SNAP_ROOT:-$MAIN_ROOT/build/state/snapshots}"
DEST="$SNAP_ROOT/$KEY"
echo ">> snapshot key '$KEY' -> $DEST"
echo ">> domains: ${DOMAINS[*]}"

fail() { echo "FATAL: $*" >&2; exit 1; }

# 1) clean shutdown (releases the qcow2 write lock; flushes FS / MQ logs / DRBD)
for d in "${DOMAINS[@]}"; do
  v dominfo "$d" >/dev/null 2>&1 || fail "no such domain: $d"
  state=$(v domstate "$d" 2>/dev/null || echo unknown)
  if [ "$state" = "running" ]; then
    echo ">> shutting down $d"
    v shutdown "$d" >/dev/null || true
  fi
done
for d in "${DOMAINS[@]}"; do
  for _ in $(seq 1 120); do
    state=$(v domstate "$d" 2>/dev/null || echo unknown)
    [ "$state" = "shut off" ] && break
    sleep 2
  done
  [ "$state" = "shut off" ] || fail "$d did not reach 'shut off' (state=$state)"
  echo ">> $d is shut off"
done

# 2) per-domain: save redefine-able XML + flatten each disk volume to a golden
mkdir -p "$DEST"
for d in "${DOMAINS[@]}"; do
  out="$DEST/$d"
  mkdir -p "$out"
  v dumpxml --inactive "$d" >"$out/domain.xml" || fail "dumpxml $d"
  # domblklist --details columns: Type Device Target Source ; capture device==disk
  manifest="$out/disks.tsv"
  : >"$manifest"
  while read -r _type device target source; do
    [ "$device" = "disk" ] || continue
    [ -n "$source" ] || continue
    echo ">> flatten $d:$target ($source)"
    sudo qemu-img convert -O qcow2 "$source" "$out/$target.qcow2" || fail "convert $d:$target"
    sudo chown "$(id -u):$(id -g)" "$out/$target.qcow2"
    printf '%s\t%s\n' "$target" "$source" >>"$manifest"
  done < <(v domblklist --details "$d" | tail -n +3)
done

# 3) restart the captured domains (leave the lab running)
for d in "${DOMAINS[@]}"; do
  echo ">> starting $d"
  v start "$d" >/dev/null || fail "start $d"
done

echo ">> snapshot complete: $DEST"
du -sh "$DEST"/* 2>/dev/null || true
