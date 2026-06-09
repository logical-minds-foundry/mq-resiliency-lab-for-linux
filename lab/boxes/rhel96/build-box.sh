#!/usr/bin/env bash
# lab/boxes/rhel96/build-box.sh - cache-aware RHEL 9.6 box build.
#
# Builds the box ONCE (DVD ISO + OEMDRV kickstart -> qcow2 -> vagrant-libvirt
# .box; ~45-90 min under TCG) and caches it on the HOST-DURABLE, repo-root
# build/ so it survives base-VM rebuilds (#57). Subsequent runs just
# `vagrant box add` from the cache (minutes). The running lab stays ephemeral.
#
#   --rebuild-box / LAB_REBUILD_BOX=1   force a fresh build (overwrite the cache)
#   --dry-run                           print the decision and exit, do nothing
#   STALE_DAYS=N (default 30)           age past which a NON-blocking notice prints
#   RHEL_ISO=/path                      override ISO location (else build/)
set -euo pipefail
cd "$(dirname "$0")"

BOX_NAME="rhel/9.6-x86_64"
STALE_DAYS="${STALE_DAYS:-30}"
FORCE="${LAB_REBUILD_BOX:-0}"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --rebuild-box) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "ERROR: unknown arg: $arg" >&2; exit 2 ;;
  esac
done

# Resolve the MAIN-worktree build/ (host-durable), NOT a feature worktree's
# ephemeral build/. git-common-dir points at the main repo's .git from any
# worktree, so its parent is the main worktree root regardless of where we run.
common_dir="$(git rev-parse --git-common-dir)"
MAIN_ROOT="$(cd "$(dirname "$common_dir")" && pwd)"
BUILD_DIR="$MAIN_ROOT/build"
CACHE="$BUILD_DIR/boxes/rhel-9.6-x86_64-libvirt.box"
mkdir -p "$BUILD_DIR/boxes"

# Decide the action up front (the testable surface, exercised via --dry-run).
if [ "$FORCE" = 1 ]; then
  action="FORCE-BUILD"
elif [ -f "$CACHE" ]; then
  action="REUSE"
else
  action="BUILD"
fi

if [ "$action" = REUSE ]; then
  age_days=$(( ( $(date +%s) - $(stat -c %Y "$CACHE") ) / 86400 ))
  if [ "$age_days" -ge "$STALE_DAYS" ]; then
    echo "NOTICE: cached box is ${age_days}d old (>= ${STALE_DAYS}d);" \
         "pass --rebuild-box to refresh." >&2
  fi
fi

echo "box cache: $CACHE"
echo "decision:  $action"
if [ "$DRY_RUN" = 1 ]; then
  echo "(dry-run; no action taken)"
  exit 0
fi

# --- Cheap path: register the cached box and we are done. ---
if [ "$action" = REUSE ]; then
  vagrant box add --force "$BOX_NAME" "$CACHE"
  echo "box ready (from cache): $BOX_NAME"
  exit 0
fi

# --- Expensive path (BUILD / FORCE-BUILD): the ~45-90 min TCG install. ---
ISO="${RHEL_ISO:-}"
if [ -z "$ISO" ]; then
  c="$BUILD_DIR/rhel-9.6-x86_64-dvd.iso"
  [ -f "$c" ] && ISO="$c"
fi
test -n "$ISO" || {
  echo "ERROR: rhel-9.6-x86_64-dvd.iso not found in $BUILD_DIR (set RHEL_ISO)" >&2
  exit 1
}
WORK="$BUILD_DIR/rhel96-box"; mkdir -p "$WORK"

# 1. OEMDRV volume: anaconda auto-loads ks.cfg from a volume so labeled.
genisoimage -quiet -V OEMDRV -o "$WORK/oemdrv.iso" ks.cfg

# 2. Stage inputs where qemu (its own uid) can read them - the host mount
#    is not readable by the qemu user (diag-spike permission lesson).
sudo cp "$WORK/oemdrv.iso" /var/lib/libvirt/images/oemdrv.iso
if [ ! -f /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso ]; then
  echo "staging ISO into the storage pool (12.7G copy)..."
  sudo cp "$ISO" /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso
fi
sudo qemu-img create -f qcow2 /var/lib/libvirt/images/rhel96-build.qcow2 20G
sudo touch /var/lib/libvirt/images/rhel96-build-console.log
sudo chown 64055:993 /var/lib/libvirt/images/rhel96-build.qcow2 \
  /var/lib/libvirt/images/rhel96-build-console.log

# 3. Transient build domain (the #24 TCG recipe), wait for install poweroff.
sed -e "s|@ISO@|/var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso|" \
  build-domain.xml.tpl > "$WORK/domain.xml"
virsh -c qemu:///system define "$WORK/domain.xml"
virsh -c qemu:///system start rhel96-build
echo "installing (TCG, expect 45-90 min); waiting for shut off..."
until [ "$(virsh -c qemu:///system domstate rhel96-build 2>/dev/null)" = "shut off" ]; do
  sleep 60
done

# 4. Package the box into the CACHE (compressed convert sheds install scratch).
sudo qemu-img convert -O qcow2 -c \
  /var/lib/libvirt/images/rhel96-build.qcow2 "$WORK/box.img.tmp"
sudo chown "$(id -u)" "$WORK/box.img.tmp"
mv "$WORK/box.img.tmp" "$WORK/box.img"
printf '{"provider":"libvirt","format":"qcow2","virtual_size":20}\n' > "$WORK/metadata.json"
tar -C "$WORK" -czf "$CACHE" metadata.json box.img
vagrant box add --force "$BOX_NAME" "$CACHE"

# 5. Cleanup (keep the staged ISO for future rebuilds).
virsh -c qemu:///system undefine rhel96-build
sudo rm -f /var/lib/libvirt/images/rhel96-build.qcow2 /var/lib/libvirt/images/oemdrv.iso
echo "box ready (built + cached): $BOX_NAME -> $CACHE"
