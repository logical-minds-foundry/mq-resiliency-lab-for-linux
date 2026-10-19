#!/usr/bin/env bash
# S2 / S3: forced DR to site B, then site A returns (#1666). Stops after the final-state
# snapshot with the poller still running; the resolve + failback are run by hand (marked).
#   drill.sh S2 graceful   drbd-degrade.sh break <A primary> rdqmapp.dr; site A restarted normally
#   drill.sh S3 wan        net-cut.sh down net-wan (site A); site A restarted with the WAN still cut
set -uo pipefail
S="${1:?scen}"; MODE="${2:?graceful|wan}"
SP="$(cd "$(dirname "$0")" && pwd)"
K=$SP/k
R="${MQLAB_REPO:?set MQLAB_REPO to the checkout whose build/ holds the lab inventory}"
cd "$R"
key=~/.vagrant.d/insecure_private_key
SSHO=(-i $key -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=4)
ipof() { case "$1" in rdqm-a1) echo 10.50.0.31;; rdqm-a2) echo 10.50.0.32;; rdqm-a3) echo 10.50.0.33;;
                      rdqm-b1) echo 10.50.0.41;; rdqm-b2) echo 10.50.0.42;; rdqm-b3) echo 10.50.0.43;; esac; }
on() { local n="$1"; shift; timeout 120 ssh "${SSHO[@]}" "vagrant@$(ipof "$n")" "$@"; }
reach() { timeout 6 ssh "${SSHO[@]}" "vagrant@$(ipof "$1")" true 2>/dev/null; }
act() { $K mark "$S" "$*"; "$@" 2>&1 | sed 's/^/    | /'; local rc=${PIPESTATUS[0]}; $K mark "$S" "rc=$rc"; return 0; }
V() { echo "+ virsh -c qemu:///system $*"; virsh -c qemu:///system "$@"; }
site_primary() {  # a|b -> node reporting "HA current location: This node" (retries 60 s)
  local i n
  for i in $(seq 1 12); do
    for n in rdqm-$1{1,2,3}; do
      on "$n" 'sudo /opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1' 2>/dev/null | grep -q 'HA current location: *This node' && { echo "$n"; return 0; }
    done
    sleep 5
  done
  return 1
}
rdqm_on() { on "$1" 'sudo /opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1'; }
put() {  # node prefix tag
  on "$1" "for i in 1 2 3 4 5; do echo $2-\$i-$3; done | sudo -u mqm /opt/mqm/samp/bin/amqsput DR.FORCE.DRILL RDQMAPP"
}
depth() { on "$1" "echo 'DISPLAY QLOCAL(DR.FORCE.DRILL) CURDEPTH' | sudo -u mqm /opt/mqm/bin/runmqsc RDQMAPP" | grep -E 'CURDEPTH|AMQ'; }
count() {  # node -> HEAD/TAIL counts from a non-destructive browse
  local out; out=$(on "$1" 'sudo -u mqm /opt/mqm/samp/bin/amqsbcg DR.FORCE.DRILL RDQMAPP 2>&1')
  echo "$out" >"${OUT_ROOT:?}/$S/amqsbcg-$1-$(date -u +%H%M%S).txt"
  echo "HEAD=$(grep -c "'HEAD-" <<<"$out") TAIL=$(grep -c "'TAIL-" <<<"$out") (browse saved)"
}

H=$("$SP/health.sh"); echo "$H"; grep -q '^HEALTHY' <<<"$H" || { echo "ABORT: not healthy before $S"; exit 1; }
P=$(sed -n 's/^PRIMARY=//p' <<<"$H"); PB=$(site_primary b) || { echo "ABORT: no site-B HA primary"; exit 1; }
echo "site-A HA primary: $P ; site-B HA primary: $PB"
TAG="$S$(date -u +%H%M%S)"
T0=$(date -u +%Y-%m-%dT%H:%M:%S); echo "T0=$T0" | tee "${OUT_ROOT:?}/$S.T0"
$K mark "$S" "T0 scenario start; A primary $P, B primary $PB, tag $TAG"
$K clocks "$S" start
$K rec "$S"
$K poll "$S" start
$K snap "$S" before

# 1. drill queue + HEAD
act on "$P" "echo 'DEFINE QLOCAL(DR.FORCE.DRILL) DEFPSIST(YES) REPLACE' | sudo -u mqm /opt/mqm/bin/runmqsc RDQMAPP"
act put "$P" HEAD "$TAG"
act depth "$P"
for i in $(seq 1 12); do rdqm_on "$P" | grep -q 'DR status: *Normal' && break; sleep 5; done
act rdqm_on "$P"
$K snap "$S" head-put

# 2. break the DR link
if [ "$MODE" = graceful ]; then
  act uv run lab/scripts/drbd-degrade.sh break "$P" rdqmapp.dr
else
  act lab/scripts/net-cut.sh down net-wan rdqm-a1 rdqm-a2 rdqm-a3
fi
sleep 15
act rdqm_on "$P"
$K snap "$S" dr-link-broken

# 3. TAIL
act put "$P" TAIL "$TAG"
act depth "$P"
sleep 5
$K snap "$S" tail-put

# 4. destroy all of site A
$K mark "$S" "virsh destroy lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3"
for n in a1 a2 a3; do V destroy "lab_rdqm-$n" & done; wait
$K mark "$S" "site A destroyed"
sleep 20
$K snap "$S" site-a-destroyed

# 5. forced promote at B (IBM "Switching over to a recovery node if a disaster occurs")
act on "$PB" 'sudo /opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1'
act on "$PB" 'sudo /opt/mqm/bin/rdqmdr -m RDQMAPP -p 2>&1'
act on "$PB" 'sudo -u mqm /opt/mqm/bin/strmqm RDQMAPP 2>&1'
for i in $(seq 1 36); do PB2=$(site_primary b) && rdqm_on "$PB2" | grep -q 'Queue manager status: *Running' && break; sleep 5; done
PB=$(site_primary b); $K mark "$S" "site-B HA primary after promote: $PB"
act rdqm_on "$PB"
$K snap "$S" b-promoted

# 6. count at B + e2e
act count "$PB"
act uv run mqlab qm e2e rdqm-rhel
$K snap "$S" b-verified

# 7. start site A, no intervention
if [ "$MODE" = graceful ]; then
  $K mark "$S" "virsh start lab_rdqm-a1 lab_rdqm-a2 lab_rdqm-a3"
  for n in a1 a2 a3; do V start "lab_rdqm-$n" & done; wait
else
  # net-cut.sh refuses a non-running domain, and a fresh boot brings every link up, so the
  # WAN link is cut by hand on a paused boot: start --paused, domif-setlink down, resume.
  for n in a1 a2 a3; do
    act V start --paused "lab_rdqm-$n"
    mac=$(virsh -c qemu:///system domiflist "lab_rdqm-$n" | awk '$3=="net-wan"{print $5}')
    act V domif-setlink "lab_rdqm-$n" "$mac" down
    act V domif-getlink "lab_rdqm-$n" "$mac"
  done
  for n in a1 a2 a3; do act V resume "lab_rdqm-$n"; done
fi
$K mark "$S" "site A started ($MODE)"
for n in rdqm-a1 rdqm-a2 rdqm-a3; do until reach "$n"; do sleep 2; done; done
$K mark "$S" "site A ssh-reachable"
$K rec "$S" rdqm-a1 rdqm-a2 rdqm-a3
$K snap "$S" site-a-booted
if [ "$MODE" = wan ]; then
  sleep 60;  $K snap "$S" isolated-plus-60s
  sleep 60;  $K snap "$S" isolated-plus-120s
  sleep 180; $K snap "$S" isolated-plus-300s
  act lab/scripts/net-cut.sh up net-wan rdqm-a1 rdqm-a2 rdqm-a3
  $K mark "$S" "WAN restored; observing 10 min"
fi
sleep 60;  $K snap "$S" observe-plus-60s
sleep 120; $K snap "$S" observe-plus-180s
sleep 180; $K snap "$S" observe-plus-360s
sleep 240; $K snap "$S" observe-plus-600s
for n in rdqm-a1 rdqm-a2 rdqm-a3 rdqm-b1 rdqm-b2 rdqm-b3; do act rdqm_on "$n"; done
$K mark "$S" "final state recorded; poller still running; resolve by hand next"
echo PART1-DONE
