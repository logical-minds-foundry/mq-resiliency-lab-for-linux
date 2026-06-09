#!/usr/bin/env bash
# lab/scripts/net-up.sh — define, start, autostart every lab network.
# Self-echoes each virsh action (run helper) so mqlab's treatment-A transcript
# shows the literal commands; idempotent (skip already-defined/active).
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }   # echo verbatim, then run
for xml in net-*.xml; do
  net="${xml%.xml}"
  virsh -c qemu:///system net-info "$net" >/dev/null 2>&1 \
    || run virsh -c qemu:///system net-define "$xml"
  # awk reads all input — avoids the pipefail+grep -q SIGPIPE footgun.
  active=$(virsh -c qemu:///system net-info "$net" | awk '/^Active:/{print $2}')
  [ "$active" = "yes" ] || run virsh -c qemu:///system net-start "$net"
  run virsh -c qemu:///system net-autostart "$net"
  echo "up: $net"
done
