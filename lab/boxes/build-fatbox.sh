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

# --box selects the base box + arch + the box's bake playbook (ansible/bake-<BAKE>.yml).
# The playbook stem (#602: bake-mq-rdqm / bake-obs / bake-infra) is shorter than the box
# name, so it is mapped explicitly rather than derived from $BOX.
case "$BOX" in
  mq-rdqm-rhel9)    BASE_KIND=rhel;   BASE_BOX="rhel/9.6-x86_64";        BAKE=mq-rdqm ;;
  obs-ubuntu2404)   BASE_KIND=ubuntu; BASE_BOX="cloud-image/ubuntu-24.04"; BAKE=obs ;;
  infra-ubuntu2404) BASE_KIND=ubuntu; BASE_BOX="cloud-image/ubuntu-24.04"; BAKE=infra ;;
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
# MAIN_ROOT is the host-durable main-worktree root (git-common-dir's parent from any
# worktree). The box cache lives under it AND so do the bake inputs the BUILD path needs
# (the MQ media + the install DVD) — a feature worktree's own build/ is empty, so the bake
# must source them from here, not from the worktree the playbook runs in (#604).
common_dir="$(git rev-parse --git-common-dir)"
MAIN_ROOT="$(cd "$(dirname "$common_dir")" && pwd)"
if [ -n "${LAB_BOX_CACHE_DIR:-}" ]; then
  CACHE_DIR="$LAB_BOX_CACHE_DIR"
else
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

# --- Expensive path (BUILD / FORCE-BUILD): boot the base box, run the box's bake playbook
#     against it OVER SSH, then snapshot the result into the CACHE (#604 hardens this path).
BAKE_PLAYBOOK="../../ansible/bake-${BAKE}.yml"
test -f "$BAKE_PLAYBOOK" \
  || { echo "ERROR: bake playbook not found: $BAKE_PLAYBOOK (produced by #602)" >&2; exit 1; }

BUILD_DOM="fatbox-${BOX}-build"
POOL_IMG="/var/lib/libvirt/images"
IMG="${POOL_IMG}/${BUILD_DOM}.qcow2"
CONSOLE="${POOL_IMG}/${BUILD_DOM}-console.log"
VAGRANT_KEY="$HOME/.vagrant.d/insecure_private_key"

# 1. Ensure the base box is present: the RHEL base is itself locally built
#    (rhel96/build-box.sh); the Ubuntu base comes from Vagrant Cloud (idempotent
#    add — an already-present box is left as-is).
if [ "$BASE_KIND" = rhel ]; then
  ./rhel96/build-box.sh --domain-type "$DOMAIN_TYPE" --cpu-mode "$CPU_MODE"
else
  vagrant box list | grep -q "^${BASE_BOX} " || vagrant box add "$BASE_BOX"
fi

# 2. Resolve the base box's disk image and COPY it into the pool as the transient build
#    disk. A full copy (not a backing-file overlay) keeps qemu off the home-dir base image
#    — libvirt's dynamic ownership + per-domain AppArmor only cover pool paths — and is
#    itself scratch: the bake mutates the copy, the shared base box is untouched.
BASE_DIR="$HOME/.vagrant.d/boxes/${BASE_BOX//\//-VAGRANTSLASH-}"
BASE_IMG="$(find "$BASE_DIR" -name box.img -path '*/libvirt/*' | sort | tail -n1)"
test -n "$BASE_IMG" || { echo "ERROR: base box image not found under $BASE_DIR" >&2; exit 1; }
../scripts/net-up.sh vagrant-libvirt
virsh -c qemu:///system destroy "$BUILD_DOM" 2>/dev/null || true
virsh -c qemu:///system undefine "$BUILD_DOM" --nvram 2>/dev/null || true
sudo rm -f "$IMG"
sudo cp "$BASE_IMG" "$IMG"

# 3. RHEL bakes need the install DVD attached as a cdrom: rdqm-install builds its offline
#    dnf repo from it (BaseOS+AppStream) to resolve the MQ rpms' base-OS deps. Stage it into
#    the pool (symlink/copy per source fs) and attach on the sata bus (q35 has no IDE),
#    mirroring the lab Vagrantfile + rhel96 build-domain. The bake runs the WORKTREE's
#    playbook (whose build/ is empty), so point the MQ media at the host-durable
#    main-worktree cache (mq_media_dir role default, #604). Ubuntu fat boxes attach no DVD.
CDROM_XML=""
BAKE_EXTRA_VARS=()
if [ "$BASE_KIND" = rhel ]; then
  DVD_SRC="$MAIN_ROOT/build/state/rhel-9.6-x86_64-dvd.iso"
  test -f "$DVD_SRC" || { echo "ERROR: install DVD not found: $DVD_SRC" >&2; exit 1; }
  DVD_POOL="${POOL_IMG}/rhel-9.6-x86_64-dvd.iso"
  ../scripts/stage-iso-into-pool.sh "$DVD_SRC" "$DVD_POOL"
  CDROM_XML="<disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='${DVD_POOL}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>"
  BAKE_EXTRA_VARS=(-e "mq_media_dir=$MAIN_ROOT/build/cache/mq")
fi

# The obs bake's mq-exporter build pulls the full MQ (client libs + SDK for the cgo build)
# via the Ubuntu mq-install role, which — like rdqm-install — reads its tarball from
# mq_media_dir. Point it at the host-durable main-worktree cache too (the worktree's build/
# is empty). Ubuntu registers online, so no DVD is attached. (#605)
if [ "$BAKE" = obs ]; then
  ls "$MAIN_ROOT"/build/cache/mq/*-IBM-MQ-Advanced-for-Developers-UbuntuLinuxX64.tar.gz >/dev/null 2>&1 \
    || { echo "ERROR: UbuntuLinuxX64 MQ media not found under $MAIN_ROOT/build/cache/mq for the obs bake" >&2; exit 1; }
  BAKE_EXTRA_VARS=(-e "mq_media_dir=$MAIN_ROOT/build/cache/mq")
fi

# 4. Define + boot the transient build domain (same virt knobs the lab uses; acpi so
#    `virsh shutdown` powers it off cleanly).
BUILD_XML="$(mktemp)"
cat > "$BUILD_XML" <<XML
<domain type='${DOMAIN_TYPE}'>
  <name>${BUILD_DOM}</name>
  <memory unit='MiB'>2048</memory>
  <vcpu>2</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <features><acpi/></features>
  <cpu mode='${CPU_MODE}'/>
  <on_poweroff>destroy</on_poweroff>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='${IMG}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    ${CDROM_XML}
    <interface type='network'>
      <source network='vagrant-libvirt'/>
      <model type='virtio'/>
    </interface>
    <serial type='file'>
      <source path='${CONSOLE}'/>
      <target port='0'/>
    </serial>
    <console type='file'>
      <source path='${CONSOLE}'/>
      <target type='serial' port='0'/>
    </console>
  </devices>
</domain>
XML
virsh -c qemu:///system define "$BUILD_XML"
virsh -c qemu:///system start "$BUILD_DOM"

# 5. Wait for the guest to take a DHCP lease on vagrant-libvirt, discover its IP, then wait
#    for sshd. The base box ships Vagrant's insecure key for the vagrant user (the lab sets
#    config.ssh.insert_key=false) with passwordless sudo, so the bake connects as vagrant
#    and becomes root — the lab's per-node access, but to this one build VM.
MAC="$(virsh -c qemu:///system domiflist "$BUILD_DOM" | awk '/vagrant-libvirt/ {print $NF}')"
BUILD_IP=""
for _ in $(seq 1 60); do
  BUILD_IP="$(virsh -c qemu:///system net-dhcp-leases vagrant-libvirt 2>/dev/null \
    | awk -v m="$MAC" 'tolower($0) ~ tolower(m) {print $5}' | cut -d/ -f1 | head -n1)"
  [ -n "$BUILD_IP" ] && break
  sleep 5
done
test -n "$BUILD_IP" || { echo "ERROR: no DHCP lease for $BUILD_DOM (mac $MAC) after 5m" >&2; exit 1; }
echo "build VM $BUILD_DOM is $BUILD_IP; waiting for sshd..."
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o BatchMode=yes)
ssh_up=0
for _ in $(seq 1 60); do
  if ssh -i "$VAGRANT_KEY" "${SSH_OPTS[@]}" "vagrant@${BUILD_IP}" true 2>/dev/null; then ssh_up=1; break; fi
  sleep 5
done
[ "$ssh_up" = 1 ] || { echo "ERROR: sshd on $BUILD_IP never came up after 5m" >&2; exit 1; }

# 6. Generate a one-host dynamic inventory placing the build VM in the `bake` group, and run
#    the box's bake playbook against it over SSH (NOT the local-connection bake-host.ini,
#    which would install onto THIS host). Run from ansible/ so ansible.cfg applies; point the
#    collections path at the main-worktree cache (the worktree's build/ is empty).
BUILD_INV="$(mktemp)"
cat > "$BUILD_INV" <<INV
[bake]
${BUILD_DOM} ansible_host=${BUILD_IP}

[bake:vars]
ansible_user=vagrant
ansible_connection=ssh
ansible_ssh_private_key_file=${VAGRANT_KEY}
ansible_ssh_common_args=-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
INV
( cd ../../ansible \
  && ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_COLLECTIONS_PATH="$MAIN_ROOT/build/cache" \
     ansible-playbook -i "$BUILD_INV" "bake-${BAKE}.yml" \
       ${BAKE_EXTRA_VARS[@]+"${BAKE_EXTRA_VARS[@]}"} )

# 7. Power off (wait for shut off), then package the disk into the CACHE. A
#    compressed convert flattens the image and sheds bake scratch.
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

# 8. Cleanup: tear down the transient build domain + scratch (cache + staged DVD are kept).
virsh -c qemu:///system undefine "$BUILD_DOM" --nvram 2>/dev/null || true
sudo rm -f "$IMG" "$CONSOLE"
rm -rf "$WORK" "$BUILD_XML" "$BUILD_INV"
echo "box ready (built + cached): $BOX -> $CACHE"
