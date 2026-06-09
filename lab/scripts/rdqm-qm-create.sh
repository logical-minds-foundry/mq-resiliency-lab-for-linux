#!/usr/bin/env bash
# lab/scripts/rdqm-qm-create.sh - create a replicated QM on a formed RDQM
# group (the spec 8.4 'form-group' verb, site-A shape). Verified order:
# secondaries (-sxs) BEFORE the primary (-sx); both as root; floating IP
# via rdqmint -f <addr> -l <interface>. Lab channel posture per spec 1.
# Usage: rdqm-qm-create.sh [QM=QMRDQM] [VIP=10.10.1.100]
set -euo pipefail
QM="${1:-QMRDQM}"
VIP="${2:-10.10.1.100}"
cd "$(dirname "$0")/../../ansible"

run() { uv run ansible "$1" -b -m shell -a "$2"; }

run rdqm-a2,rdqm-a3 "/opt/mqm/bin/crtmqm -fs 3072M -sxs $QM || /opt/mqm/bin/dspmq -m $QM"
run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
IFACE=$(uv run ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP -l $IFACE || true"
run rdqm-a1 "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
