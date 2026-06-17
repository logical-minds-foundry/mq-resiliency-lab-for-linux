#!/usr/bin/env bash
# lab/scripts/wan-degrade.sh - widen the cross-site DR async window so RPO is
# measurable (#233). RDQM hides DRBD, so we degrade the only layer it leaves open to
# us — the net-wan replication link — with tc/netem on the ACTIVE site-A primary's
# net-wan egress. Intra-site HA (net-hb/net-data) is untouched, so HA stays healthy.
# Usage: wan-degrade.sh on|off [NODE=rdqm-a1] [DELAY=80ms] [LOSS=5%]
set -euo pipefail
ACTION="${1:?usage: wan-degrade.sh on|off [node] [delay] [loss]}"
NODE="${2:-rdqm-a1}"
DELAY="${3:-80ms}"
LOSS="${4:-5%}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }
IFACE=$(ansible "$NODE" -m shell -a "ip -br addr | grep 10.99.0. | cut -d' ' -f1" | tail -1)
case "$ACTION" in
  on)  run "$NODE" "tc qdisc replace dev $IFACE root netem delay $DELAY loss $LOSS" ;;
  off) run "$NODE" "tc qdisc del dev $IFACE root || true" ;;
  *) echo "action must be on|off" >&2; exit 2 ;;
esac
echo "=== wan-degrade $ACTION on $NODE:$IFACE (delay=$DELAY loss=$LOSS) ==="
