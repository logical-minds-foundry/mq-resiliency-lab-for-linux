#!/usr/bin/env bash
# lab/scripts/nic-assure.sh — post-boot guest NIC assurance for RHEL9 lab nodes (#860).
#
# Why: vagrant-libvirt's RedHat configure_networks writes an ifcfg for every lab NIC,
# but NetworkManager on RHEL9 non-deterministically leaves some of them unmanaged /
# DOWN with no IP after boot (triage .github#102). That cascades into unreachable
# nodes, "provision dns" exit 4, and Native HA no-quorum — one race, many symptoms,
# worse under host load. #690 forces NM_CONTROLLED=yes so the connection profile
# always EXISTS; this guard is the deterministic backstop that makes sure it is
# actually ACTIVE. It reloads NetworkManager, claims every ethernet device as
# managed, brings its connection up, then ASSERTS every expected IP is live —
# exiting non-zero (fail loud) if any declared NIC is still missing.
#
# Idempotent: on a healthy node every remediation step is a no-op and the assert
# passes on the first probe. Because the driver invokes it over the Vagrant NAT
# channel (enp5s0, independent of every lab NIC), it can repair even a down
# net-mgmt NIC that the Ansible-over-net-mgmt provision plays could never reach.
#
# Usage:  sudo bash nic-assure.sh <expected-ip> [<expected-ip> ...]
#   Each <expected-ip> is a declared lab NIC address for this node (topology nics).
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "nic-assure: no expected IPs given" >&2
  exit 2
fi
expected=("$@")

# --- Remediate: make NetworkManager own and activate every ethernet device ------
if command -v nmcli >/dev/null 2>&1; then
  # Load any ifcfg/keyfile profile NM has not yet picked up (the #690 profile).
  nmcli connection reload || true
  mapfile -t devices < <(nmcli -t -f DEVICE,TYPE device 2>/dev/null \
    | awk -F: '$2 == "ethernet" { print $1 }')
  for dev in "${devices[@]}"; do
    [ -n "$dev" ] || continue
    # Claim a device the boot race left "unmanaged", then activate its profile.
    # Both are no-ops on an already-connected device; tolerate their exit codes.
    nmcli device set "$dev" managed yes >/dev/null 2>&1 || true
    nmcli device connect "$dev" >/dev/null 2>&1 || true
  done
fi

# --- Assert: every expected IP is live, retrying while NetworkManager settles ----
missing=()
for ((attempt = 0; attempt < 10; attempt++)); do
  mapfile -t live < <(ip -o -4 addr show | awk '{ print $4 }' | cut -d/ -f1)
  missing=()
  for want in "${expected[@]}"; do
    found=""
    for have in "${live[@]}"; do
      [ "$have" = "$want" ] && { found=1; break; }
    done
    [ -n "$found" ] || missing+=("$want")
  done
  [ "${#missing[@]}" -eq 0 ] && break
  sleep 2
done

if [ "${#missing[@]}" -ne 0 ]; then
  echo "nic-assure: FAILED — declared NIC IP(s) still missing after remediation:" \
    "${missing[*]}" >&2
  echo "--- nmcli device ---" >&2
  nmcli -t -f DEVICE,TYPE,STATE device >&2 2>/dev/null || true
  echo "--- ip -4 addr ---" >&2
  ip -o -4 addr show >&2 || true
  exit 1
fi

echo "nic-assure: OK — all ${#expected[@]} declared NIC IP(s) live: ${expected[*]}"
