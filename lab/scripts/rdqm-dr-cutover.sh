#!/usr/bin/env bash
# lab/scripts/rdqm-dr-cutover.sh - controlled cross-site cutover/failback of the DR/HA
# RDQM (#233). rdqmdr -s demotes the current DR primary; rdqmdr -p promotes the peer.
# Canonical (IBM 9.4 rdqmdr): the -p promote fails if the old primary is still running
# with the link up (built-in split-brain guard) — so we demote the source first.
# Usage: rdqm-dr-cutover.sh a2b|b2a [QM=QMRDQM]
set -euo pipefail
DIR="${1:?usage: rdqm-dr-cutover.sh a2b|b2a [QM]}"
QM="${2:-QMRDQM}"
case "$DIR" in
  a2b) FROM=rdqm-a1; TO=rdqm-b1 ;;
  b2a) FROM=rdqm-b1; TO=rdqm-a1 ;;
  *) echo "direction must be a2b|b2a" >&2; exit 2 ;;
esac
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
run "$FROM" "/opt/mqm/bin/rdqmdr -m $QM -s"   # demote source to DR secondary
run "$TO"   "/opt/mqm/bin/rdqmdr -m $QM -p"   # promote peer to DR primary
run "$TO"   "/opt/mqm/bin/rdqmstatus -m $QM"
echo "=== cutover $DIR complete; DR primary is now on $TO ==="
