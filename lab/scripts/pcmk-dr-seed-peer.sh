#!/usr/bin/env bash
# lab/scripts/pcmk-dr-seed-peer.sh — seed the QM definition + a (disabled)
# systemd unit onto the DR-peer cluster, so a cutover can start the QM there.
# Run after the QM is created at the live site (pcmk-qm-create.sh). The QM DATA
# travels via DRBD; this only teaches the peer nodes that the QM exists (the
# addmqinf + unit the Phase D findings flagged as a manual pre-cutover step).
# Usage: pcmk-dr-seed-peer.sh [QM=QMPCMK] [PEER=pcmk_b] [SRC=pcmk-a1]
set -euo pipefail
QM="${1:-QMPCMK}"
PEER="${2:-pcmk_b}"
SRC="${3:-pcmk-a1}"
cd "$(dirname "$0")/../../ansible"
run() { uv run ansible "$1" -b -m shell -a "$2"; }

INF=$(run "$SRC" "su mqm -c '/opt/mqm/bin/dspmqinf -o command $QM'" | grep '^addmqinf' | tr -d '\r')
run "$PEER" "mkdir -p /mqshared; su mqm -c '/opt/mqm/bin/${INF#*/opt/mqm/bin/}' || su mqm -c '/opt/mqm/bin/dspmq -m $QM'"
run "$PEER" "printf '[Unit]\nDescription=IBM MQ queue manager $QM (pacemaker-managed)\n[Service]\nType=forking\nUser=mqm\nExecStart=/opt/mqm/bin/strmqm $QM\nExecStop=/opt/mqm/bin/endmqm -w $QM\nTimeoutStartSec=300\n' > /etc/systemd/system/mq-$QM.service && systemctl daemon-reload && systemctl disable mq-$QM.service 2>/dev/null; true"
echo "seeded $QM onto $PEER (definition + disabled unit; data arrives via DRBD on cutover)"
