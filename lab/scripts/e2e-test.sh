#!/usr/bin/env bash
# lab/scripts/e2e-test.sh - N trades through app-client -> QMAIN ->
# channel -> QDTCC -> responder -> back. Exits non-zero unless every
# trade round-trips with a clean ACK.
set -euo pipefail
N="${1:-5}"
cd "$(dirname "$0")/.."
vagrant ssh dtcc-sim -c "~/mqvenv/bin/python ~/epn_responder.py $N" &
RESP=$!
sleep 3
RC=0
vagrant ssh app-client -c "~/mqvenv/bin/python ~/epn_requester.py $N" || RC=$?
wait "$RESP" || RC=$?
exit "$RC"
