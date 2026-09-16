#!/usr/bin/env bash
# lab/scripts/net-latency.sh {set <delay>|clear|show} — inject a symmetric WAN
# latency on the cross-region plane (virbr-wan) ONLY, via `tc netem` delay (#1105).
#
# ATTACH POINT (determined empirically, epic #227 / plan Task 6):
#   a root `netem` qdisc on EACH tap device enslaved to virbr-wan.
#
# Why not the bridge's own root qdisc? On an isolated libvirt bridge, guest<->guest
# traffic is L2-switched directly between tap ports and NEVER traverses the bridge
# device's own root qdisc — so `netem` on virbr-wan itself has ZERO effect on
# inter-guest traffic. Verified in a veth/netns sandbox mirroring two guests on one
# bridge: bridge-root `delay 10ms` left guest<->guest RTT at ~0.04 ms (unchanged),
# whereas an egress `delay 10ms` on EVERY tap delayed each one-way hop exactly once,
# yielding a SYMMETRIC round-trip of ~20 ms in BOTH directions (a->b 20.1 ms,
# b->a 20.1 ms). A single tap gives ~10 ms (one-way delayed once). Hence: one-way
# delay D on every tap => RTT ~= 2D, symmetric.
#
# The HA / heartbeat planes (virbr-hb-a, virbr-hb-b) are never enumerated (we only
# ever list taps whose master is virbr-wan) and are therefore never shaped —
# intra-group Native HA replication is always same-site and must stay unshaped.
#
# Runs `tc`/`ip` under sudo when not already root (mqlab runs as the unprivileged
# lab user on the libvirt host; tc needs CAP_NET_ADMIN).
set -euo pipefail

BRIDGE=virbr-wan

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO=sudo

die() {
  echo "net-latency: $*" >&2
  exit 1
}

usage() {
  echo "usage: net-latency.sh {set <delay>|clear|show}" >&2
  exit 2
}

require_bridge() {
  ip link show "$BRIDGE" >/dev/null 2>&1 \
    || die "bridge $BRIDGE not found (is the lab WAN network up?)"
}

# The guest-facing tap devices enslaved to virbr-wan. `master $BRIDGE` scopes the
# query to this bridge alone, so the heartbeat bridges are structurally excluded.
wan_taps() {
  local taps=()
  mapfile -t taps < <(ip -o link show master "$BRIDGE" | awk -F': ' '{print $2}' | cut -d'@' -f1)
  printf '%s\n' "${taps[@]}"
}

main() {
  local action="${1:-}"
  case "$action" in
    set)
      [ "$#" -eq 2 ] || usage
      local delay="$2" tap found=0
      require_bridge
      while read -r tap; do
        [ -n "$tap" ] || continue
        found=1
        echo "+ tc qdisc replace dev $tap root netem delay $delay"
        $SUDO tc qdisc replace dev "$tap" root netem delay "$delay"
      done < <(wan_taps)
      [ "$found" -eq 1 ] \
        || die "no guest taps attached to $BRIDGE (are the WAN-connected VMs up?)"
      echo "netem: one-way delay $delay on every $BRIDGE tap (RTT ~= 2 x $delay)"
      ;;
    clear)
      local tap
      require_bridge
      while read -r tap; do
        [ -n "$tap" ] || continue
        echo "+ tc qdisc del dev $tap root"
        $SUDO tc qdisc del dev "$tap" root 2>/dev/null || true
      done < <(wan_taps)
      echo "netem: cleared on $BRIDGE taps (default qdisc restored)"
      ;;
    show)
      local tap
      require_bridge
      while read -r tap; do
        [ -n "$tap" ] || continue
        echo "== $tap =="
        $SUDO tc qdisc show dev "$tap"
      done < <(wan_taps)
      ;;
    *)
      usage
      ;;
  esac
}

main "$@"
