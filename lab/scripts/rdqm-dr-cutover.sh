#!/usr/bin/env bash
# lab/scripts/rdqm-dr-cutover.sh - controlled cross-site cutover/failback for the RDQM arm.
# RDQM wraps the work in two rdqmdr commands (vs the Pacemaker arm's multi-step
# pcmk-dr-cutover.sh): quiesce the live primary, promote the recovery primary, then re-add
# the single floating IP at the new live site. The QM's objects + messages travel with DR
# replication, so the recovery site needs no content re-apply — only its VIP.
#
# Proven in the Phase-C drill (docs/reports/2026-06-06-phase-c-rdqm-findings.md): ~69 s
# cutover / ~104 s failback at RPO 0 both directions.
#
# Direction: a2b (cutover, site A -> B) | b2a (failback, B -> A). Each direction mirrors.
#
# HARDENING (#294, from the #288 drill — see docs/reports/2026-07-31-rdqm-dr-cutover-hardening-findings.md):
#   1. HA-primary discovery. rdqmdr MUST run on the site's CURRENT HA primary, which RDQM
#      floats across a1/a2/a3 (or b1/b2/b3) — not necessarily node-1 (else AMQ3705E "not
#      issued on the HA primary node"). We resolve it from rdqmstatus "HA current location"
#      at BOTH sites, and re-resolve the TO site after the promote so the verify/poll follows
#      the QM wherever HA placed it (the old single-shot node-1 poll missed a QM on a2/a3).
#   2. Pre-cut DR-in-sync gate. We refuse to promote until the live site reports DR status
#      Normal (bounded by DR_SYNC_TIMEOUT), so an incomplete recovery copy is never activated.
#   3. Post-promote HA-settle wait. After rdqmdr -p the QM HA-bounces under Pacemaker; on a
#      slow host this can trip RDQM's (non-tunable) start/monitor timing and set
#      "HA blocked location: All nodes" — a timing artifact, not a data fault. We wait
#      (bounded by HA_SETTLE_TIMEOUT) and surface it loudly with the proven recovery, rather
#      than emit a confusing mid-bounce status. #294 finding 3 first saw this under macOS-era
#      TCG emulation, but on an x86 host these RHEL arms run native KVM/host-passthrough (per
#      src/mqlab/platforms.py _provider), so it is a slow-host timing flake (emulation-or-not),
#      not an emulation-specific one — and may be a macOS-arm64-era artifact to re-verify under
#      cloud KVM (#870). The cutover capability itself is proven at ~69 s on real timing.
#
# Run via `uv run` / the mqlab venv (needs ansible on PATH) — or, preferred, via
# `mqlab dr cutover|failback <stack>` (#867), which shells this script from inside that
# venv so ansible is on PATH without a manual `uv run`.
set -euo pipefail
DIR="${1:-a2b}"
QM="${2:-RDQMAPP}"
# Bounded gates (seconds), overridable for a slower/faster host (emulated or native KVM).
DR_SYNC_TIMEOUT="${DR_SYNC_TIMEOUT:-300}"   # max wait for pre-cut DR status Normal
HA_SETTLE_TIMEOUT="${HA_SETTLE_TIMEOUT:-180}"  # max wait for the post-promote HA bounce
# RPO-0 message-survival drill (#867, from #294's deferred item). Opt-in via RPO0_DRILL=1
# (the `mqlab dr … --rpo0-drill` flag sets it). Seed a uniquely-tagged PERSISTENT message on
# the live site BEFORE the cut; the pre-cut DR-in-sync gate (finding 2) then guarantees it is
# replicated to the recovery site; after the cut, retrieve it at the new live site and assert
# the tag survived — RPO 0 (no message loss). The message rides RDQM DR block replication like
# the QM's own data, so no re-apply is needed; a miss fails the run loud under `set -e`.
RPO0_DRILL="${RPO0_DRILL:-0}"
RPO0_DRILL_QUEUE="${RPO0_DRILL_QUEUE:-DR.RPO0.DRILL}"
RPO0_TOKEN=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

# Capture rdqmstatus text for $QM on a node, tolerant of a transient blip (returns "" on
# failure so callers can retry rather than abort under `set -e`).
rdqmstatus_on() {  # $1=node
  ansible "$1" -b -m shell -a "/opt/mqm/bin/rdqmstatus -m $QM" 2>/dev/null || true
}

# Extract a single-line rdqmstatus field value ("Label:   value") from captured text.
# Uses a here-string (no pipe) so an early awk exit cannot SIGPIPE an upstream producer
# under pipefail (the failure mode documented in rdqm-qm-create.sh active_a_node).
rdqm_field() {  # $1=field label (literal), $2=rdqmstatus text
  awk -v lbl="$1" 'index($0, lbl ":") == 1 {
      v = substr($0, length(lbl) + 2); sub(/^[[:space:]]+/, "", v); sub(/[[:space:]]+$/, "", v);
      print v; exit
    }' <<<"$2"
}

# The RDQM floating IPs are declared once in lab/topology.yaml (rdqm-rhel qm.vip /
# qm.vip_b) — the single source of truth. Read them here rather than duplicating
# literals in this script (epic #39, #540).
rdqm_vip() {
  python3 -c "import yaml; print(yaml.safe_load(open('$SCRIPT_DIR/../topology.yaml'))['stacks']['rdqm-rhel']['qm']['$1'])"
}

# Resolve the HA primary node of a site's RDQM group. RDQM floats the QM's HA role across the
# three group nodes, so rdqmdr must be issued on whichever node currently holds it (finding 1;
# else AMQ3705E). rdqmstatus' local block reports "HA current location: This node" on the
# primary itself, or the primary's node name on a secondary — so querying ANY reachable group
# node resolves the primary in one shot. This works on the DR-secondary site too (its QM is
# Ended, but the HA primary is still reported). Retry briefly so a mid-relocation blip is not
# misread as a headless group; fail loud if none settles.
ha_primary() {  # $1..$N = candidate nodes of the site group -> prints the primary node name
  local nodes=("$@")
  local n out loc i
  for i in $(seq 1 30); do
    for n in "${nodes[@]}"; do
      out="$(rdqmstatus_on "$n")"
      loc="$(rdqm_field "HA current location" "$out")"
      [ -z "$loc" ] && continue
      if [ "$loc" = "This node" ]; then echo "$n"; else echo "$loc"; fi
      return 0
    done
    sleep 5
  done
  echo "ERROR: could not resolve the HA primary for group [${nodes[*]}] running $QM" >&2
  return 1
}

# Finding 2 — pre-cut DR-in-sync gate. The proven paved path confirms replication has caught
# up, THEN cuts; promoting a DR secondary whose sync has not converged risks activating an
# incomplete recovery copy. On the live (DR-primary) site's HA primary, rdqmstatus reports
# "DR status: Normal" once in-sync (other states: "Synchronization in progress",
# "Remote unavailable", "Partitioned", "Inactive", ...). Poll up to DR_SYNC_TIMEOUT; fail loud.
confirm_dr_in_sync() {  # $1 = live-site HA primary node
  local node="$1"
  local out status waited=0 interval=10
  while :; do
    out="$(rdqmstatus_on "$node")"
    status="$(rdqm_field "DR status" "$out")"
    if [ "$status" = "Normal" ]; then
      echo "=== DR in-sync (DR status: Normal) on $node — safe to cut ==="
      return 0
    fi
    if [ "$waited" -ge "$DR_SYNC_TIMEOUT" ]; then
      echo "ERROR: DR not in-sync after ${DR_SYNC_TIMEOUT}s (last DR status: ${status:-unknown} on $node)." >&2
      echo "ERROR: refusing to cut — promoting now risks activating an incomplete recovery copy." >&2
      return 1
    fi
    echo "=== DR status: ${status:-unknown} on $node — waiting for Normal (${waited}s/${DR_SYNC_TIMEOUT}s) ===" >&2
    sleep "$interval"; waited=$((waited + interval))
  done
}

# Finding 3 — post-promote HA-settle wait. After rdqmdr -p the QM HA-bounces under Pacemaker.
# Poll the new primary until the QM is Running with no blocked HA location. Under slow-host
# timing (macOS-era TCG emulation, or any slow host — on x86 these arms run native KVM) the
# bounce can trip RDQM's start/monitor timing and set "HA blocked location: All
# nodes" (a timing artifact — the QM logs only warnings + a controlled end, no data fault). We
# do NOT auto-remediate with an unproven command: the proven recovery (see the #288 report) is
# a rdqmdr re-flip on the CORRECT HA primary, which the finding-1 discovery above now makes
# trivially correct to run by hand. On timeout we warn loudly and continue rather than emit a
# confusing mid-bounce status; the cutover capability itself is proven at ~69 s on real timing.
wait_ha_settle() {  # $1 = new (TO-site) HA primary node
  local node="$1"
  local out qmstat blocked waited=0 interval=10
  while :; do
    out="$(rdqmstatus_on "$node")"
    qmstat="$(rdqm_field "Queue manager status" "$out")"
    blocked="$(rdqm_field "HA blocked location" "$out")"
    if [ "$qmstat" = "Running" ] && [ "$blocked" = "None" ]; then
      echo "=== HA settled: $QM Running on $node, HA blocked location None ==="
      return 0
    fi
    if [ "$waited" -ge "$HA_SETTLE_TIMEOUT" ]; then
      echo "WARNING: $QM did not reach a clean HA-settled state on $node after ${HA_SETTLE_TIMEOUT}s" >&2
      echo "WARNING:   (Queue manager status: ${qmstat:-unknown}, HA blocked location: ${blocked:-unknown})." >&2
      if [ "$blocked" = "All nodes" ]; then
        echo "WARNING: this is the known slow-host start/monitor timing flake (#294 finding 3): the cutover" >&2
        echo "WARNING:   capability is proven (~69 s in the Phase-C drill), the QM logs only warnings + a" >&2
        echo "WARNING:   controlled end, and the node has free RAM. Recover with a rdqmdr re-flip on the" >&2
        echo "WARNING:   CURRENT HA primary of each site (rdqmstatus 'HA current location'), per the #288 report." >&2
      fi
      echo "WARNING: continuing — verify manually with 'rdqmstatus -m $QM'." >&2
      return 0
    fi
    echo "=== waiting for HA to settle on $node (QM: ${qmstat:-?}, blocked: ${blocked:-?}) (${waited}s/${HA_SETTLE_TIMEOUT}s) ===" >&2
    sleep "$interval"; waited=$((waited + interval))
  done
}

# --- RPO-0 message-survival drill (#867) -----------------------------------------------------
# Run a shell command AS THE mqm user on a node — MQ control commands (runmqsc) and the shipped
# samples (amqsput/amqsget) need MQ authority. --become-user layers onto the site run()'s -b.
mqm_run() {  # $1=node ; $2=shell command
  ansible "$1" -b --become-user mqm -m shell -a "$2"
}

# Seed one uniquely-tagged PERSISTENT message on the live site's HA primary, BEFORE the cut.
# Defines the drill queue idempotently with DEFPSIST(YES) so amqsput's message is persistent
# (survives the QM restart the cut entails), then puts the token. The subsequent DR-in-sync
# gate (finding 2) waits for "DR status: Normal", which guarantees this message reached the
# recovery copy before we promote it.
rpo0_seed() {  # $1 = live (FROM) HA primary node ; sets RPO0_TOKEN
  RPO0_TOKEN="RPO0-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
  echo "=== RPO-0 drill: seeding persistent message '$RPO0_TOKEN' into $RPO0_DRILL_QUEUE on $1 ==="
  mqm_run "$1" "echo 'DEFINE QLOCAL($RPO0_DRILL_QUEUE) DEFPSIST(YES) REPLACE' | /opt/mqm/bin/runmqsc $QM"
  mqm_run "$1" "printf '%s\n' '$RPO0_TOKEN' | /opt/mqm/samp/bin/amqsput $RPO0_DRILL_QUEUE $QM"
}

# After the cut, retrieve from the drill queue at the NEW live site and assert the seeded token
# survived — RPO 0. amqsget destructively drains the queue and prints each message body; a hit
# proves no loss, a miss fails loud (RPO-0 violated) and — under set -e — aborts the run.
rpo0_verify() {  # $1 = new (TO) HA primary node
  local out
  echo "=== RPO-0 drill: retrieving from $RPO0_DRILL_QUEUE on $1 (expect '$RPO0_TOKEN') ==="
  out="$(mqm_run "$1" "/opt/mqm/samp/bin/amqsget $RPO0_DRILL_QUEUE $QM")"
  echo "$out"
  if grep -qF "$RPO0_TOKEN" <<<"$out"; then
    echo "=== RPO-0 VERIFIED: the seeded message survived the $DIR cutover — no message loss ==="
  else
    echo "ERROR: RPO-0 VIOLATED — seeded message '$RPO0_TOKEN' was not retrievable at $1 after the cut." >&2
    return 1
  fi
}

if [ "$DIR" = a2b ]; then
  FROM_NODES=(rdqm-a1 rdqm-a2 rdqm-a3); TO_NODES=(rdqm-b1 rdqm-b2 rdqm-b3); TO_VIP="$(rdqm_vip vip_b)"
elif [ "$DIR" = b2a ]; then
  FROM_NODES=(rdqm-b1 rdqm-b2 rdqm-b3); TO_NODES=(rdqm-a1 rdqm-a2 rdqm-a3); TO_VIP="$(rdqm_vip vip)"
else
  echo "usage: rdqm-dr-cutover.sh [a2b|b2a] [QM=RDQMAPP]" >&2
  exit 2
fi
[ -n "$TO_VIP" ] || { echo "could not resolve TO_VIP from lab/topology.yaml (rdqm-rhel qm)" >&2; exit 1; }

echo "=== 0. Discover the current HA primary at each site (finding 1) ==="
FROM_PRIMARY="$(ha_primary "${FROM_NODES[@]}")"
TO_PRIMARY="$(ha_primary "${TO_NODES[@]}")"
echo "    live (FROM) HA primary: $FROM_PRIMARY ; recovery (TO) HA primary: $TO_PRIMARY"

if [ "$RPO0_DRILL" = 1 ]; then
  echo "=== RPO-0 drill enabled (#867): seed a persistent message now, assert its survival after the cut ==="
  rpo0_seed "$FROM_PRIMARY"
fi

echo "=== 1. Confirm DR is in-sync before cutting (finding 2) ==="
confirm_dr_in_sync "$FROM_PRIMARY"

echo "=== 2. Make the live site ($FROM_PRIMARY) the DR secondary ==="
run "$FROM_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -s"

echo "=== 3. Promote the recovery site ($TO_PRIMARY) to DR primary ==="
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmdr -m $QM -p"

echo "=== 4. Wait for the QM's HA to settle at the new primary site (finding 3) ==="
# Re-resolve the TO-site HA primary: the promote bounce may have relocated the QM within the
# group, and the verify/poll must follow it (finding 1, second half).
TO_PRIMARY="$(ha_primary "${TO_NODES[@]}")"
wait_ha_settle "$TO_PRIMARY"

echo "=== 5. Re-add the floating IP at the new live site ($TO_VIP on $TO_PRIMARY) ==="
IFACE=$(ansible "$TO_PRIMARY" -m shell -a "ip -br addr | grep ${TO_VIP%.*}\\. | cut -d' ' -f1" | tail -1)
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmint -m $QM -a -f $TO_VIP -l $IFACE || true"

echo "=== 6. Verify ==="
run "$TO_PRIMARY" "/opt/mqm/bin/rdqmstatus -m $QM"

if [ "$RPO0_DRILL" = 1 ]; then
  echo "=== 7. RPO-0 assertion: retrieve the seeded message at the new live site (finding: #294 drill) ==="
  rpo0_verify "$TO_PRIMARY"
fi
echo "=== cutover $DIR complete: $QM now live at $TO_PRIMARY via $TO_VIP ==="
