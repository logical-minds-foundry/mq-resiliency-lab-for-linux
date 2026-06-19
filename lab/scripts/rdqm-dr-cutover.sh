#!/usr/bin/env bash
# lab/scripts/rdqm-dr-cutover.sh - controlled cross-site cutover/failback for the RDQM arm.
# RDQM wraps the work in two rdqmdr commands (vs the Pacemaker arm's multi-step
# pcmk-dr-cutover.sh): quiesce the live primary, promote the recovery primary, then re-add
# the single floating IP at the new live site. The QM's objects + messages travel with DR
# replication, so the recovery site needs no content re-apply — only its VIP.
#
# Proven in the Phase-C drill (docs/reports/2026-06-06-phase-c-rdqm-findings.md): ~69 s
# cutover / ~104 s failback at RPO 0 both directions.
#
# Direction: a2b (cutover, site A -> B) | b2a (failback, B -> A). Each direction mirrors.
set -euo pipefail
DIR="${1:-a2b}"
QM="${2:-QMRDQM}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

if [ "$DIR" = a2b ]; then
  FROM_PRIMARY=rdqm-a1; TO_PRIMARY=rdqm-b1; TO_VIP=10.10.2.100
elif [ "$DIR" = b2a ]; then
  FROM_PRIMARY=rdqm-b1; TO_PRIMARY=rdqm-a1; TO_VIP=10.10.1.100
else
  echo "usage: rdqm-dr-cutover.sh [a2b|b2a] [QM=QMRDQM]" >&2
  exit 2
fi

echo "=== 1. Make the live site ($FROM_PRIMARY) the DR secondary ==="
run "$FROM_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -s"

echo "=== 2. Promote the recovery site ($TO_PRIMARY) to DR primary ==="
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -p"

echo "=== 3. Re-add the floating IP at the new live site ($TO_VIP) ==="
IFACE=$(ansible "$TO_PRIMARY" -m shell -a "ip -br addr | grep ${TO_VIP%.*}\\. | cut -d' ' -f1" | tail -1)
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmint -m $QM -a -f $TO_VIP -l $IFACE || true"

echo "=== 4. Verify ==="
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmstatus -m $QM"
echo "=== cutover $DIR complete: $QM now live at $TO_PRIMARY via $TO_VIP ==="
