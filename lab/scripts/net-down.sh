#!/usr/bin/env bash
# lab/scripts/net-down.sh — tear down every lab network (guests first!).
set -euo pipefail
cd "$(dirname "$0")/../networks"
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-destroy  "$net" 2>/dev/null || true
  virsh -c qemu:///system net-undefine "$net" 2>/dev/null || true
  echo "down: $net"
done
