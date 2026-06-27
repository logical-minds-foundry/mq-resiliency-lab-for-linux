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
( cd "$HERE/../.." && mqlab vm inventory )   # renders build/work/inventory.ini from topology
cd "$HERE/../../ansible"
# #350 Task 2: the DR-ready Pacemaker provision is now the consolidated full-HADR
# site-pcmk.yml (was site-pcmk-dr.yml). It also creates the QM, so it needs the
# #351 QM extra-vars — the canonical path is `mqlab bootstrap pcmk-ubuntu`, which
# passes them via phases._qm_extra_vars. This legacy wrapper requires them on the
# command line, e.g.:
#   dr-provision.sh -e qm_app=PCMKAPP -e qm_svc=PCMKSVC \
#                   -e chl_to_svc=PCMKAPP.PCMKSVC -e chl_to_app=PCMKSVC.PCMKAPP
exec ansible-playbook site-pcmk.yml "$@"
