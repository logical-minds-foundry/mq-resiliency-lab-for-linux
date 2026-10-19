#!/usr/bin/env bash
# lab/scripts/net-cut.sh <down|up> <net-name> [node ...] — the lab's reversible
# link-cut fault primitive (#1638). Sets the link of every running lab guest's
# interface on <net-name> down or up with `virsh domif-setlink`; the network and
# the guests' tap devices stay in place, so `up` restores exactly what `down` cut.
# With node names, only those guests (libvirt domain lab_<node>) are touched.
#
# Use this, not net-down.sh, for network-loss drills: net-down.sh destroys the
# libvirt network, and net-up.sh does not reattach running guests' taps.
#
# Each interface is resolved from `virsh domiflist` (the MAC whose Source column is
# <net-name>). All targets are resolved before any link changes. Self-echoes each
# virsh action. Fail-loud: exits non-zero when no interface matches, a named node
# is not running or has no interface on the network, a domif-setlink fails, or
# domif-getlink afterwards does not report the requested state.
#
# The libvirt network itself stays active, so lab_network_state does not change
# and nothing is published.
set -euo pipefail
shopt -s inherit_errexit

usage() { echo "usage: net-cut.sh <down|up> <net-name> [node ...]" >&2; exit 2; }
die() { echo "net-cut: $*" >&2; exit 1; }
V() { virsh -c qemu:///system "$@"; }
run() { echo "+ virsh -c qemu:///system $*"; V "$@"; }   # echo verbatim, then run

[ "$#" -ge 2 ] || usage
STATE="$1"; NET="$2"; shift 2
case "$STATE" in down|up) ;; *) usage ;; esac
[ -n "$NET" ] || usage

# macs_on <domain> — the MACs of the domain's interfaces whose Source is $NET. The
# columns are located by the header row, and any row that does not fit is an error.
macs_on() {
  local out
  out=$(V domiflist "$1") || die "virsh domiflist $1 failed"
  printf '%s\n' "$out" | awk -v net="$NET" -v dom="$1" '
    function bad(what) { printf "net-cut: unexpected domiflist %s for %s: %s\n", what, dom, $0 > "/dev/stderr"; failed = 1; exit 1 }
    !hdr && NF == 0 { next }
    !hdr { for (i = 1; i <= NF; i++) col[$i] = i
           if (!("Source" in col) || !("MAC" in col)) bad("header")
           hdr = 1; cols = NF; next }
    /^[[:space:]]*-+[[:space:]]*$/ || NF == 0 { next }
    NF != cols { bad("row") }
    $col["Source"] == net { print $col["MAC"] }
    END { if (!failed && !hdr) { $0 = "(no output)"; bad("header") } }'
}

# Resolve the target domains: the named nodes (each must be running), or every
# running lab_ domain.
domains=()
if [ "$#" -gt 0 ]; then
  for node in "$@"; do
    dom="lab_$node"
    st=$(V domstate "$dom") || die "virsh domstate $dom failed (no such domain?)"
    [ "$st" = "running" ] || die "$dom is '$st', not running"
    domains+=("$dom")
  done
else
  running=$(V list --name) || die "virsh list --name failed"
  while IFS= read -r dom; do
    case "$dom" in lab_*) domains+=("$dom") ;; esac
  done <<<"$running"
fi

# Resolve every target interface before touching any link.
targets=()
for dom in "${domains[@]}"; do
  macs=$(macs_on "$dom")
  if [ -z "$macs" ]; then
    [ "$#" -eq 0 ] || die "$dom has no interface on $NET"
    continue
  fi
  while IFS= read -r mac; do targets+=("$dom $mac"); done <<<"$macs"
done
[ "${#targets[@]}" -gt 0 ] || die "no running lab domain has an interface on $NET"

for t in "${targets[@]}"; do
  read -r dom mac <<<"$t"
  run domif-setlink "$dom" "$mac" "$STATE" || die "domif-setlink $dom $mac $STATE failed"
  echo "+ virsh -c qemu:///system domif-getlink $dom $mac"
  got=$(V domif-getlink "$dom" "$mac") || die "domif-getlink $dom $mac failed"
  echo "$got"
  # libvirt prints one line, "<interface> <state>". Anything else is unexpected.
  seen=$(printf '%s\n' "$got" | awk 'NF { n++; f = NF; s = $NF } END { if (n == 1 && f == 2) print s }')
  [ "$seen" = "$STATE" ] || die "domif-getlink $dom $mac reported '$got', expected '$STATE'"
  echo "$STATE: $dom $mac ($NET)"
done
