#!/usr/bin/env bash
# lab/scripts/net-down.sh — tear down every lab network (guests first!).
# Self-echoes each virsh action (run helper) for mqlab's treatment-A transcript.
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }
for xml in net-*.xml; do
  net="${xml%.xml}"
  run virsh -c qemu:///system net-destroy  "$net" 2>/dev/null || true
  run virsh -c qemu:///system net-undefine "$net" 2>/dev/null || true
  echo "down: $net"
done
