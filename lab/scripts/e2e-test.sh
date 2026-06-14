#!/usr/bin/env bash
# lab/scripts/e2e-test.sh - N requests through the distributed flow:
#   app -> QMPCMK -> inter-QM SENDER/RECEIVER -> QMDTCC -> service -> reply back.
# The DTCC service responder runs as a systemd service (mq-service-responder on
# dtcc-sim), so we just drive the app. Exits non-zero unless every request
# round-trips (app_requester returns 1 on any miss). (#148)
set -euo pipefail
N="${1:-5}"
cd "$(dirname "$0")/.."
vagrant ssh app-client -c "~/mqvenv/bin/python ~/app_requester.py --count ${N}"
