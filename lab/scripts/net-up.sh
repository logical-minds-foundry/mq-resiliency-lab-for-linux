#!/usr/bin/env bash
# lab/scripts/net-up.sh — define, start, autostart every lab network.
set -euo pipefail
cd "$(dirname "$0")/../networks"
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-info "$net" >/dev/null 2>&1 \
    || virsh -c qemu:///system net-define "$xml"
  # awk reads all input — avoids the pipefail+grep -q SIGPIPE footgun.
  active=$(virsh -c qemu:///system net-info "$net" | awk '/^Active:/{print $2}')
  [ "$active" = "yes" ] || virsh -c qemu:///system net-start "$net"
  virsh -c qemu:///system net-autostart "$net" >/dev/null
  echo "up: $net"
done
