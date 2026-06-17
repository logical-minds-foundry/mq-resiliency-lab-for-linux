#!/usr/bin/env bash
# lab/scripts/rdqm-dr-qm-create.sh - create the DR/HA RDQM QMRDQM across the 3+3
# cluster (#233). Standalone (not a qm-create registry verb): the registry resolves
# verbs per-arm and rdqm_ha/rdqm_dr share rdqm-rhel, so DR lives in the rdqm-dr-*
# family like the Pacemaker arm's pcmk-dr-*.sh.
#
# Canonical (IBM 9.4 "Creating DR/HA RDQMs" + worked example): ONE crtmqm per site
# primary fans out to that site's two HA secondaries -> six instances, two commands.
# HA role -sx (primary); DR role -rr p / -rr s. DR partners by net-wan IP (-rl local
# trio, -ri remote trio); DR replication on port 7001.
set -euo pipefail
QM="${1:-QMRDQM}"
VIP_A="${2:-10.10.1.100}"     # site-A floating IP (net-data-a)
VIP_B="${3:-10.10.2.100}"     # site-B floating IP (net-data-b)
A_WAN="10.99.0.31,10.99.0.32,10.99.0.33"   # site-A DR interfaces (net-wan)
B_WAN="10.99.0.41,10.99.0.42,10.99.0.43"   # site-B DR interfaces (net-wan)
DRPORT=7001
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

# Site A: HA primary + DR primary on rdqm-a1; fans out to rdqm-a2/a3 as HA secondaries.
run rdqm-a1 "/opt/mqm/bin/crtmqm -sx -rr p -rl $A_WAN -ri $B_WAN -rp $DRPORT -fs 3072M $QM \
  || /opt/mqm/bin/dspmq -m $QM"
# Site B: HA primary + DR secondary on rdqm-b1; fans out to rdqm-b2/b3.
run rdqm-b1 "/opt/mqm/bin/crtmqm -sx -rr s -rl $B_WAN -ri $A_WAN -rp $DRPORT -fs 3072M $QM \
  || /opt/mqm/bin/dspmq -m $QM"

# One floating IP per HA group (#223) on each site's primary node.
IFACE_A=$(ansible rdqm-a1 -m shell -a "ip -br addr | grep ${VIP_A%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-a1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_A -l $IFACE_A || true"
IFACE_B=$(ansible rdqm-b1 -m shell -a "ip -br addr | grep ${VIP_B%.*}\\. | cut -d' ' -f1" | tail -1)
run rdqm-b1 "/opt/mqm/bin/rdqmint -m $QM -a -f $VIP_B -l $IFACE_B || true"

# Lab MQSC posture (same as Plan B), defined on the active DR primary (site A); the
# QM's object data replicates to site B via DR.
run rdqm-a1 "printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM'"

run rdqm-a1 "/opt/mqm/bin/rdqmstatus -m $QM"
