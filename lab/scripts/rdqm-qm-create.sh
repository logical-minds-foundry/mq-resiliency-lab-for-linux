#!/usr/bin/env bash
# lab/scripts/rdqm-qm-create.sh - create the replicated QM on a formed RDQM group.
#
# Create order (#561): IBM's documented coordinated flow. A single `crtmqm -sx` per site,
# run AS mqm, auto-creates that site's HA secondaries by SSH-ing to the peers as mqm and
# running sudo crtmqm there (per IBM's 9.4 worked example,
# build/cache/refs/ibm-docs/.../availability-drha-rdqm-worked-example). The mqm passwordless
# SSH + sudo is provisioned by the rdqm-ssh-access role (#560) before mqweb, and removed
# after this script. This replaces the old manual secondaries-first workaround, which forced
# a full initial DRBD resync that never reached UpToDate on DR/HA and failed as
# AMQ3879E/AMQ3817E (#559). Floating IP via rdqmint -f <addr> -l <interface>.
#
# HA/DR (3+3, #288): auto-detected when the site-B HA group (rdqm_b) is formed. Site A is DR
# primary (-rr p); site B is DR secondary (-rr s). The DR IPs go on the command line
# (net-wan, port 7001, async), so no rdqm.ini DR stanza is needed. The QM's objects +
# messages replicate to site B, which needs only its own floating IP.
#
# RDQM supports exactly ONE floating IP per queue manager (rdqmint, #216 spike: a second is
# AMQ3877E). So the FIP is spent on the data plane (VIP) for the app's HA path; the partner
# (RDQMSVC) reaches us over net-ext via a per-node CONNAME list, not a second FIP.
#
# Usage: rdqm-qm-create.sh [QM=RDQMAPP] [VIP=10.10.1.100] [SVC_CONN] [QM_SVC=SVCQM] [SVC_REQ_QUEUE]
#   VIP           - site-A single floating IP (data plane); the app rides HA via this addr
#   SVC_CONN      - counterparty CONNAME; when set, define the our-side inter-QM MQSC to QM_SVC
#   QM_SVC        - the shared counterparty QM name (SVCQM); #446
#   SVC_REQ_QUEUE - this stack's own request queue on QM_SVC ({SHORT}.SVC.REQUEST); #446
set -euo pipefail
QM="${1:-RDQMAPP}"
VIP="${2:-10.10.1.100}"
SVC_CONN="${3:-}"
# The shared SVC counterparty (SVCQM) and this stack's request queue on it (#446),
# threaded from QmConfig; no longer derived {QM/APP/SVC}.
QM_SVC="${4:-SVCQM}"
SVC_REQ_QUEUE="${5:-}"
cd "$(dirname "$0")/../../ansible"

# Lab DR topology (net-wan replication addresses + per-site data VIPs). Fixed for this lab,
# like the hardcoded node names below.
A_WAN="10.99.0.31,10.99.0.32,10.99.0.33"
B_WAN="10.99.0.41,10.99.0.42,10.99.0.43"
B_VIP="10.10.2.100"
DR_PORT="7001"

run() { ansible "$1" -b -m shell -a "$2"; }

# Run a command as the mqm login user. The coordinated crtmqm create must run as mqm:
# crtmqm self-sudos for its privileged work and SSHes to the HA peers AS mqm (over the
# 172.16.x replication net) to auto-create that site's secondaries. The mqm passwordless
# SSH + sudo is provisioned by the rdqm-ssh-access role (#560) before mqweb, and removed
# after this script. `su - mqm` gives the login env (HOME=/home/mqm) mqm's SSH needs.
run_mqm() { ansible "$1" -b -m shell -a "su - mqm -c \"$2\""; }

# Add the single data-plane floating IP on a node, bound to the data-subnet interface.
add_vip() {  # $1=node $2=vip
  local iface
  iface=$(ansible "$1" -m shell -a "ip -br addr | grep ${2%.*}\\. | cut -d' ' -f1" | tail -1)
  run "$1" "/opt/mqm/bin/rdqmint -m $QM -a -f $2 -l $iface || true"
}

# Lab MQSC posture (listener + app channel + a persistent test queue). On HA/DR these
# objects replicate to site B with the QM, so they are defined once on the site-A primary.
base_mqsc() {  # $1=node
  run "$1" "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
  # Start the listener only if not already running (#577): START LISTENER errors when it is,
  # which broke the resume path (bootstrap --from provision). The DEFINEs above are idempotent
  # via REPLACE; CONTROL(QMGR) also (re)starts the listener on QM start/failover.
  run "$1" "printf 'DISPLAY LSSTATUS(L1414) STATUS\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM' 2>/dev/null | grep -q 'STATUS(RUNNING)' || printf 'START LISTENER(L1414)\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
}

# Auto-detect HA/DR: present iff the site-B HA group is formed (rdqm_b provisioned via
# site-rdqm-dr.yml). Absent → HA-only (site A), the original shape.
DR=0
if ansible rdqm-b1 -b -m shell -a "/opt/mqm/bin/rdqmstatus -n" >/dev/null 2>&1; then
  DR=1
fi

# Skip the create if the QM already exists on the site-A primary (idempotent re-provision).
qm_exists() { run rdqm-a1 "/opt/mqm/bin/dspmq | grep -q 'QMNAME($QM)'" >/dev/null 2>&1; }

if [ "$DR" = 1 ]; then
  echo "=== HA/DR detected (site-B group formed) — coordinated create (crtmqm auto-creates secondaries) ==="
  # Secure BOTH the HA and DR replication links with TLS (#545): -re. The per-node certs +
  # tlshd are provisioned by rdqm-replication-tls BEFORE this runs; the cert SANs use the
  # default group DNS name (encrypted.remote), so no -san is needed.
  REPL_TLS="-re"
  if qm_exists; then
    echo "=== $QM already exists on rdqm-a1 — skipping create ==="
  else
    # IBM's documented DR/HA create: ONE `crtmqm -sx` per site, run as mqm. crtmqm SSHes
    # to that site's peers (over 172.16.x) as mqm and auto-creates the secondaries
    # ("Secondary queue manager created on ..."). No manual -sxs.
    run_mqm rdqm-a1 "/opt/mqm/bin/crtmqm -fs 3072M -sx -rr p -rl $A_WAN -ri $B_WAN -rp $DR_PORT $REPL_TLS $QM"  # site A = DR primary, auto-creates a2/a3
    run_mqm rdqm-b1 "/opt/mqm/bin/crtmqm -fs 3072M -sx -rr s -rl $B_WAN -ri $A_WAN -rp $DR_PORT $REPL_TLS $QM"  # site B = DR secondary, auto-creates b2/b3
  fi
  add_vip rdqm-a1 "$VIP"
  add_vip rdqm-b1 "$B_VIP"
  base_mqsc rdqm-a1
else
  echo "=== HA-only (no site-B group) — coordinated create ==="
  # Secure the HA replication links with TLS (#545): -reh (this shape has no DR link).
  REPL_TLS="-reh"
  if qm_exists; then
    echo "=== $QM already exists on rdqm-a1 — skipping create ==="
  else
    # Single `crtmqm -sx` on the site-A primary as mqm; auto-creates a2/a3.
    run_mqm rdqm-a1 "/opt/mqm/bin/crtmqm -fs 3072M -sx $REPL_TLS $QM"
  fi
  add_vip rdqm-a1 "$VIP"
  base_mqsc rdqm-a1
fi

# Our-side inter-QM MQSC to the SVC counterparty (#147), only when a counterparty
# CONNAME is given (the distributed setup). Defined on the site-A primary; replicates
# with the QM. QM_SVC is the shared counterparty (SVCQM); this stack's request queue
# on it is SVC_REQ_QUEUE ({SHORT}.SVC.REQUEST) — both threaded in, not derived (#446).
if [ -n "$SVC_CONN" ]; then
  # RNAME must not be empty, else DEFINE QREMOTE(SVC.REQUEST) RNAME() is an MQSC syntax
  # error (AMQ8405I). SVC_REQ_QUEUE ({SHORT}.SVC.REQUEST) is threaded from site-rdqm.yml (#574).
  : "${SVC_REQ_QUEUE:?SVC_REQ_QUEUE (arg 5) is required when SVC_CONN is set — the QREMOTE RNAME would be empty}"
  run rdqm-a1 "printf 'DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE\nDEFINE QREMOTE(SVC.REQUEST) RNAME($SVC_REQ_QUEUE) RQMNAME($QM_SVC) XMITQ($QM_SVC) REPLACE\nDEFINE QLOCAL($QM_SVC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA($QM.$QM_SVC) REPLACE\nDEFINE CHANNEL($QM.$QM_SVC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('\\''$SVC_CONN(1414)'\\'') XMITQ($QM_SVC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE\nDEFINE CHANNEL($QM_SVC.$QM) CHLTYPE(RCVR) TRPTYPE(TCP) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
fi

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
[ "$DR" = 1 ] && run rdqm-b1 "/opt/mqm/bin/rdqmstatus -m $QM" || true
