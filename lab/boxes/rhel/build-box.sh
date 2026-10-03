#!/usr/bin/env bash
# lab/boxes/rhel/build-box.sh - cache-aware RHEL base box build, one per RHEL major.
#
# Builds the box ONCE (DVD ISO + OEMDRV kickstart -> qcow2 -> vagrant-libvirt
# .box; ~45-90 min under TCG on the arm64 Mac, minutes under KVM on a native-x86
# host — #327) and caches it on the HOST-DURABLE, repo-root
# build/ so it survives base-VM rebuilds (#57). Subsequent runs just
# `vagrant box add` from the cache (minutes). The running lab stays ephemeral.
#
# Dumb builder (epic .github#280): mqlab passes every version input as a flag, taken
# from the catalog (lab/versions.yaml). Nothing here names a RHEL version.
#
#   --major <N>                         REQUIRED; the RHEL major (box rhel/<N>-x86_64)
#   --point <N.M>                       REQUIRED; the point release the DVD installs
#   --iso <file>                        REQUIRED; the DVD ISO filename under build/state/
#   --domain-type <kvm|qemu>            REQUIRED; mqlab supplies it from host facts
#   --cpu-mode <host-passthrough|maximum> REQUIRED; pairs with --domain-type
#   --rebuild-box / LAB_REBUILD_BOX=1   force a fresh build (overwrite the cache)
#   --dry-run                           print the decision and exit, do nothing
#   STALE_DAYS=N (default 30)           age past which a NON-blocking notice prints
#   RHEL_ISO=/path                      override ISO location (else build/state/<iso>)
#   LAB_BOX_CACHE_DIR=/path             override the cache directory (tests use it)
#
# Box name rhel/<major>-x86_64; cache build/state/boxes/rhel-<major>-x86_64.box. Both
# MUST match src/mqlab/box.py (the base box's name comes from the catalog's base_box,
# and its cache artifact is that name with '/' -> '-' plus '.box').
#
# Registration (#1248): REUSE keeps an already-current registration. The box is
# re-added only when the registered copy did not come from this exact cache
# artifact (see ../_box-register.sh). A BUILD / FORCE-BUILD always re-adds.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=lab/boxes/_box-register.sh
. ../_box-register.sh

STALE_DAYS="${STALE_DAYS:-30}"
FORCE="${LAB_REBUILD_BOX:-0}"
DRY_RUN=0
MAJOR=""
POINT=""
ISO_NAME=""
DOMAIN_TYPE=""
CPU_MODE=""

usage() {
  cat >&2 <<'USAGE'
usage: build-box.sh --major <N> --point <N.M> --iso <file> --domain-type <kvm|qemu> \
                    --cpu-mode <host-passthrough|maximum> [--rebuild-box] [--dry-run]

  --major / --point / --iso come from the catalog (lab/versions.yaml os.rhel.<N>);
  mqlab passes them (`mqlab box build rhel/<N>-x86_64`).
  --domain-type / --cpu-mode are REQUIRED. mqlab normally supplies them
  (it computes them from host facts via platforms.box_build_domain_virt, #327).
  If you are running this by hand on a native-x86 host, pass:
      --domain-type kvm  --cpu-mode host-passthrough
  on the arm64 Mac (x86 guest is emulated), pass:
      --domain-type qemu --cpu-mode maximum
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --major) MAJOR="${2:-}"; shift ;;
    --point) POINT="${2:-}"; shift ;;
    --iso) ISO_NAME="${2:-}"; shift ;;
    --rebuild-box) FORCE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --domain-type) DOMAIN_TYPE="${2:-}"; shift ;;
    --cpu-mode) CPU_MODE="${2:-}"; shift ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

for flag in major point iso; do
  case "$flag" in
    major) value="$MAJOR" ;;
    point) value="$POINT" ;;
    iso) value="$ISO_NAME" ;;
  esac
  if [ -z "$value" ]; then
    echo "ERROR: --${flag} is required" >&2
    usage
    exit 2
  fi
done
case "$MAJOR" in
  *[!0-9]*) echo "ERROR: --major must be a number (got '${MAJOR}')" >&2; usage; exit 2 ;;
esac
case "$POINT" in
  "${MAJOR}".*) ;;
  *) echo "ERROR: --point must be a ${MAJOR}.x release (got '${POINT}')" >&2; usage; exit 2 ;;
esac
case "$ISO_NAME" in
  */*) echo "ERROR: --iso is a filename under build/state/, not a path (got '${ISO_NAME}')" >&2; usage; exit 2 ;;
esac
case "$DOMAIN_TYPE" in
  kvm|qemu) ;;
  *) echo "ERROR: --domain-type must be 'kvm' or 'qemu' (got '${DOMAIN_TYPE}')" >&2; usage; exit 2 ;;
esac
case "$CPU_MODE" in
  host-passthrough|maximum) ;;
  *) echo "ERROR: --cpu-mode must be 'host-passthrough' or 'maximum' (got '${CPU_MODE}')" >&2; usage; exit 2 ;;
esac

BOX_NAME="rhel/${MAJOR}-x86_64"
BUILD_DOM="rhel${MAJOR}-build"
POOL_IMG="/var/lib/libvirt/images"
DISK="${POOL_IMG}/${BUILD_DOM}.qcow2"
CONSOLE="${POOL_IMG}/${BUILD_DOM}-console.log"
POOL_ISO="${POOL_IMG}/${ISO_NAME}"

# Resolve the MAIN-worktree build/ (host-durable), NOT a feature worktree's
# ephemeral build/. git-common-dir points at the main repo's .git from any
# worktree, so its parent is the main worktree root regardless of where we run.
common_dir="$(git rev-parse --git-common-dir)"
MAIN_ROOT="$(cd "$(dirname "$common_dir")" && pwd)"
BUILD_DIR="$MAIN_ROOT/build"
CACHE_DIR="${LAB_BOX_CACHE_DIR:-$BUILD_DIR/state/boxes}"
CACHE="$CACHE_DIR/rhel-${MAJOR}-x86_64.box"
mkdir -p "$CACHE_DIR"

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
if [ "$action" = REUSE ]; then
  # The base box carries no manifest hash, so its identity records "-" there.
  IDENTITY="$(box_reg_identity "$CACHE" -)"
fi
if [ "$DRY_RUN" = 1 ]; then
  if [ "$action" = REUSE ]; then
    echo "registration: $(box_reg_state "$BOX_NAME" "$IDENTITY")"
  fi
  echo "(dry-run; no action taken)"
  exit 0
fi

# --- Cheap path: keep (or register) the cached box and we are done (#1248). ---
if [ "$action" = REUSE ]; then
  box_reuse_register "$BOX_NAME" "$CACHE" "$IDENTITY" --force "$BOX_NAME" "$CACHE"
  echo "box ready (from cache): $BOX_NAME"
  exit 0
fi

# --- Expensive path (BUILD / FORCE-BUILD): the install (~45-90 min under TCG; minutes under KVM). ---
ISO="${RHEL_ISO:-}"
if [ -z "$ISO" ]; then
  c="$BUILD_DIR/state/${ISO_NAME}"
  [ -f "$c" ] && ISO="$c"
fi
test -n "$ISO" || {
  echo "ERROR: ${ISO_NAME} (RHEL ${POINT} DVD) not found in $BUILD_DIR/state (set RHEL_ISO)" >&2
  exit 1
}
WORK="$BUILD_DIR/state/rhel${MAJOR}-box"; mkdir -p "$WORK"

# 1. OEMDRV volume: anaconda auto-loads ks.cfg from a volume so labeled.
genisoimage -quiet -V OEMDRV -o "$WORK/oemdrv.iso" ks.cfg

# 2. Stage inputs where qemu (its own uid) can read them - the host mount
#    is not readable by the qemu user (diag-spike permission lesson). The small
#    OEMDRV is always copied; the 12.7G DVD is symlinked from a local-fs source
#    (cloud /vergil) or copied from a host-passthrough mount (Lima) (#337).
sudo cp "$WORK/oemdrv.iso" "${POOL_IMG}/oemdrv.iso"
../../scripts/stage-iso-into-pool.sh "$ISO" "$POOL_ISO"
# Clean slate so the build is retryable (#325): a prior build that failed AFTER
# `virsh define` (e.g. at start) leaves the build domain defined — which blocks both the
# disk re-create (if it were still running) and the re-define below. Tear it down
# first; idempotent (no-op when absent). The domain is undefined on success too, at the
# end — this just covers the failure path.
virsh -c qemu:///system destroy "$BUILD_DOM" 2>/dev/null || true
virsh -c qemu:///system undefine "$BUILD_DOM" 2>/dev/null || true
sudo qemu-img create -f qcow2 "$DISK" 20G
sudo touch "$CONSOLE"
sudo chown 64055:993 "$DISK" "$CONSOLE"
# World-readable so the install heartbeat (await-install.sh) can tail the serial
# console without sudo; it is owned by the qemu uid above. (#331)
sudo chmod a+r "$CONSOLE"

# 3. Transient build domain (the #24 TCG recipe), wait for install poweroff.
sed -e "s|@ISO@|${POOL_ISO}|" \
  -e "s|@DOMAIN@|${BUILD_DOM}|g" \
  -e "s|@DISK@|${DISK}|" \
  -e "s|@CONSOLE@|${CONSOLE}|" \
  -e "s|@DOMAIN_TYPE@|${DOMAIN_TYPE}|" \
  -e "s|@CPU_MODE@|${CPU_MODE}|" \
  build-domain.xml.tpl > "$WORK/domain.xml"
# Ensure the vagrant-libvirt management network exists before the build domain
# attaches to it (#323). The plugin only auto-creates it on `vagrant up`, but this
# raw-virsh build runs first; net-up is idempotent, so a network a prior `vagrant up`
# already made is reused, not redefined.
../../scripts/net-up.sh vagrant-libvirt
virsh -c qemu:///system define "$WORK/domain.xml"
virsh -c qemu:///system start "$BUILD_DOM"
if [ "$DOMAIN_TYPE" = kvm ]; then
  echo "installing RHEL ${POINT} (KVM — native virtualization, much faster than the TCG path)..."
else
  echo "installing RHEL ${POINT} (TCG, expect 45-90 min)..."
fi
# Wait for the install to power the domain off, with an elapsed + latest-console-line
# heartbeat each poll instead of a silent sleep loop. (#331)
./await-install.sh "$BUILD_DOM" "$CONSOLE" 30

# 4. Package the box into the CACHE (compressed convert sheds install scratch).
sudo qemu-img convert -O qcow2 -c "$DISK" "$WORK/box.img.tmp"
sudo chown "$(id -u)" "$WORK/box.img.tmp"
mv "$WORK/box.img.tmp" "$WORK/box.img"
printf '{"provider":"libvirt","format":"qcow2","virtual_size":20}\n' > "$WORK/metadata.json"
tar -C "$WORK" -czf "$CACHE" metadata.json box.img
box_register "$BOX_NAME" "$(box_reg_identity "$CACHE" -)" --force "$BOX_NAME" "$CACHE"

# 5. Cleanup (keep the staged ISO for future rebuilds).
virsh -c qemu:///system undefine "$BUILD_DOM"
sudo rm -f "$DISK" "${POOL_IMG}/oemdrv.iso"
echo "box ready (built + cached): $BOX_NAME -> $CACHE"
