#!/usr/bin/env bash
# lab/scripts/net-up.sh [net-name ...] — define, start, autostart the named lab
# networks (or all net-*.xml if none given). Self-echoes each virsh action so
# mqlab's treatment-A transcript shows the literal commands; idempotent.
set -euo pipefail
cd "$(dirname "$0")/../networks"
run() { echo "+ $*"; "$@"; }   # echo verbatim, then run
nets=("$@")
if [ "${#nets[@]}" -eq 0 ]; then
  for xml in net-*.xml; do nets+=("${xml%.xml}"); done
fi
for net in "${nets[@]}"; do
  xml="$net.xml"
  virsh -c qemu:///system net-info "$net" >/dev/null 2>&1 \
    || run virsh -c qemu:///system net-define "$xml"
  # awk reads all input — avoids the pipefail+grep -q SIGPIPE footgun.
  active=$(virsh -c qemu:///system net-info "$net" | awk '/^Active:/{print $2}')
  [ "$active" = "yes" ] || run virsh -c qemu:///system net-start "$net"
  run virsh -c qemu:///system net-autostart "$net"
  echo "up: $net"
done
