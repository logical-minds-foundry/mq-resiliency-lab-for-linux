#!/usr/bin/env bash
# S1 / S1b: ungraceful HA failover at the DR primary site while the DR link is down (#1666).
#   S1.sh S1  wan        WAN cut at site A (net-cut.sh down net-wan rdqm-a1 rdqm-a2 rdqm-a3)
#   S1.sh S1b graceful   drbd-degrade.sh break <site-A HA primary> rdqmapp.dr
set -uo pipefail
S="${1:?scen}"; MODE="${2:?wan|graceful}"
SP="$(cd "$(dirname "$0")" && pwd)"
K=$SP/k
R="${MQLAB_REPO:?set MQLAB_REPO to the checkout whose build/ holds the lab inventory}"
cd "$R"
V() { echo "+ virsh -c qemu:///system $*"; virsh -c qemu:///system "$@"; }
key=~/.vagrant.d/insecure_private_key
reach() { timeout 6 ssh -i $key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=4 vagrant@"$1" true 2>/dev/null; }
ipof() { case "$1" in rdqm-a1) echo 10.50.0.31;; rdqm-a2) echo 10.50.0.32;; rdqm-a3) echo 10.50.0.33;; esac; }
act() { $K mark "$S" "$*"; "$@" 2>&1 | sed 's/^/    | /'; local rc=${PIPESTATUS[0]}; $K mark "$S" "rc=$rc"; return 0; }

H=$("$SP/health.sh"); echo "$H"; grep -q '^HEALTHY' <<<"$H" || { echo "ABORT: not healthy before $S"; exit 1; }
P=$(sed -n 's/^PRIMARY=//p' <<<"$H"); echo "site-A HA primary: $P"
T0=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T0=$T0"
$K mark "$S" "T0 scenario start; pre-health: site-A HA primary $P"
$K clocks "$S" start
$K rec "$S"
$K poll "$S" start
$K snap "$S" before
if [ "$MODE" = wan ]; then
  act lab/scripts/net-cut.sh down net-wan rdqm-a1 rdqm-a2 rdqm-a3
else
  act uv run lab/scripts/drbd-degrade.sh break "$P" rdqmapp.dr
fi
sleep 30
$K snap "$S" dr-link-down
act V destroy "lab_$P"
sleep 30;  $K snap "$S" plus-30s
sleep 60;  $K snap "$S" plus-90s
sleep 90;  $K snap "$S" plus-180s
others=(); for n in rdqm-a1 rdqm-a2 rdqm-a3; do [ "$n" = "$P" ] || others+=("$n"); done
if [ "$MODE" = wan ]; then
  act lab/scripts/net-cut.sh up net-wan "${others[@]}"
  sleep 20; $K snap "$S" wan-restored
fi
act V start "lab_$P"
until reach "$(ipof "$P")"; do sleep 2; done
$K mark "$S" "$P ssh-reachable"
$K rec "$S" "$P"
sleep 60;  $K snap "$S" restarted-plus-60s
sleep 120; $K snap "$S" restarted-plus-180s
H=$("$SP/health.sh"); echo "$H" | sed 's/^/    post-health | /'; $K mark "$S" "post-health: $(tail -1 <<<"$H") $(grep -E '^(HEALTHY|UNHEALTHY)' <<<"$H")"
$K poll "$S" stop
$K clocks "$S" end
T1=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T1=$T1"
$K mark "$S" "T1 scenario end"
$K collect "$S" "$T0" "$T1"
echo "$T0 $T1" >"${OUT_ROOT:?}/$S/window"
echo DONE
