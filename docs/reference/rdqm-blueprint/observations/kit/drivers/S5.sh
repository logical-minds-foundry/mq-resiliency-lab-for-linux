#!/usr/bin/env bash
# S5: brief HA partition of the site-A HA primary (~15 s), DR link up (#1666 addendum).
#   S5.sh S5 <net> [secs]   net = the network carrying HA DRBD replication (from drbdsetup show)
set -uo pipefail
S="${1:?scen}"; NET="${2:?net}"; SECS="${3:-15}"
SP="$(cd "$(dirname "$0")" && pwd)"
K=$SP/k
R="${MQLAB_REPO:?set MQLAB_REPO to the checkout whose build/ holds the lab inventory}"
cd "$R"
act() { $K mark "$S" "$*"; "$@" 2>&1 | sed 's/^/    | /'; local rc=${PIPESTATUS[0]}; $K mark "$S" "rc=$rc"; return 0; }

H=$("$SP/health.sh"); echo "$H"; grep -q '^HEALTHY' <<<"$H" || { echo "ABORT: not healthy before $S"; exit 1; }
P=$(sed -n 's/^PRIMARY=//p' <<<"$H"); echo "site-A HA primary: $P"
T0=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T0=$T0"
$K mark "$S" "T0 scenario start; site-A HA primary $P; cutting $NET on $P for ${SECS}s"
$K clocks "$S" start
$K rec "$S"
$K poll "$S" start
$K snap "$S" before
act lab/scripts/net-cut.sh down "$NET" "$P"
sleep "$SECS"
act lab/scripts/net-cut.sh up "$NET" "$P"
$K snap "$S" restored
sleep 60;  $K snap "$S" plus-60s
sleep 120; $K snap "$S" plus-180s
sleep 120; $K snap "$S" plus-300s
H=$("$SP/health.sh"); echo "$H" | sed 's/^/    post-health | /'; $K mark "$S" "post-health: $(tail -1 <<<"$H") $(grep -E '^(HEALTHY|UNHEALTHY)' <<<"$H")"
$K poll "$S" stop
$K clocks "$S" end
T1=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T1=$T1"
$K mark "$S" "T1 scenario end"
$K collect "$S" "$T0" "$T1"
echo "$T0 $T1" >"${OUT_ROOT:?}/$S/window"
echo DONE
