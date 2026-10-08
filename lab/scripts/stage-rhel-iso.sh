#!/usr/bin/env bash
# lab/scripts/stage-rhel-iso.sh - stage RHEL DVD ISOs into the libvirt storage pool so
# RHEL guests can attach them as their offline BaseOS+AppStream dnf repo (#276/#291).
# For each ISO it resolves the operator-supplied source, then runs
# stage-iso-into-pool.sh, which SYMLINKS the pool entry to the source on a local fs
# (the cloud box's /vergil — no ISO-sized copy on the small boot disk) and COPIES
# only when the source is a host-passthrough mount the nested qemu can't read through
# (Lima's build/). (#337) Idempotent; the pool is wiped on a VM rebuild, so this
# re-stages on a fresh box. Credential-less (no GitHub/Claude creds).
#
# Usage:   stage-rhel-iso.sh --iso <file> [--iso <file> ...]
#          stage-rhel-iso.sh --catalog
#          MQLAB_RHEL_ISO=/path/to/<downloaded>.iso stage-rhel-iso.sh --iso <file>
#
# --iso <file> names a DVD ISO filename (the catalog's os.rhel.<major>.iso, which mqlab
# passes). It names both the source under build/state/ and the pool entry the RHEL
# guests attach. Repeat it to stage several. --catalog stages every os.rhel.<major>.iso
# in lab/versions.yaml (read by rhel-catalog-isos.sh). Each ISO is staged in turn; the
# run exits non-zero naming every ISO that failed (#1395). The MQLAB_RHEL_ISO /
# RHEL_ISO source override names ONE file, so it is refused with --catalog or with
# more than one --iso.
set -euo pipefail
here="$(dirname "$0")"

usage() {
  echo "usage: stage-rhel-iso.sh --iso <file> [--iso <file> ...] | --catalog" >&2
  echo "       (<file> is an os.rhel.<major>.iso value in lab/versions.yaml)" >&2
}

ISOS=()
CATALOG=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso)
      case "${2:-}" in
        "") echo "ERROR: --iso needs a filename" >&2; usage; exit 2 ;;
        */*) echo "ERROR: --iso is a filename under build/state/, not a path (got '$2')" >&2; usage; exit 2 ;;
      esac
      ISOS+=("$2"); shift ;;
    --catalog) CATALOG=1 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

OVERRIDE="${MQLAB_RHEL_ISO:-${RHEL_ISO:-}}"
if [ "$CATALOG" = 1 ]; then
  if [ "${#ISOS[@]}" -gt 0 ]; then
    echo "ERROR: --catalog and --iso are mutually exclusive" >&2; usage; exit 2
  fi
  if [ -n "$OVERRIDE" ]; then
    echo "ERROR: MQLAB_RHEL_ISO/RHEL_ISO names one source file; it is ambiguous with --catalog." >&2
    echo "       unset it, or stage a single ISO with --iso <file>." >&2
    exit 2
  fi
  # Capture-then-check: the reader fails loudly on a catalog it cannot read.
  listing="$(bash "$here/rhel-catalog-isos.sh")" || {
    echo "ERROR: could not resolve the RHEL DVD list from the catalog (see above)." >&2
    exit 1
  }
  while IFS= read -r iso; do
    ISOS+=("$iso")
  done <<<"$listing"
fi
if [ "${#ISOS[@]}" -eq 0 ]; then
  echo "ERROR: --iso or --catalog is required" >&2; usage; exit 2
fi
if [ "${#ISOS[@]}" -gt 1 ] && [ -n "$OVERRIDE" ]; then
  echo "ERROR: MQLAB_RHEL_ISO/RHEL_ISO names one source file; it is ambiguous with ${#ISOS[@]} --iso values." >&2
  echo "       unset it, or stage a single ISO with --iso <file>." >&2
  exit 2
fi

main_root=""
if [ -z "$OVERRIDE" ]; then
  # The MAIN worktree's host-durable build/ (git-common-dir finds main from any worktree).
  common_dir="$(git rev-parse --git-common-dir)"
  main_root="$(cd "$(dirname "$common_dir")" && pwd)"
fi

# stage_one <iso>: resolve the source and stage it into the pool; non-zero on failure.
stage_one() {
  local iso="$1" src
  src="${OVERRIDE:-$main_root/build/state/$iso}"
  if [ ! -f "$src" ]; then
    echo "ERROR: RHEL DVD ISO not found at $src" >&2
    echo "       set MQLAB_RHEL_ISO=/path/to/${iso}, or drop it in build/state/." >&2
    return 1
  fi
  "$here/stage-iso-into-pool.sh" "$src" "/var/lib/libvirt/images/${iso}"
}

FAILED=()
for iso in "${ISOS[@]}"; do
  [ "${#ISOS[@]}" -gt 1 ] && echo "==> staging $iso"
  stage_one "$iso" || FAILED+=("$iso")
done
if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "ERROR: failed to stage ${#FAILED[@]} of ${#ISOS[@]} RHEL DVD ISO(s): ${FAILED[*]}" >&2
  exit 1
fi
[ "${#ISOS[@]}" -gt 1 ] && echo "staged all ${#ISOS[@]} RHEL DVD ISOs."
exit 0
