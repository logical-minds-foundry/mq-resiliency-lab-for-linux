#!/usr/bin/env bash
# lab/scripts/lab-restore.sh - restore a lab snapshot (#218): recreate the captured
# domains from host-durable goldens and start them, WITHOUT re-provisioning. Tears
# down any existing instance of each domain first, so this works after a full lab
# teardown (virsh destroy/undefine) as well as a plain reboot.
# See docs/specs/2026-06-16-lab-snapshot-restore-design.md.
#
# Usage: lab-restore.sh [KEY=distributed-rdqm-rhel]
set -euo pipefail
v() { virsh -c qemu:///system "$@"; }
KEY="${1:-distributed-rdqm-rhel}"

SCRIPT_ABS=$(cd "$(dirname "$0")" && pwd)
case "$SCRIPT_ABS" in
  */.worktrees/*) MAIN_ROOT="${SCRIPT_ABS%%/.worktrees/*}" ;;
  *)              MAIN_ROOT="${SCRIPT_ABS%/lab/scripts}" ;;
esac
SNAP_ROOT="${SNAP_ROOT:-$MAIN_ROOT/build/snapshots}"
SRC="$SNAP_ROOT/$KEY"
echo ">> restore key '$KEY' from $SRC"

fail() { echo "FATAL: $*" >&2; exit 1; }
[ -d "$SRC" ] || fail "no snapshot at $SRC"

for dir in "$SRC"/*/; do
  d=$(basename "$dir")
  [ -f "$dir/domain.xml" ] || { echo ">> skip $d (no domain.xml)"; continue; }
  [ -f "$dir/disks.tsv" ] || fail "$d: missing disks.tsv"
  echo ">> restoring $d"

  # tear down any existing instance (tolerant: may already be gone)
  v destroy "$d" >/dev/null 2>&1 || true
  v undefine "$d" --nvram >/dev/null 2>&1 || v undefine "$d" >/dev/null 2>&1 || true

  # place each golden back at its recorded pool path (disks.tsv: target<TAB>source)
  while IFS=$'\t' read -r target source; do
    [ -n "$target" ] || continue
    echo ">> place $d:$target -> $source"
    sudo cp --reflink=auto "$dir/$target.qcow2" "$source" || fail "copy $d:$target"
  done < "$dir/disks.tsv"

  # rewrite the saved definition for a standalone (flattened) disk, then define+start
  python3 "$SCRIPT_ABS/lab-restore-xml.py" "$dir/domain.xml" >"$dir/restore.xml" \
    || fail "xml rewrite $d"
  v define "$dir/restore.xml" >/dev/null || fail "define $d"
  v start "$d" >/dev/null || fail "start $d"
  echo ">> $d defined + started"
done

echo ">> restore complete for '$KEY' — give the guests a moment, then run e2e-test.sh"
