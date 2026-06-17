#!/usr/bin/env bash
# lab/scripts/rdqm-qm-create.sh - create a replicated QM on a formed RDQM
# group (the spec 8.4 'form-group' verb, site-A shape). Verified order:
# secondaries (-sxs) BEFORE the primary (-sx); both as root; floating IP
# via rdqmint -f <addr> -l <interface>. Lab channel posture per spec 1.
#
# RDQM supports exactly ONE floating IP per queue manager (rdqmint) -- confirmed
# by the #216 verb spike: a second rdqmint is rejected with AMQ3877E "Floating IP
# address already exists". So, unlike the Pacemaker arm (which binds both a data
# VIP and a partner VIP to the QM resource group), the RDQM arm spends its single
# FIP on the data plane (VIP) for the app's HA path. The inter-business partner
# (QMDTCC) reaches this QM over net-ext via a per-node CONNAME list -- the SDR
# reconnects to whichever node currently runs the QM -- instead of a second FIP.
# See ansible/site-rdqm-distributed.yml (our_conn) and the spike report
# docs/reports/2026-06-16-rdqm-verb-spike.md.
# Usage: rdqm-qm-create.sh [QM=QMRDQM] [VIP=10.10.1.100] [DTCC_CONN]
#   VIP       - the single floating IP (data plane); the app rides HA via this addr
#   DTCC_CONN - counterparty CONNAME; when set, define the our-side inter-QM MQSC to QMDTCC
set -euo pipefail
QM="${1:-QMRDQM}"
VIP="${2:-10.10.1.100}"
DTCC_CONN="${3:-}"
cd "$(dirname "$0")/../../ansible"

run() { ansible "$1" -b -m shell -a "$2"; }

run rdqm-a2,rdqm-a3 "/opt/mqm/bin/crtmqm -fs 3072M -sxs $QM || /opt/mqm/bin/dspmq -m $QM"
run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
IFACE=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP -l $IFACE || true"

run rdqm-a1 "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"

# Our-side inter-QM MQSC to QMDTCC (#147), mirroring mq-pcmk-qmgr's inter-qm.mqsc.j2,
# only when a counterparty CONNAME is given (the distributed setup).
if [ -n "$DTCC_CONN" ]; then
  run rdqm-a1 "printf 'DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE\nDEFINE QREMOTE(DTCC.REQUEST) RNAME(SVC.REQUEST) RQMNAME(QMDTCC) XMITQ(QMDTCC) REPLACE\nDEFINE QLOCAL(QMDTCC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA($QM.QMDTCC) REPLACE\nDEFINE CHANNEL($QM.QMDTCC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('\\''$DTCC_CONN(1414)'\\'') XMITQ(QMDTCC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE\nDEFINE CHANNEL(QMDTCC.$QM) CHLTYPE(RCVR) TRPTYPE(TCP) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
fi

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
