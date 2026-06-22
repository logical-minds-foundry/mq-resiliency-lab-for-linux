#!/usr/bin/env bash
# lab/scripts/e2e-test.sh - N requests through the distributed flow:
#   app -> <our HA QM> -> inter-QM SENDER/RECEIVER -> QMSVC -> service -> reply back.
# The SVC service responder runs as a systemd service (mq-service-responder on
# svc-sim), so we just drive the app. Exits non-zero unless every request
# round-trips (app_requester returns 1 on any miss). (#148)
#
# Arm-agnostic: the app targets our-side HA QM by name + CONNAME. Defaults are the
# Pacemaker arm (QMPCMK, site VIPs 10.10.1.200/10.10.2.200); pass QM + CONN to drive
# the RDQM arm (QMRDQM, data VIP 10.10.1.100). (#216)
# Usage: e2e-test.sh [N=5] [QM=QMPCMK] [CONN=<conname-list>]
set -euo pipefail
N="${1:-5}"
QM="${2:-QMPCMK}"
CONN="${3:-10.10.1.200(1414),10.10.2.200(1414)}"
cd "$(dirname "$0")/.."
# TLS (#250): present the app-client cert over the mutual-TLS APP.SVRCONN. The
# keystore stem (+ sibling .sth stash) and cert label are placed by mq-client.
vagrant ssh app-client -c \
  "~/mqvenv/bin/python ~/app_requester.py --qm ${QM} --conn '${CONN}' --count ${N} \
     --keyrepo /home/vagrant/ssl/app-client --certlabel app-client"
