#!/usr/bin/env bash
# lab/scripts/nic-config.sh — NM-native, authoritative config for RHEL9 lab NICs (#866).
#
# Root-cause companion to the #860 nic-assure guard. The RHEL9 NIC race (triage
# .github#102) is that vagrant-libvirt's RedHat configure_networks writes an ifcfg per
# lab NIC and then applies it with /sbin/ifup — which does not exist on NetworkManager-
# only RHEL9. So the profile lands on disk but its apply step is a silent no-op, and
# whether NetworkManager reads + auto-activates it at boot is a load-sensitive race:
# some NICs come up managed+up, others are left unmanaged / DOWN with no IP.
#
# #690 made the profile always EXIST (forces NM_CONTROLLED=yes, short-circuiting a
# second, separate probe race). #860's nic-assure guard reactively nudges it ACTIVE
# once, post-boot, and asserts (fail loud). This step attacks the source instead of
# the symptom: it makes NetworkManager AUTHORITATIVELY OWN each declared lab NIC so
# NM itself brings it up and keeps it up — deterministically, the first time, and
# self-healing across any later device/carrier event during the long provision — so
# the assurance guard becomes a rare no-op rather than the thing doing the work.
#
# It reuses the mapping vagrant-libvirt already got right: configure_networks bound
# each declared IP to the correct device in the ifcfg it wrote, so we resolve each
# expected IP back to its NM profile and harden that profile in place. No new
# device-name→IP mapping (enp7s0…) is invented here; the enumeration order stays
# owned by Vagrant.
#
# For each declared lab NIC profile this step sets, idempotently:
#   connection.autoconnect        yes   — NM auto-activates it (no boot-time race)
#   connection.autoconnect-priority 100 — lab NICs win over any default profile
#   connection.autoconnect-retries  0   — retry forever; never give up mid-provision
#   ipv4.method                   manual — pin the static config (no-op; already manual)
# then claims the device managed and brings the connection up.
#
# It does NOT assert liveness or fail on a still-missing NIC — that stays the #860
# nic-assure guard's job, which runs immediately after this step as the fail-loud
# backstop. Both run over the Vagrant NAT channel (independent of every lab NIC), so
# they can configure even a down net-mgmt NIC the Ansible-over-net-mgmt plays could
# never reach. Idempotent: on an already-healthy node every step is a no-op.
#
# Usage:  sudo bash nic-config.sh <expected-ip> [<expected-ip> ...]
#   Each <expected-ip> is a declared lab NIC address for this node (topology nics).
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "nic-config: no expected IPs given" >&2
  exit 2
fi
expected=("$@")

# Non-RHEL / no-NetworkManager path: nothing to do. The phases driver only invokes
# this on RHEL nodes, but stay defensive rather than fail if nmcli is absent.
if ! command -v nmcli >/dev/null 2>&1; then
  echo "nic-config: nmcli not present — skipping (non-NetworkManager guest)"
  exit 0
fi

# Load any ifcfg/keyfile profile NetworkManager has not yet picked up — the #690
# profile whose /sbin/ifup apply step was a no-op on RHEL9.
nmcli connection reload || true

# Resolve an expected lab IP back to the NM connection profile that carries it. Prints
# "<profile-name>" on the first ethernet connection whose ipv4 addresses include the
# IP, or nothing if no profile carries it yet (the assure guard will remediate then).
conn_for_ip() {
  local want="$1" name type addrs
  while IFS=$'\t' read -r name type; do
    [ "$type" = "802-3-ethernet" ] || [ "$type" = "ethernet" ] || continue
    addrs="$(nmcli -g ipv4.addresses connection show "$name" 2>/dev/null || true)"
    case " ${addrs//,/ } " in
      *" ${want}/"*) printf '%s\n' "$name"; return 0 ;;
    esac
  done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '{print $1"\t"$2}')
  return 0
}

# Make NetworkManager authoritatively own + activate one lab-NIC profile.
harden() {
  local conn="$1" dev
  dev="$(nmcli -g connection.interface-name connection show "$conn" 2>/dev/null || true)"
  # Claim the device if the boot race left it "unmanaged"; no-op once managed.
  [ -n "$dev" ] && nmcli device set "$dev" managed yes >/dev/null 2>&1 || true
  nmcli connection modify "$conn" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    connection.autoconnect-retries 0 \
    ipv4.method manual >/dev/null 2>&1 || true
  # Deterministic activation now, rather than hoping NM won the boot autoconnect race.
  # Retry a few times while NM settles; tolerate final failure (the guard asserts).
  local i
  for ((i = 0; i < 5; i++)); do
    nmcli connection up "$conn" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 0
}

configured=()
skipped=()
for want in "${expected[@]}"; do
  conn="$(conn_for_ip "$want")"
  if [ -n "$conn" ]; then
    harden "$conn"
    configured+=("$want")
  else
    skipped+=("$want")
  fi
done

echo "nic-config: hardened ${#configured[@]}/${#expected[@]} declared NIC profile(s)" \
  "as NM-managed autoconnect: ${configured[*]:-(none)}"
if [ "${#skipped[@]}" -ne 0 ]; then
  # Not fatal here — the #860 nic-assure guard runs next and fails loud if these are
  # still missing after its own remediation.
  echo "nic-config: no profile yet for: ${skipped[*]} (assure guard will remediate)"
fi
