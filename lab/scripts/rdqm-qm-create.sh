#!/usr/bin/env bash
# lab/scripts/rdqm-qm-create.sh - create a replicated QM on a formed RDQM
# group (the spec 8.4 'form-group' verb, site-A shape). Verified order:
# secondaries (-sxs) BEFORE the primary (-sx); both as root; floating IP
# via rdqmint -f <addr> -l <interface>. Lab channel posture per spec 1.
# Usage: rdqm-qm-create.sh [QM=QMRDQM] [VIP=10.10.1.100] [VIP_EXT] [DTCC_CONN]
#   VIP_EXT   - partner-facing floating IP on net-ext (the inter-business link, #216)
#   DTCC_CONN - counterparty CONNAME; when set, define the our-side inter-QM MQSC to QMDTCC
set -euo pipefail
QM="${1:-QMRDQM}"
VIP="${2:-10.10.1.100}"
VIP_EXT="${3:-}"
DTCC_CONN="${4:-}"
cd "$(dirname "$0")/../../ansible"

run() { ansible "$1" -b -m shell -a "$2"; }

run rdqm-a2,rdqm-a3 "/opt/mqm/bin/crtmqm -fs 3072M -sxs $QM || /opt/mqm/bin/dspmq -m $QM"
run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
IFACE=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP -l $IFACE || true"

# Partner-facing floating IP on net-ext (the inter-business link to QMDTCC, #216).
if [ -n "$VIP_EXT" ]; then
  IFACE_EXT=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP_EXT%.*}\\. | cut -d' ' -f1" | tail -1)
  run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_EXT -l $IFACE_EXT || true"
fi

run rdqm-a1 "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"

# Our-side inter-QM MQSC to QMDTCC (#147), mirroring mq-pcmk-qmgr's inter-qm.mqsc.j2,
# only when a counterparty CONNAME is given (the distributed setup).
if [ -n "$DTCC_CONN" ]; then
  run rdqm-a1 "printf 'DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE\nDEFINE QREMOTE(DTCC.REQUEST) RNAME(SVC.REQUEST) RQMNAME(QMDTCC) XMITQ(QMDTCC) REPLACE\nDEFINE QLOCAL(QMDTCC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA($QM.QMDTCC) REPLACE\nDEFINE CHANNEL($QM.QMDTCC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('\\''$DTCC_CONN(1414)'\\'') XMITQ(QMDTCC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE\nDEFINE CHANNEL(QMDTCC.$QM) CHLTYPE(RCVR) TRPTYPE(TCP) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
fi

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
