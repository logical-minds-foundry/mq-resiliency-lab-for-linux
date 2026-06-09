#!/usr/bin/env bash
# lab/scripts/net-down.sh [net-name ...] — destroy + undefine the named lab
# networks (or all net-*.xml if none given; guests first!). Self-echoes actions.
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }
nets=("$@")
if [ "${#nets[@]}" -eq 0 ]; then
  for xml in net-*.xml; do nets+=("${xml%.xml}"); done
fi
for net in "${nets[@]}"; do
  run virsh -c qemu:///system net-destroy  "$net" 2>/dev/null || true
  run virsh -c qemu:///system net-undefine "$net" 2>/dev/null || true
  echo "down: $net"
done
