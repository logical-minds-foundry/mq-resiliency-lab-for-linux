#!/usr/bin/env bash
# lab/scripts/dr-provision.sh — provision the DR-ready Pacemaker arm (both
# sites, DRBD async under the SAN). Reproducible and hands-off: it auto-sources
# the persisted, auto-generated cluster secret (no env var to remember or lose —
# the bug that stalled the first DR build), renders the static inventory from
# topology, then runs the cross-site DR playbook. Idempotent.
#
# Run once all 8 arm nodes are booted: san-a, san-b, pcmk-a1..3, pcmk-b1..3.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PCMK_HACLUSTER_PASSWORD="$("$HERE/lab-secret.sh" pcmk_hacluster_password)"
( cd "$HERE/../.." && mqlab vm inventory )   # renders build/inventory.ini from topology
cd "$HERE/../../ansible"
exec ansible-playbook site-pcmk-dr.yml "$@"
