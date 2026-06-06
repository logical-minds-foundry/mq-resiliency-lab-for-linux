#!/usr/bin/env bash
# lab/scripts/e2e-test.sh - N trades through app-client -> QMAIN ->
# channel -> QDTCC -> responder -> back. Exits non-zero unless every
# trade round-trips with a clean ACK.
set -euo pipefail
N="${1:-5}"
cd "$(dirname "$0")/.."
# Drain the trade queues first - residue from interrupted runs otherwise
# satisfies (or starves) the counted get loops and corrupts the assertion.
vagrant ssh qm-main -c 'echo "CLEAR QLOCAL(TRADE.REPLY)" | sudo -u mqm /opt/mqm/bin/runmqsc QMAIN' >/dev/null 2>&1 || true
vagrant ssh dtcc-sim -c 'echo "CLEAR QLOCAL(TRADE.REQUEST)" | sudo -u mqm /opt/mqm/bin/runmqsc QDTCC' >/dev/null 2>&1 || true
vagrant ssh dtcc-sim -c "~/mqvenv/bin/python ~/epn_responder.py $N" &
RESP=$!
sleep 3
RC=0
vagrant ssh app-client -c "~/mqvenv/bin/python ~/epn_requester.py $N" || RC=$?
wait "$RESP" || RC=$?
exit "$RC"
