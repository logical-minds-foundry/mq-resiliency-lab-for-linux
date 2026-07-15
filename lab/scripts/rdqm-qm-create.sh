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
# Idempotent + loud (#583): skip if the QM already carries this floating IP (a re-provision
# re-runs this), otherwise add it and let a genuine rdqmint failure surface. The old blanket
# `|| true` swallowed EVERY rdqmint error — masking the benign "already added" AMQ3873E on
# re-runs, but equally hiding a real misconfiguration (wrong node/interface) behind silence.
add_vip() {  # $1=node $2=vip
  local iface vipstat
  # rdqmstatus reports the QM's configured floating IP in the LOCAL node's block, whether
  # this node is primary or secondary, so this idempotency check works on any site node.
  vipstat=$(run "$1" "/opt/mqm/bin/rdqmstatus -m $QM" 2>/dev/null || true)
  if [[ $vipstat == *"floating IP address:"*"$2"* ]]; then
    echo "=== floating IP $2 already configured for $QM on $1 — skipping ==="
    return 0
  fi
  iface=$(ansible "$1" -m shell -a "ip -br addr | grep ${2%.*}\\. | cut -d' ' -f1" | tail -1)
  run "$1" "/opt/mqm/bin/rdqmint -m $QM -a -f $2 -l $iface"
}

# Resolve the site-A node currently running $QM (the HA primary). It floats across the
# group and — critically on the resume path, where crtmqm is skipped (qm_exists) — is not
# necessarily rdqm-a1. runmqsc / rdqmint config must target it or fail AMQ8146E. Bash
# analog of the rdqm-active-node Ansible role (#582/#581). Retry briefly so a QM that is
# mid-start right after crtmqm is not misread as absent; fail loud if none settles.
active_a_node() {
  local n i out
  for i in $(seq 1 30); do
    for n in rdqm-a1 rdqm-a2 rdqm-a3; do
      # Capture then match in-shell (no `| grep -q`: under `set -o pipefail` grep -q
      # closes the pipe early, SIGPIPEs ansible, and the pipeline reports failure even on
      # a match). STATUS(Running) — with the closing paren — is unique to the active node;
      # STATUS(Running elsewhere) is a standby and does not contain the literal.
      out=$(ansible "$n" -b -m shell -a "/opt/mqm/bin/dspmq -m $QM -o status" 2>/dev/null || true)
      if [[ $out == *"STATUS(Running)"* ]]; then
        echo "$n"; return 0
      fi
    done
    sleep 5
  done
  echo "ERROR: no site-A node (rdqm-a1/a2/a3) is running $QM" >&2
  return 1
}

# Run an MQSC command block on the active site-A primary, retrying on a transient failure
# (#657). Right after the coordinated create the pacemaker-managed RDQM cluster is still
# settling — microseconds after crtmqm the first runmqsc client connection can drop
# mid-command (AMQ8145E "Connection broken"), even though the QM is already Running (the
# active_a_node gate above). The intermittent flake is the last gap to a clean one-shot
# rebuild. Every MQSC block passed here is idempotent (all DEFINEs use REPLACE, ALTER/REFRESH
# are set-state, the listener START is DISPLAY-guarded), so a full re-run — or a mid-run
# retry — is harmless. Re-resolve the active primary each attempt: an HA relocation while the
# cluster settles may have moved the QM to another site-A node (active_a_node, #582), and a
# runmqsc against a standby fails AMQ8146E. Fail loud after exhausting attempts — a persistent
# failure is a real fault, not a transient one, and must not be swallowed (#583).
run_mqsc_retry() {  # $1=label  $2=MQSC command (run on the current active site-A primary)
  local label="$1" cmd="$2" node i
  local attempts=5 delay=5
  for (( i=1; i<=attempts; i++ )); do
    node=$(active_a_node)  # re-resolve each try; a relocation may have moved the primary
    if run "$node" "$cmd"; then
      [ "$i" -gt 1 ] && echo "=== $label: runmqsc succeeded on attempt $i/$attempts ($node) ==="
      return 0
    fi
    if [ "$i" -lt "$attempts" ]; then
      echo "=== $label: runmqsc attempt $i/$attempts on $node failed (transient connection-broken? AMQ8145E) — re-resolving the active primary and retrying in ${delay}s ===" >&2
      sleep "$delay"
    fi
  done
  echo "ERROR: $label: runmqsc still failing after $attempts attempts on the site-A primary (last target $node)" >&2
  return 1
}

# Lab MQSC posture (listener + app channel + a persistent test queue). On HA/DR these
# objects replicate to site B with the QM, so they are defined once on the site-A primary.
# Each runmqsc block is wrapped in run_mqsc_retry (#657) — it re-resolves and targets the
# active primary itself, so no node arg is threaded in here.
base_mqsc() {
  run_mqsc_retry "base MQSC (listener + APP.SVRCONN + CHLAUTH/CONNAUTH + HA.TEST)" \
    "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
  # Start the listener only if not already running (#577): START LISTENER errors when it is,
  # which broke the resume path (bootstrap --from provision). The DEFINEs above are idempotent
  # via REPLACE; CONTROL(QMGR) also (re)starts the listener on QM start/failover.
  run_mqsc_retry "listener start (L1414)" \
    "printf 'DISPLAY LSSTATUS(L1414) STATUS\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM' 2>/dev/null | grep -q 'STATUS(RUNNING)' || printf 'START LISTENER(L1414)\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
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
  A_PRIMARY=$(active_a_node)  # the site-A node running the QM (not necessarily a1 on a resume)
  add_vip "$A_PRIMARY" "$VIP"
  add_vip rdqm-b1 "$B_VIP"    # site B is the DR secondary (QM stopped there); FIP on its group head
  base_mqsc
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
  A_PRIMARY=$(active_a_node)  # the site-A node running the QM (not necessarily a1 on a resume)
  add_vip "$A_PRIMARY" "$VIP"
  base_mqsc
fi

# Our-side inter-QM MQSC to the SVC counterparty (#147), only when a counterparty
# CONNAME is given (the distributed setup). Defined on the site-A primary; replicates
# with the QM. QM_SVC is the shared counterparty (SVCQM); this stack's request queue
# on it is SVC_REQ_QUEUE ({SHORT}.SVC.REQUEST) — both threaded in, not derived (#446).
if [ -n "$SVC_CONN" ]; then
  # RNAME must not be empty, else DEFINE QREMOTE(SVC.REQUEST) RNAME() is an MQSC syntax
  # error (AMQ8405I). SVC_REQ_QUEUE ({SHORT}.SVC.REQUEST) is threaded from site-rdqm.yml (#574).
  : "${SVC_REQ_QUEUE:?SVC_REQ_QUEUE (arg 5) is required when SVC_CONN is set — the QREMOTE RNAME would be empty}"
  # Wrapped in run_mqsc_retry (#657): this is the same create->configure boundary, so the
  # runmqsc connection can transiently break here too. All DEFINEs use REPLACE = idempotent.
  run_mqsc_retry "SVC inter-QM MQSC (QREMOTE/XMITQ/SDR/RCVR)" \
    "printf 'DEFINE QLOCAL(APP.REPLY) DEFPSIST(YES) REPLACE\nDEFINE QREMOTE(SVC.REQUEST) RNAME($SVC_REQ_QUEUE) RQMNAME($QM_SVC) XMITQ($QM_SVC) REPLACE\nDEFINE QLOCAL($QM_SVC) USAGE(XMITQ) TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA($QM.$QM_SVC) REPLACE\nDEFINE CHANNEL($QM.$QM_SVC) CHLTYPE(SDR) TRPTYPE(TCP) CONNAME('\\''$SVC_CONN(1414)'\\'') XMITQ($QM_SVC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE\nDEFINE CHANNEL($QM_SVC.$QM) CHLTYPE(RCVR) TRPTYPE(TCP) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"
fi

run "$A_PRIMARY" "/opt/mqm/bin/rdqmstatus -m $QM"
[ "$DR" = 1 ] && run rdqm-b1 "/opt/mqm/bin/rdqmstatus -m $QM" || true
