#!/usr/bin/env bash
# lab/scripts/stage-rhel-iso.sh - stage the RHEL 9.6 DVD ISO into the libvirt
# storage pool so RHEL guests can attach it as their offline BaseOS+AppStream dnf
# repo (#276/#291). The host-mounted build/ is not readable by the qemu user, so
# the ISO must live in the pool. Idempotent (no-op if already staged); the pool is
# wiped on a VM rebuild, so this re-stages on a fresh box. Credential-less (sudo cp
# of the operator-supplied ISO; no GitHub/Claude creds).
set -euo pipefail
POOL_ISO="/var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso"
if [ -f "$POOL_ISO" ]; then
  echo "RHEL DVD ISO already staged: $POOL_ISO"
  exit 0
fi
# Resolve the operator-supplied source ISO: env override, else the MAIN worktree's
# host-durable build/ (git-common-dir finds main from any worktree).
SRC="${MQLAB_RHEL_ISO:-${RHEL_ISO:-}}"
if [ -z "$SRC" ]; then
  common_dir="$(git rev-parse --git-common-dir)"
  main_root="$(cd "$(dirname "$common_dir")" && pwd)"
  SRC="$main_root/build/state/rhel-9.6-x86_64-dvd.iso"
fi
test -f "$SRC" || {
  echo "ERROR: RHEL DVD ISO not found at $SRC" >&2
  echo "       set MQLAB_RHEL_ISO=/path/to/rhel-9.6-x86_64-dvd.iso, or drop it in build/state/." >&2
  exit 1
}
echo "staging RHEL DVD ISO into the storage pool (~12.7G copy)..."
sudo cp "$SRC" "$POOL_ISO"
echo "staged: $POOL_ISO"
