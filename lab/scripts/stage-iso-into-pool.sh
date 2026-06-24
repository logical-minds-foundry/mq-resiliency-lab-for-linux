#!/usr/bin/env bash
# lab/scripts/stage-iso-into-pool.sh <src-iso> <pool-dest> — make <pool-dest> (a path
# in the libvirt storage pool that guests attach as a cdrom) resolve to <src-iso>, by
# the cheapest means the source filesystem allows (#337):
#
#   - local fs (e.g. the cloud box's /vergil ext4 data volume): SYMLINK pool-dest ->
#     src. qemu reads the source in place; libvirt's per-domain AppArmor profile
#     (virt-aa-helper) resolves and allows the target. No ISO-sized copy lands on the
#     (small) boot disk that backs the pool.
#   - host-passthrough mount (Lima's 9p/virtiofs/fuse/nfs build/ mount): the nested
#     qemu CANNOT read through it, so COPY the ISO into the pool (original behaviour).
#
# Idempotent (a correct symlink / same-size copy is a no-op). Ensures the source is
# world-readable so the qemu uid can read it. sudo only when not already root, so the
# helper is unit-testable without sudo.
set -euo pipefail
SRC="${1:?usage: stage-iso-into-pool.sh <src-iso> <pool-dest>}"
DEST="${2:?usage: stage-iso-into-pool.sh <src-iso> <pool-dest>}"
test -f "$SRC" || {
  echo "ERROR: source ISO not found: $SRC" >&2
  exit 1
}

SUDO=()
[ "$(id -u)" -ne 0 ] && SUDO=(sudo)

# Filesystems the nested qemu cannot read through -> the ISO must be copied into the
# pool. Anything else is a local fs qemu can read in place -> symlink.
fstype="$(stat -f -c %T "$(dirname "$SRC")" 2>/dev/null || echo unknown)"
case "$fstype" in
  9p | virtiofs | fuse* | nfs* | cifs | smb*)
    if [ -f "$DEST" ] && [ ! -L "$DEST" ] \
      && [ "$(stat -c %s "$DEST" 2>/dev/null || echo 0)" = "$(stat -c %s "$SRC")" ]; then
      echo "ISO already staged (copy): $DEST"
      exit 0
    fi
    echo "copying ISO into pool ($fstype source, $(du -h "$SRC" | cut -f1))..."
    "${SUDO[@]}" cp "$SRC" "$DEST"
    echo "staged ISO (copy): $DEST"
    ;;
  *)
    if [ -L "$DEST" ] && [ "$(readlink -f "$DEST")" = "$(readlink -f "$SRC")" ]; then
      echo "ISO already linked: $DEST -> $SRC"
      exit 0
    fi
    "${SUDO[@]}" chmod a+r "$SRC"
    "${SUDO[@]}" ln -sfn "$SRC" "$DEST"
    echo "linked ISO into pool ($fstype source): $DEST -> $SRC"
    ;;
esac
