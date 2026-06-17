#!/usr/bin/env bash
# lab/scripts/rdqm-dr-force.sh - FORCED cross-site cutover (#233): site A is GONE (hard
# power-off), so we cannot and need not demote it — RDQM's split-brain guard passes
# because the replication link is down. We promote site B. The caller powers site A
# off first (lab: `virsh destroy lab_rdqm-a{1,2,3}` — libvirt force-OFF; the domains
# and disks PERSIST, never undefine). Failback = power site A on, then
# rdqm-dr-cutover.sh b2a.
# Usage: rdqm-dr-force.sh [QM=QMRDQM] [TO=rdqm-b1]
set -euo pipefail
QM="${1:-QMRDQM}"
TO="${2:-rdqm-b1}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
run "$TO" "/opt/mqm/bin/rdqmdr -m $QM -p"        # force-promote the recovery site
run "$TO" "su mqm -c '/opt/mqm/bin/strmqm $QM'"  # start the QM on the new primary
# Fail loud: complete only if the QM is actually Running on the recovery site.
if run "$TO" "/opt/mqm/bin/dspmq -m $QM -o status" | grep -q "STATUS(Running)"; then
  echo "=== forced cutover complete; $QM Running on $TO ==="
else
  echo "ERROR: forced cutover did NOT bring $QM up on $TO" >&2
  exit 1
fi
