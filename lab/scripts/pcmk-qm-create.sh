#!/usr/bin/env bash
# lab/scripts/pcmk-qm-create.sh - create QMPCMK on the shared LUN and hand
# it to Pacemaker as a Filesystem -> IPaddr2 -> systemd resource group.
# The hand-built spelling of what 'crtmqm -sx' + rdqmint did in two
# commands on the RDQM arm (spec 8.4 verb parity; ledger evidence).
# Usage: pcmk-qm-create.sh [QM=QMPCMK] [VIP=10.10.1.200]
set -euo pipefail
QM="${1:-QMPCMK}"
VIP="${2:-10.10.1.200}"
cd "$(dirname "$0")/../../ansible"

run() { uv run ansible "$1" -b -m shell -a "$2"; }

# 1. Create the QM with data+logs on the LUN (a1 holds the mount only for
#    creation; Pacemaker owns it afterwards).
run pcmk-a1 "mkdir -p /mqshared && mountpoint -q /mqshared || mount /dev/disk/by-label/MQSHARED /mqshared"
run pcmk-a1 "mkdir -p /mqshared/qmgrs /mqshared/log && chown -R mqm:mqm /mqshared"
run pcmk-a1 "su mqm -c '/opt/mqm/bin/crtmqm -md /mqshared/qmgrs -ld /mqshared/log $QM' || su mqm -c '/opt/mqm/bin/dspmq -m $QM'"
run pcmk-a1 "su mqm -c '/opt/mqm/bin/strmqm $QM' || true; printf 'DEFINE LISTENER(L1414) TRPTYPE(TCP) PORT(1414) CONTROL(QMGR) REPLACE\nSTART LISTENER(L1414)\nDEFINE CHANNEL(APP.SVRCONN) CHLTYPE(SVRCONN) TRPTYPE(TCP) MCAUSER('\\''mqm'\\'') HBINT(15) KAINT(15) REPLACE\nALTER QMGR CHLAUTH(DISABLED) CONNAUTH('\\'' '\\'')\nREFRESH SECURITY TYPE(CONNAUTH)\nDEFINE QLOCAL(HA.TEST) DEFPSIST(YES) REPLACE\n' | su mqm -c '/opt/mqm/bin/runmqsc $QM' || true; su mqm -c '/opt/mqm/bin/endmqm -w $QM'"

# 2. Teach the other nodes the QM definition (addmqinf from dspmqinf).
INF=$(run pcmk-a1 "su mqm -c '/opt/mqm/bin/dspmqinf -o command $QM'" | grep '^addmqinf')
run pcmk-a2,pcmk-a3 "su mqm -c '/opt/mqm/bin/${INF#*/opt/mqm/bin/}' || su mqm -c '/opt/mqm/bin/dspmq -m $QM'"
run pcmk-a1 "umount /mqshared"

# 3. systemd unit on every node, disabled - Pacemaker is the only starter.
#    ExecStop uses `endmqm -w` (clean, blocking, controlled stop) so Pacemaker
#    gets the exit code it expects and relocates cleanly. Reconnect across a
#    failover is NOT the stop flag's job: a CRASH never runs ExecStop (the
#    survivor takeover + client auto-reconnect handle it); the only thing that
#    broke reconnect in #64 was the controlled move-BACK to a rebooted node, so
#    the fix is resource stickiness (no auto-failback), set below — not -r, which
#    makes the stop reconnect-friendly but confuses Pacemaker's relocation.
run pcmk_a "printf '[Unit]\nDescription=IBM MQ queue manager $QM (pacemaker-managed)\n[Service]\nType=forking\nUser=mqm\nExecStart=/opt/mqm/bin/strmqm $QM\nExecStop=/opt/mqm/bin/endmqm -w $QM\nTimeoutStartSec=300\n' > /etc/systemd/system/mq-$QM.service && mkdir -p /mqshared && systemctl daemon-reload && systemctl disable mq-$QM.service 2>/dev/null; true"

# 3b. Resource stickiness: after a crash + fence-reboot, the group must NOT
#     auto-fail-back to the recovered node (that controlled move-back runs
#     endmqm and disconnects reconnected clients). Keep it where it landed.
run pcmk-a1 "pcs resource defaults update resource-stickiness=1000 || pcs resource defaults resource-stickiness=1000"

# 4. The resource group: fs -> vip -> qm, in one group (order+colocation).
# mq_fs monitor uses OCF_CHECK_LEVEL=20 (a real read/write probe of the LUN, not
# just the mount table) with on-fail=fence, so a dead SAN under the owner -- the
# mount still present but I/O hanging -- is detected within one interval and the
# node is fenced, instead of the ~12-min blind spot of the default mount check.
run pcmk-a1 "pcs resource status mq_fs >/dev/null 2>&1 || pcs resource create mq_fs ocf:heartbeat:Filesystem device=/dev/disk/by-label/MQSHARED directory=/mqshared fstype=xfs op monitor interval=20s timeout=40s OCF_CHECK_LEVEL=20 on-fail=fence --group mq_group"
run pcmk-a1 "pcs resource status mq_vip >/dev/null 2>&1 || pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip=$VIP cidr_netmask=24 --group mq_group --after mq_fs"
run pcmk-a1 "pcs resource status mq_qm >/dev/null 2>&1 || pcs resource create mq_qm systemd:mq-$QM --group mq_group --after mq_vip"
run pcmk-a1 "pcs status resources"
