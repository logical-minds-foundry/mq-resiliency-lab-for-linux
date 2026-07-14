#!/usr/bin/env bash
# lab/boxes/build-fatbox.sh - cache-aware provision-then-snapshot fat-box build.
#
# A "fat" box bakes the slow, deterministic provisioning (packages, MQ, OSS
# agents) INTO the box image once, so `vagrant up` + the runtime playbook are
# fast on every lab bring-up (#603, epic .github#70). It generalizes the
# RHEL-only rhel96/build-box.sh: boot a base box -> run the box's bake playbook
# -> qemu-img convert -c -> host-durable build/state/boxes/<box>.box cache ->
# `vagrant box add`. The running lab stays ephemeral; only the cache is durable.
#
#   --box <name>                        REQUIRED; one of the known fat boxes below
#   --domain-type <kvm|qemu>            REQUIRED; mqlab supplies it from host facts
#   --cpu-mode <host-passthrough|maximum> REQUIRED; pairs with --domain-type
#   --rebuild-box / LAB_REBUILD_BOX=1   force a fresh build (overwrite the cache)
#   --dry-run                           print the decision and exit, do nothing
#
# Staleness (design: manifest-hash + graduated age):
#   * REUSE the cache only while the stored manifest hash matches the current one
#     (_manifest-hash.sh) AND the box is < WARN_DAYS old;
#   * 7-14d: still REUSE, but emit a NOTICE to refresh;
#   * >=14d: REFUSE (exit non-zero) demanding --rebuild-box - a bake that old may
#     miss base-OS security updates.
# LAB_BOX_CACHE_DIR overrides the cache directory (tests point it at a tmp dir).
set -euo pipefail
cd "$(dirname "$0")"

WARN_DAYS="${WARN_DAYS:-7}"
REFUSE_DAYS="${REFUSE_DAYS:-14}"
FORCE="${LAB_REBUILD_BOX:-0}"
DRY_RUN=0
BOX=""
DOMAIN_TYPE=""
CPU_MODE=""

usage() {
  cat >&2 <<'USAGE'
usage: build-fatbox.sh --box <name> --domain-type <kvm|qemu> \
                       --cpu-mode <host-passthrough|maximum> [--rebuild-box] [--dry-run]

  --box is one of: mq-rdqm-rhel9, obs-ubuntu2404, infra-ubuntu2404.
  --domain-type / --cpu-mode are REQUIRED. mqlab normally supplies them
  (it computes them from host facts via platforms.build_domain_virt, #327).
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --box) BOX="${2:-}"; shift ;;
    --rebuild-box) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --domain-type) DOMAIN_TYPE="${2:-}"; shift ;;
    --cpu-mode) CPU_MODE="${2:-}"; shift ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# --box selects the base box + arch; the bake playbook is derived (ansible/bake-<box>.yml).
case "$BOX" in
  mq-rdqm-rhel9)    BASE_KIND=rhel;   BASE_BOX="rhel/9.6-x86_64" ;;
  obs-ubuntu2404)   BASE_KIND=ubuntu; BASE_BOX="cloud-image/ubuntu-24.04" ;;
  infra-ubuntu2404) BASE_KIND=ubuntu; BASE_BOX="cloud-image/ubuntu-24.04" ;;
  "") echo "ERROR: --box is required" >&2; usage; exit 2 ;;
  *)  echo "ERROR: unknown --box: '${BOX}'" >&2; usage; exit 2 ;;
esac
case "$DOMAIN_TYPE" in
  kvm|qemu) ;;
  *) echo "ERROR: --domain-type must be 'kvm' or 'qemu' (got '${DOMAIN_TYPE}')" >&2; usage; exit 2 ;;
esac
case "$CPU_MODE" in
  host-passthrough|maximum) ;;
  *) echo "ERROR: --cpu-mode must be 'host-passthrough' or 'maximum' (got '${CPU_MODE}')" >&2; usage; exit 2 ;;
esac

# Cache lives on the HOST-DURABLE main-worktree build/ so it survives base-VM
# rebuilds (#57). git-common-dir points at the main repo's .git from any
# worktree; its parent is the main worktree root. LAB_BOX_CACHE_DIR overrides it.
if [ -n "${LAB_BOX_CACHE_DIR:-}" ]; then
  CACHE_DIR="$LAB_BOX_CACHE_DIR"
else
  common_dir="$(git rev-parse --git-common-dir)"
  MAIN_ROOT="$(cd "$(dirname "$common_dir")" && pwd)"
  CACHE_DIR="$MAIN_ROOT/build/state/boxes"
fi
mkdir -p "$CACHE_DIR"
CACHE="$CACHE_DIR/${BOX}.box"
HASH_FILE="$CACHE_DIR/${BOX}.manifest-hash"

CURRENT_HASH="$(./_manifest-hash.sh "$BOX")"

# Decide the action up front (the testable surface, exercised via --dry-run).
age_days=0
if [ "$FORCE" = 1 ]; then
  action="FORCE-BUILD"
elif [ ! -f "$CACHE" ]; then
  action="BUILD"
elif [ "$(cat "$HASH_FILE" 2>/dev/null || true)" != "$CURRENT_HASH" ]; then
  action="BUILD"   # manifest (pins or bake recipe) changed -> the cache is void
else
  age_days=$(( ( $(date +%s) - $(stat -c %Y "$CACHE") ) / 86400 ))
  if [ "$age_days" -ge "$REFUSE_DAYS" ]; then
    action="STALE"
  else
    action="REUSE"
  fi
fi

echo "box cache: $CACHE"
echo "decision:  $action"
if [ "$action" = REUSE ] && [ "$age_days" -ge "$WARN_DAYS" ]; then
  echo "NOTICE: cached box is ${age_days}d old (>= ${WARN_DAYS}d); pass --rebuild-box to refresh." >&2
fi

if [ "$DRY_RUN" = 1 ]; then
  echo "(dry-run; no action taken)"
  exit 0
fi

if [ "$action" = STALE ]; then
  echo "ERROR: cached box is ${age_days}d old (>= ${REFUSE_DAYS}d); refusing to reuse a bake this" \
       "old (it may miss base-OS security updates). Pass --rebuild-box to rebuild." >&2
  exit 1
fi

# --- Cheap path: register the cached box and we are done. ---
if [ "$action" = REUSE ]; then
  vagrant box add --force "$BOX" "$CACHE"
  echo "box ready (from cache): $BOX"
  exit 0
fi

# --- Expensive path (BUILD / FORCE-BUILD): boot base -> bake -> snapshot. ---
# Self-contained provision-then-snapshot. It references ansible/bake-<box>.yml +
# ansible/inventory/bake-host.ini (produced by #602). End-to-end box correctness
# — the domain boots, the bake converges, the snapshot registers — is exercised
# and hardened in #604, not here; this task ships the recipe and its cache/
# staleness decision surface.
BAKE_PLAYBOOK="../../ansible/bake-${BOX}.yml"
BAKE_INVENTORY="../../ansible/inventory/bake-host.ini"
for f in "$BAKE_PLAYBOOK" "$BAKE_INVENTORY"; do
  test -f "$f" || { echo "ERROR: bake input not found: $f (produced by #602)" >&2; exit 1; }
done

BUILD_DOM="fatbox-${BOX}-build"
IMG="/var/lib/libvirt/images/${BUILD_DOM}.qcow2"
POOL_IMG="/var/lib/libvirt/images"

# 1. Ensure the base box is present: the RHEL base is itself locally built
#    (rhel96/build-box.sh); the Ubuntu base comes from Vagrant Cloud (idempotent
#    add — an already-present box is left as-is).
if [ "$BASE_KIND" = rhel ]; then
  ./rhel96/build-box.sh --domain-type "$DOMAIN_TYPE" --cpu-mode "$CPU_MODE"
else
  vagrant box list | grep -q "^${BASE_BOX} " || vagrant box add "$BASE_BOX"
fi

# 2. Resolve the base box's disk image (the vagrant-libvirt box layout) and cut a
#    qcow2 overlay so the bake mutates scratch, never the shared base image.
BASE_DIR="$HOME/.vagrant.d/boxes/$(echo "$BASE_BOX" | tr '/' '-VAGRANTSLASH-')"
BASE_IMG="$(find "$BASE_DIR" -name box.img -path '*/libvirt/*' | sort | tail -n1)"
test -n "$BASE_IMG" || { echo "ERROR: base box image not found under $BASE_DIR" >&2; exit 1; }
../scripts/net-up.sh vagrant-libvirt
virsh -c qemu:///system destroy "$BUILD_DOM" 2>/dev/null || true
virsh -c qemu:///system undefine "$BUILD_DOM" --nvram 2>/dev/null || true
sudo qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMG" "$IMG"

# 3. Define + boot a transient build domain over the overlay (same virt knobs the
#    lab uses), then run the box's bake playbook against it.
BUILD_XML="$(mktemp)"
cat > "$BUILD_XML" <<XML
<domain type='${DOMAIN_TYPE}'>
  <name>${BUILD_DOM}</name>
  <memory unit='MiB'>2048</memory>
  <vcpu>2</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <cpu mode='${CPU_MODE}'/>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='${IMG}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='network'>
      <source network='vagrant-libvirt'/>
      <model type='virtio'/>
    </interface>
    <serial type='file'>
      <source path='${POOL_IMG}/${BUILD_DOM}-console.log'/>
      <target port='0'/>
    </serial>
    <console type='file'>
      <source path='${POOL_IMG}/${BUILD_DOM}-console.log'/>
      <target type='serial' port='0'/>
    </console>
  </devices>
</domain>
XML
virsh -c qemu:///system define "$BUILD_XML"
virsh -c qemu:///system start "$BUILD_DOM"
ansible-playbook -i "$BAKE_INVENTORY" "$BAKE_PLAYBOOK"

# 4. Power off (wait for shut off), then package the overlay into the CACHE. A
#    compressed convert flattens the base+overlay chain and sheds bake scratch.
virsh -c qemu:///system shutdown "$BUILD_DOM"
while [ "$(virsh -c qemu:///system domstate "$BUILD_DOM" 2>/dev/null || true)" != "shut off" ]; do
  sleep 5
done
WORK="$CACHE_DIR/${BOX}-work"; mkdir -p "$WORK"
sudo qemu-img convert -O qcow2 -c "$IMG" "$WORK/box.img.tmp"
sudo chown "$(id -u)" "$WORK/box.img.tmp"
mv "$WORK/box.img.tmp" "$WORK/box.img"
printf '{"provider":"libvirt","format":"qcow2","virtual_size":20}\n' > "$WORK/metadata.json"
tar -C "$WORK" -czf "$CACHE" metadata.json box.img
printf '%s\n' "$CURRENT_HASH" > "$HASH_FILE"   # stamp the manifest hash beside the box
vagrant box add --force "$BOX" "$CACHE"

# 5. Cleanup: tear down the transient build domain + scratch (cache is kept).
virsh -c qemu:///system undefine "$BUILD_DOM" --nvram 2>/dev/null || true
sudo rm -f "$IMG" "$POOL_IMG/${BUILD_DOM}-console.log"
rm -rf "$WORK" "$BUILD_XML"
echo "box ready (built + cached): $BOX -> $CACHE"
