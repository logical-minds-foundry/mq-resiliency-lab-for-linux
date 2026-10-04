#!/usr/bin/env bash
# lab/boxes/_grow-build-disk.sh <image> <GiB> - grow a fat-box build disk to <GiB>,
# NEVER shrink it (#1340).
#
# build-fatbox.sh grows the transient build disk so the heaviest Ubuntu bakes stop
# overflowing the ~8.7G cloud image (#1144/#1147). The bases differ in size: the Ubuntu
# cloud image is ~8.7G, but the RHEL base is created at 20G (rhel/build-box.sh). An
# unconditional absolute resize to 18G shrinks the RHEL disk, which qemu-img refuses, and
# that killed every RHEL fat-box bake. So resize only when the image is SMALLER than the
# target. A larger base is left as-is; it already fits the box's virtual_size:20.
#
# Reading the size goes through `qemu-img info --output=json` (top-level "virtual-size";
# a qcow2's nested child node carries its own, smaller one, hence a real JSON parse). A
# failure to read it is fatal: never resize blind.
set -euo pipefail

IMG="${1:?usage: _grow-build-disk.sh <image> <GiB>}"
GIB="${2:?usage: _grow-build-disk.sh <image> <GiB>}"
[[ "$GIB" =~ ^[0-9]+$ ]] || { echo "ERROR: <GiB> must be a whole number, got '$GIB'" >&2; exit 2; }

TARGET=$((GIB * 1024 * 1024 * 1024))
CUR="$(sudo qemu-img info --output=json "$IMG" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["virtual-size"])')"
[[ "$CUR" =~ ^[0-9]+$ ]] || { echo "ERROR: unreadable virtual size for $IMG: '$CUR'" >&2; exit 1; }

if [ "$CUR" -lt "$TARGET" ]; then
  echo "build disk: growing $IMG from $CUR to $TARGET bytes (${GIB}G)"
  sudo qemu-img resize "$IMG" "${GIB}G"
else
  echo "build disk: $IMG is already $CUR bytes (>= ${GIB}G); leaving it (grow-only, #1340)"
fi
