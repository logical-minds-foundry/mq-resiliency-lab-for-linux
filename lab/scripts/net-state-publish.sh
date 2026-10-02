#!/usr/bin/env bash
# lab/scripts/net-state-publish.sh — publish lab_network_state to the host
# node_exporter textfile drop zone (#1253).
#
# Lab networks are virtual: their state only changes when WE change it (net-up.sh /
# net-down.sh, i.e. bring-up and partition drills). So instead of a probe polling
# virsh every 10s as root (the retired lab-net-state service), the scripts that
# change state call this right after, and the observe phase calls it once after it
# creates the drop zone.
#
# Rendering is the tested `mqlab obs net-state` (every declared net, absent ones as
# an explicit 0), invoked the conventional way — `uv run`, as the lab user. Only the
# final install into the root-owned drop zone uses sudo: this is a lab-simulation
# tool, not a production monitoring path. The file lands atomically (.tmp + mv) so
# node_exporter never scrapes a half-written file.
#
# If the drop zone does not exist yet (a fresh host's bring-up runs net-up.sh before
# the observe phase installs node_exporter), it says so and exits 0; observe
# publishes once it has created the zone.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DROP="${MQLAB_TEXTFILE_DIR:-/var/lib/node_exporter/textfile}"
OUT="$DROP/lab_network_state.prom"

if [ ! -d "$DROP" ]; then
  echo "net-state: $DROP not present yet (host node_exporter not installed) — not published; the observe phase publishes it"
  exit 0
fi

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo -n"

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
uv run --quiet --project "$REPO" mqlab obs net-state > "$rendered"
$SUDO install -m 0644 "$rendered" "$OUT.tmp"
$SUDO mv -f "$OUT.tmp" "$OUT"
echo "net-state: published $(grep -c '^lab_network_state{' "$rendered") net(s) -> $OUT"
