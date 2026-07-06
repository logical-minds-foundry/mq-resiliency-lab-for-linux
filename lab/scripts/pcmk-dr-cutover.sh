#!/usr/bin/env bash
# lab/scripts/pcmk-dr-cutover.sh - controlled cross-site cutover for the
# Pacemaker/SAN arm. The hand-built equivalent of RDQM's `rdqmdr -s`/`-p`
# (two commands) - count the steps here for the Phase E ledger.
#
# Direction: cutover a2b | failback b2a. Each direction is the mirror.
# The paved-path discipline (spec 8.5): quiesce -> confirm replication
# caught up -> flip DRBD -> bring the peer site's storage+cluster up.
set -euo pipefail
DIR="${1:-a2b}"
QM="${2:?usage: pcmk-dr-cutover.sh [a2b|b2a] <qm-name>}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

# TO_VIP / TO_VIP_EXT are raw IPs on purpose: they define the pacemaker floating-IP
# resources (pcs resource create ... IPaddr2 ip=...), NOT client CONNAMEs — an FQDN
# there would fail the cutover, so do NOT FQDN-ify them (#494). TO_PORTAL is an iSCSI
# fabric address, kept raw per the minimal-dependency rule.
if [ "$DIR" = a2b ]; then
  FROM_SAN=san-a; TO_SAN=san-b; FROM_PCMK=pcmk_a; TO_PCMK=pcmk_b
  TO_PORTAL=10.40.2.6; TO_VIP=10.10.2.200; TO_VIP_EXT=10.60.0.20
  TO_NODES="pcmk-b1 pcmk-b2 pcmk-b3"; TO_IQNS="pcmk-b1 pcmk-b2 pcmk-b3"
else
  FROM_SAN=san-b; TO_SAN=san-a; FROM_PCMK=pcmk_b; TO_PCMK=pcmk_a
  TO_PORTAL=10.40.1.5; TO_VIP=10.10.1.200; TO_VIP_EXT=10.60.0.10
  TO_NODES="pcmk-a1 pcmk-a2 pcmk-a3"; TO_IQNS="pcmk-a1 pcmk-a2 pcmk-a3"
fi

echo "=== 1. QUIESCE the live site ($FROM_PCMK): stop the group, unmount ==="
run "$FROM_PCMK" "pcs cluster standby --all" || true
run "$FROM_PCMK" "for i in \$(seq 1 30); do mountpoint -q /mqshared || break; sleep 2; done; mountpoint /mqshared 2>&1 || true"

echo "=== 2. CONFIRM replication caught up, then demote DRBD on $FROM_SAN ==="
run "$FROM_SAN" "drbdadm status mqlun"
run "$FROM_SAN" "umount /mqshared 2>/dev/null; targetctl clear 2>/dev/null; drbdadm secondary mqlun && echo demoted"

echo "=== 3. PROMOTE DRBD on $TO_SAN (now the primary copy) ==="
run "$TO_SAN" "drbdadm primary mqlun && drbdadm status mqlun"

echo "=== 4. EXPORT the LUN at $TO_SAN (LIO over /dev/drbd0, now accessible) ==="
run "$TO_SAN" "targetcli /backstores/block create name=mqlun dev=/dev/drbd0 || true
  targetcli /iscsi create iqn.2026-06.lab.mq:${TO_SAN}.lun0 || true
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/luns create /backstores/block/mqlun || true
  for n in $TO_IQNS; do targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/acls create iqn.2026-06.lab.mq:\$n || true; done
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/portals delete 0.0.0.0 3260 2>/dev/null || true
  targetcli /iscsi/iqn.2026-06.lab.mq:${TO_SAN}.lun0/tpg1/portals create ${TO_PORTAL} 3260 || true
  targetcli saveconfig"

echo "=== 5. ATTACH storage at the peer cluster ($TO_PCMK) ==="
run "$TO_PCMK" "iscsiadm -m discovery -t sendtargets -p ${TO_PORTAL}:3260 >/dev/null 2>&1 || true
  iscsiadm -m node -T iqn.2026-06.lab.mq:${TO_SAN}.lun0 -p ${TO_PORTAL}:3260 --login 2>/dev/null || iscsiadm -m session --rescan
  sleep 3; ls -l /dev/disk/by-label/MQSHARED"

echo "=== 6. START the QM resource group on the peer cluster ==="
FIRST=$(echo $TO_NODES | awk '{print $1}')
run "$FIRST" "pcs cluster unstandby --all 2>/dev/null || true
  if ! pcs resource status mq_group >/dev/null 2>&1; then
    pcs resource create mq_fs ocf:heartbeat:Filesystem device=/dev/disk/by-label/MQSHARED directory=/mqshared fstype=xfs op monitor interval=30s OCF_CHECK_LEVEL=20 on-fail=fence --group mq_group
    pcs resource create mq_vip ocf:heartbeat:IPaddr2 ip=${TO_VIP} cidr_netmask=24 --group mq_group --after mq_fs
    pcs resource create mq_vip_ext ocf:heartbeat:IPaddr2 ip=${TO_VIP_EXT} cidr_netmask=24 --group mq_group --after mq_vip
    pcs resource create mq_qm systemd:mq-${QM} --group mq_group --after mq_vip_ext
  else
    pcs resource enable mq_group
  fi"
sleep 8
run "$FIRST" "pcs status resources | tail -3"
echo "=== cutover $DIR complete; live site is now $TO_PCMK, VIP $TO_VIP / ext $TO_VIP_EXT ==="
