#!/usr/bin/env bash
# lab/boxes/rhel96/build-box.sh - unattended RHEL 9.6 box build:
# DVD ISO + OEMDRV kickstart -> qcow2 -> vagrant-libvirt .box.
# One-time cost ~45-90 min under TCG. Requires the ISO in build/
# (worktree or project root - both are checked; override with RHEL_ISO).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd ../../.. && pwd)"
ISO="${RHEL_ISO:-}"
if [ -z "$ISO" ]; then
  for c in "$ROOT/build/rhel-9.6-x86_64-dvd.iso" "$ROOT/../../build/rhel-9.6-x86_64-dvd.iso"; do
    [ -f "$c" ] && ISO="$c" && break
  done
fi
test -n "$ISO" || { echo "ERROR: rhel-9.6-x86_64-dvd.iso not found in build/" >&2; exit 1; }
WORK="$ROOT/build/rhel96-box"; mkdir -p "$WORK"

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

# 4. Package the box (compressed convert also sheds install scratch).
sudo qemu-img convert -O qcow2 -c \
  /var/lib/libvirt/images/rhel96-build.qcow2 "$WORK/box.img.tmp"
sudo chown "$(id -u)" "$WORK/box.img.tmp"
mv "$WORK/box.img.tmp" "$WORK/box.img"
printf '{"provider":"libvirt","format":"qcow2","virtual_size":20}\n' > "$WORK/metadata.json"
tar -C "$WORK" -czf "$ROOT/build/rhel-9.6-x86_64-libvirt.box" metadata.json box.img
vagrant box add --force rhel/9.6-x86_64 "$ROOT/build/rhel-9.6-x86_64-libvirt.box"

# 5. Cleanup (keep the staged ISO for future rebuilds).
virsh -c qemu:///system undefine rhel96-build
sudo rm -f /var/lib/libvirt/images/rhel96-build.qcow2 /var/lib/libvirt/images/oemdrv.iso
echo "box ready: rhel/9.6-x86_64"
