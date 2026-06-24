#!/usr/bin/env bash
# lab/scripts/stage-rhel-iso.sh - stage the RHEL 9.6 DVD ISO into the libvirt
# storage pool so RHEL guests can attach it as their offline BaseOS+AppStream dnf
# repo (#276/#291). Resolves the operator-supplied source ISO, then delegates to
# stage-iso-into-pool.sh, which SYMLINKS the pool entry to the source on a local fs
# (the cloud box's /vergil — no ISO-sized copy on the small boot disk) and COPIES
# only when the source is a host-passthrough mount the nested qemu can't read through
# (Lima's build/). (#337) Idempotent; the pool is wiped on a VM rebuild, so this
# re-stages on a fresh box. Credential-less (no GitHub/Claude creds).
set -euo pipefail
POOL_ISO="/var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso"
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
exec "$(dirname "$0")/stage-iso-into-pool.sh" "$SRC" "$POOL_ISO"
