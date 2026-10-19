#!/usr/bin/env bash
# S4: whole-site cold restart of site A, DR link up (#1666)
set -uo pipefail
K="$(cd "$(dirname "$0")" && pwd)"/k
S=S4
V() { echo "+ virsh -c qemu:///system $*"; virsh -c qemu:///system "$@"; }
key=~/.vagrant.d/insecure_private_key
reach() { timeout 6 ssh -i $key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=4 vagrant@"$1" true 2>/dev/null; }

T0=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T0=$T0"
$K mark $S "T0 scenario start"
$K clocks $S start
$K rec $S
$K poll $S start
$K snap $S before
$K mark $S "virsh destroy lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3"
for n in a1 a2 a3; do V destroy lab_rdqm-$n & done; wait
$K mark $S "destroy done; waiting 60 s"
sleep 60
$K snap $S site-a-down
$K mark $S "virsh start lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3"
for n in a1 a2 a3; do V start lab_rdqm-$n & done; wait
$K mark $S "start done"
for ipn in 31 32 33; do until reach 10.50.0.$ipn; do sleep 2; done; done
$K mark $S "site A nodes ssh-reachable"
$K rec $S rdqm-a1 rdqm-a2 rdqm-a3
$K snap $S site-a-booted
sleep 150
$K snap $S plus-150s
sleep 150
$K snap $S after-5min
$K poll $S stop
$K clocks $S end
T1=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T1=$T1"
$K mark $S "T1 scenario end"
$K collect $S "$T0" "$T1"
echo "$T0 $T1" > ${OUT_ROOT:?}/$S/window
echo DONE
