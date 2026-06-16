#!/usr/bin/env bash
# lab/scripts/dr-run.sh — drive one no-fault baseline flow for a setup and collect
# the firm + DTCC ledgers to the host run dir (pivot spec §4.4 / DR-HA framework §4).
# Assumes the setup is already provisioned and up (run `mqlab vm provision <setup>`
# first). Reuses the deployed clients/dr_flow.py + clients/dr_responder.py.
#
# This is the integration seam between `mqlab run` and the live lab — its exact
# client flags must track clients/dr_flow.py + clients/dr_responder.py; verify and
# adjust during the lab-integration acceptance gate, not via a unit test.
set -euo pipefail
SETUP="" QM="" VIP="" SECONDS_RUN=30 RATE=20 FIRM_LEDGER="" DTCC_LEDGER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup) SETUP="$2"; shift 2;;
    --qm) QM="$2"; shift 2;;
    --vip) VIP="$2"; shift 2;;
    --seconds) SECONDS_RUN="$2"; shift 2;;
    --rate) RATE="$2"; shift 2;;
    --firm-ledger) FIRM_LEDGER="$2"; shift 2;;
    --dtcc-ledger) DTCC_LEDGER="$2"; shift 2;;
    *) echo "dr-run: unknown arg $1" >&2; exit 2;;
  esac
done
: "${SETUP:?} ${QM:?} ${VIP:?} ${FIRM_LEDGER:?} ${DTCC_LEDGER:?}"
cd "$(dirname "$0")/.."
mkdir -p "$(dirname "$FIRM_LEDGER")"

# Responder on the surviving DTCC side (background; outlives the flow window).
vagrant ssh dtcc-sim -c \
  "~/mqvenv/bin/python ~/dr_responder.py --qm QMDTCC --conn 'localhost(1414)' \
   --in-queue TRADE.REQUEST --out-queue FIRM.REPLY --seconds $((SECONDS_RUN + 10)) \
   --ledger ~/dr-ledgers/dtcc.jsonl" &
RESP_PID=$!

# Steady-state firm flow through the QM VIP.
vagrant ssh app-client -c \
  "~/mqvenv/bin/python ~/dr_flow.py --qm ${QM} --conn '${VIP}(1414)' \
   --req-queue DR.REQUEST --reply-queue DR.REPLY --rate ${RATE} --seconds ${SECONDS_RUN} \
   --ledger ~/dr-ledgers/firm.jsonl"
wait "$RESP_PID"

# Collect both ledgers to the host run dir.
vagrant ssh app-client -c 'cat ~/dr-ledgers/firm.jsonl' > "$FIRM_LEDGER"
vagrant ssh dtcc-sim   -c 'cat ~/dr-ledgers/dtcc.jsonl' > "$DTCC_LEDGER"
echo "dr-run: collected ledgers for ${SETUP} (${QM}@${VIP})"
