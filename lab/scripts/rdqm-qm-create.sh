#!/usr/bin/env bash
# lab/scripts/rdqm-qm-create.sh - create the replicated QM on a formed RDQM group.
#
# HA-only (site A): secondaries (-sxs) BEFORE the primary (-sx); both as root; floating
# IP via rdqmint -f <addr> -l <interface>. Lab channel posture per spec 1.
#
# HA/DR (3+3, #288): auto-detected when the site-B HA group (rdqm_b) is formed. Per IBM's
# 9.4 worked example (build/refs/ibm-docs/.../availability-drha-rdqm-worked-example), a
# single `crtmqm -sx -rr p` on each site's primary auto-creates that site's secondaries and
# the DR IPs go on the command line (net-wan, port 7001, async), so no rdqm.ini DR stanza is
# needed. Site A is DR primary (-rr p); site B is DR secondary (-rr s). The QM's objects +
# messages replicate to site B, which needs only its own floating IP.
#
# RDQM supports exactly ONE floating IP per queue manager (rdqmint, #216 spike: a second is
# AMQ3877E). So the FIP is spent on the data plane (VIP) for the app's HA path; the partner
# (QMDTCC) reaches us over net-ext via a per-node CONNAME list, not a second FIP.
#
# Usage: rdqm-qm-create.sh [QM=QMRDQM] [VIP=10.10.1.100] [DTCC_CONN]
#   VIP       - site-A single floating IP (data plane); the app rides HA via this addr
#   DTCC_CONN - counterparty CONNAME; when set, define the our-side inter-QM MQSC to QMDTCC
set -euo pipefail
QM="${1:-QMRDQM}"
VIP="${2:-10.10.1.100}"
DTCC_CONN="${3:-}"
cd "$(dirname "$0")/../../ansible"

# Lab DR topology (net-wan replication addresses + per-site data VIPs). Fixed for this lab,
# like the hardcoded node names below.
A_WAN="10.99.0.31,10.99.0.32,10.99.0.33"
B_WAN="10.99.0.41,10.99.0.42,10.99.0.43"
B_VIP="10.10.2.100"
DR_PORT="7001"

run() { ansible "$1" -b -m shell -a "$2"; }

# Add the single data-plane floating IP on a node, bound to the data-subnet interface.
add_vip() {  # $1=node $2=vip
  local iface
  iface=$(ansible "$1" -m shell -a "ip -br addr | grep ${2%.*}\\. | cut -d' ' -f1" | tail -1)
  run "$1" "/opt/mqm/bin/rdqmint -m $QM -a -f $2 -l $iface || true"
}

# Lab MQSC posture (listener + app channel + a persistent test queue). On HA/DR these
# objects replicate to site B with the QM, so they are defined once on the site-A primary.
base_mqsc() {  # $1=node
  run "$1" "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
}

# Auto-detect HA/DR: present iff the site-B HA group is formed (rdqm_b provisioned via
# site-rdqm-dr.yml). Absent → HA-only (site A), the original shape.
DR=0
if ansible rdqm-b1 -b -m shell -a "/opt/mqm/bin/rdqmstatus -n" >/dev/null 2>&1; then
  DR=1
fi

if [ "$DR" = 1 ]; then
  echo "=== HA/DR detected (site-B group formed) — creating DR/HA QMRDQM across both sites ==="
  # Site A primary (DR primary). One command auto-creates rdqm-a2/a3 secondaries.
  run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -rr p -rl $A_WAN -ri $B_WAN -rp $DR_PORT -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
  # Site B primary (DR secondary). Auto-creates rdqm-b2/b3 secondaries.
  run rdqm-b1 "/opt/mqm/bin/crtmqm -sx -rr s -rl $B_WAN -ri $A_WAN -rp $DR_PORT -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
  add_vip rdqm-a1 "$VIP"
  add_vip rdqm-b1 "$B_VIP"
  base_mqsc rdqm-a1
else
  echo "=== HA-only (no site-B group) — creating site-A QMRDQM ==="
  # Secondaries FIRST, then the primary (verified HA-only order).
  run rdqm-a2,rdqm-a3 "/opt/mqm/bin/crtmqm -fs 3072M -sxs $QM || /opt/mqm/bin/dspmq -m $QM"
  run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -fs 3072M $QM || /opt/mqm/bin/dspmq -m $QM"
  add_vip rdqm-a1 "$VIP"
  base_mqsc rdqm-a1
fi

# Our-side inter-QM MQSC to QMDTCC (#147), only when a counterparty CONNAME is given (the
# distributed setup). Defined on the site-A primary; replicates with the QM.
if [ -n "$DTCC_CONN" ]; then
  run rdqm-a1 "printf 'DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE\nDEFINE QREMOTE(DTCC.REQUEST) RNAME(SVC.REQUEST) RQMNAME(QMDTCC) XMITQ(QMDTCC) REPLACE\nDEFINE QLOCAL(QMDTCC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA($QM.QMDTCC) REPLACE\nDEFINE CHANNEL($QM.QMDTCC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('\\''$DTCC_CONN(1414)'\\'') XMITQ(QMDTCC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE\nDEFINE CHANNEL(QMDTCC.$QM) CHLTYPE(RCVR) TRPTYPE(TCP) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
fi

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
[ "$DR" = 1 ] && run rdqm-b1 "/opt/mqm/bin/rdqmstatus -m $QM" || true
