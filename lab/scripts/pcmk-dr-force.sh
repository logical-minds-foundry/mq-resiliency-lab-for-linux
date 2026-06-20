#!/usr/bin/env bash
# lab/scripts/pcmk-dr-force.sh — FORCED cross-site cutover. The live site is
# GONE (abrupt full-site loss), so unlike pcmk-dr-cutover.sh there is no
# quiesce/demote of the old primary: the surviving secondary force-promotes
# from whatever DRBD protocol-A (async) had shipped. Any writes committed at the
# dead primary but not yet replicated are LOST -- the RPO>0 the framework
# quantifies. Then it re-exports the LUN and starts the QM on the peer cluster.
#
# Direction: a2b (site A dead -> promote B) | b2a (site B dead -> promote A).
set -euo pipefail
DIR="${1:-a2b}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

if [ "$DIR" = a2b ]; then
  TO_SAN=san-b; TO_PCMK=pcmk_b; TO_PORTAL=10.40.2.6; TO_VIP=10.10.2.200
  TO_NODES="pcmk-b1 pcmk-b2 pcmk-b3"; TO_IQNS="pcmk-b1 pcmk-b2 pcmk-b3"
else
  TO_SAN=san-a; TO_PCMK=pcmk_a; TO_PORTAL=10.40.1.5; TO_VIP=10.10.1.200
  TO_NODES="pcmk-a1 pcmk-a2 pcmk-a3"; TO_IQNS="pcmk-a1 pcmk-a2 pcmk-a3"
fi

echo "=== 1. FORCE-PROMOTE DRBD on $TO_SAN (old primary gone; accept the lost tail) ==="
run "$TO_SAN" "drbdadm primary --force mqlun && drbdadm status mqlun"

echo "=== 2. EXPORT the LUN at $TO_SAN (LIO over /dev/drbd0) ==="
run "$TO_SAN" "targetcli /backstores/block create name=mqlun dev=/dev/drbd0 || true
  targetcli /iscsi create iqn.2026-06.lab.mq:${TO_SAN}.lun0 || true
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/luns create /backstores/block/mqlun || true
  for n in $TO_IQNS; do targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/acls create iqn.2026-06.lab.mq:\$n || true; done
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/portals delete 0.0.0.0 3260 2>/dev/null || true
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/portals create ${TO_PORTAL} 3260 || true
  targetcli saveconfig"

echo "=== 3. ATTACH storage at the peer cluster ($TO_PCMK) ==="
run "$TO_PCMK" "iscsiadm -m discovery -t sendtargets -p ${TO_PORTAL}:3260 >/dev/null 2>&1 || true
  iscsiadm -m node -T iqn.2026-06.lab.mq:${TO_SAN}.lun0 -p ${TO_PORTAL}:3260 --login 2>/dev/null || iscsiadm -m session --rescan
  sleep 3; ls -l /dev/disk/by-label/MQSHARED"

echo "=== 4. START the QM resource group on the peer cluster ($TO_PCMK -> VIP $TO_VIP) ==="
FIRST=$(echo $TO_NODES | awk '{print $1}')
run "$FIRST" "if ! pcs resource status mq_group >/dev/null 2>&1; then
    pcs resource create mq_fs ocf:heartbeat:Filesystem device=/dev/disk/by-label/MQSHARED directory=/mqshared fstype=xfs op monitor interval=20s timeout=40s OCF_CHECK_LEVEL=20 on-fail=fence --group mq_group
    pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip=${TO_VIP} cidr_netmask=24 --group mq_group --after mq_fs
    pcs resource create mq_qm systemd:mq-QMPCMK --group mq_group --after mq_vip
  else pcs resource enable mq_group; fi"
sleep 8
run "$FIRST" "pcs status resources | tail -4"

# Fail loud: the cutover is complete ONLY if the QM is actually Running on the
# peer. The first version masked every step with `|| true` and printed success
# unconditionally -- so an empty ansible inventory (the worktree had no
# build/work/inventory.ini) silently did nothing yet still reported "complete".
if run "$TO_PCMK" "su mqm -c '/opt/mqm/bin/dspmq -m QMPCMK'" 2>/dev/null | grep -q 'STATUS(Running)'; then
  echo "=== forced cutover $DIR complete; DR site is now $TO_PCMK, VIP $TO_VIP (QMPCMK Running) ==="
else
  echo "ERROR: forced cutover $DIR did NOT bring QMPCMK up on $TO_PCMK" >&2
  exit 1
fi
