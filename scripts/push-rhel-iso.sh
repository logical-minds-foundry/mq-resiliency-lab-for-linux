#!/usr/bin/env bash
# scripts/push-rhel-iso.sh - push operator-supplied RHEL DVD ISOs from the host
# build/state/ to an off-platform (GCP) lab VM's build/state/, over the VM's private
# IAP tunnel. The off-platform VM's build/ is a persistent volume (NOT the host
# mount), so each ~12.7G ISO must be pushed once after the volume is created; it then
# persists until the volume is destroyed. gcloud auth is host-local (macOS); the VM
# needs nothing installed beyond its stock sshd/git/coreutils.
#
# Runs on the macOS host: bash 3.2 and BSD coreutils only, no mqlab. Source resolution
# mirrors lab/scripts/stage-rhel-iso.sh (git-common-dir + MQLAB_RHEL_ISO override), so
# the two agree on where an ISO lives. Idempotent per ISO: a re-run skips any ISO the
# VM already holds as a same-size copy.
#
# Usage:   ./scripts/push-rhel-iso.sh --iso <file> [--iso <file> ...]
#          ./scripts/push-rhel-iso.sh --catalog
#          MQLAB_RHEL_ISO=/path/to/<downloaded>.iso ./scripts/push-rhel-iso.sh --iso <file>
#
# --iso <file> names a DVD ISO filename the lab expects, i.e. a catalog
# os.rhel.<major>.iso in lab/versions.yaml. It names the source under build/state/
# (unless MQLAB_RHEL_ISO overrides the source path) and the file written on the VM.
# Repeat it to push several. --catalog pushes every os.rhel.<major>.iso in the
# catalog (read by lab/scripts/rhel-catalog-isos.sh, which needs no python/PyYAML).
# The VM is looked up once; each ISO is then pushed in turn, and the run exits
# non-zero naming every ISO that failed (#1395). The MQLAB_RHEL_ISO / RHEL_ISO
# override names ONE file, so it is refused with --catalog or more than one --iso.
set -euo pipefail
here="$(dirname "$0")"

usage() {
  echo "usage: push-rhel-iso.sh --iso <file> [--iso <file> ...] | --catalog" >&2
  echo "       (<file> is an os.rhel.<major>.iso value in lab/versions.yaml)" >&2
}

ISOS=()
CATALOG=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso)
      case "${2:-}" in
        "") echo "ERROR: --iso needs a filename" >&2; usage; exit 2 ;;
        */*) echo "ERROR: --iso is a filename, not a path (got '$2')" >&2; usage; exit 2 ;;
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
    echo "       unset it, or push a single ISO with --iso <file>." >&2
    exit 2
  fi
  # Capture-then-check: the reader fails loudly on a catalog it cannot read.
  listing="$(bash "$here/../lab/scripts/rhel-catalog-isos.sh")" || {
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
  echo "       unset it, or push a single ISO with --iso <file>." >&2
  exit 2
fi

# Off-platform target (GCP). Project is stable. The box is resolved by its Vergil
# LABELS below, not by name: off-platform cloud resources are named with an opaque
# vrg-<hash> (org/repo/identity live in labels, not the name), so a name-substring
# match finds nothing — and labels also survive a `vrg-vm rebuild`'s changed suffix.
# (#329)
PROJECT_ID="vergil-project-500213-a1"
LABEL_ORG="logical-minds-foundry"
LABEL_REPO="mq-resiliency-lab-for-linux"
LABEL_IDENTITY="vergil-user"
VM_USER="ubuntu"
VM_REPO_DIR="/vergil/projects/logical-minds-foundry/mq-resiliency-lab-for-linux"
DEST_DIR="$VM_REPO_DIR/build/state"

# Source ISOs: the env override (single ISO only, enforced above), else the MAIN
# worktree's host build/state/ (git-common-dir finds main from any worktree, exactly
# like lab/scripts/stage-rhel-iso.sh). Resolve every source before touching the cloud:
# a missing source is a per-ISO failure, not a reason to skip the others.
main_root=""
if [ -z "$OVERRIDE" ]; then
  main_root="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
fi
FAILED=()
PUSH=()
for iso in "${ISOS[@]}"; do
  src="${OVERRIDE:-$main_root/build/state/$iso}"
  if [ -f "$src" ]; then
    PUSH+=("$iso")
  else
    echo "ERROR: RHEL DVD ISO not found at $src" >&2
    echo "       set MQLAB_RHEL_ISO=/path/to/<downloaded>.iso, or drop ${iso} in build/state/." >&2
    FAILED+=("$iso")
  fi
done

report_and_exit() {
  if [ "${#FAILED[@]}" -gt 0 ]; then
    echo "ERROR: failed to push ${#FAILED[@]} of ${#ISOS[@]} RHEL DVD ISO(s): ${FAILED[*]}" >&2
    exit 1
  fi
  [ "${#ISOS[@]}" -gt 1 ] && echo "all ${#ISOS[@]} RHEL DVD ISOs are on the VM."
  exit 0
}
[ "${#PUSH[@]}" -gt 0 ] || report_and_exit

command -v gcloud >/dev/null || { echo "ERROR: gcloud not on PATH (run this on the macOS host)" >&2; exit 1; }

# Resolve instance + zone by Vergil labels, ONCE for every ISO. Capture the query into
# a variable FIRST, then check it: piping straight into `read … < <(…)` under `set -e`
# aborts the whole script the moment the query is empty (read returns non-zero on
# EOF), silently — before the error below can ever fire. Capture-then-check makes the
# failure loud. (#329)
label_filter="labels.vergil-org=${LABEL_ORG} AND labels.vergil-repo=${LABEL_REPO}"
label_filter="${label_filter} AND labels.vergil-identity=${LABEL_IDENTITY}"
matches="$(gcloud compute instances list \
  --project="$PROJECT_ID" --filter="$label_filter" --format='value(name,zone)')" || {
  echo "ERROR: 'gcloud compute instances list' failed (check auth / project ${PROJECT_ID})." >&2
  exit 1
}
if [ -z "$matches" ]; then
  echo "ERROR: no off-platform VM found for ${LABEL_IDENTITY} ${LABEL_ORG}/${LABEL_REPO}" >&2
  echo "       in project ${PROJECT_ID}. Is it created? Check: vrg-vm volumes" >&2
  exit 1
fi
read -r INSTANCE ZONE <<<"$(printf '%s\n' "$matches" | head -1)"

target="${VM_USER}@${INSTANCE}"
gc=(--zone="$ZONE" --project="$PROJECT_ID" --tunnel-through-iap)
echo "==> target: $INSTANCE ($ZONE)"

# push_one <iso>: push one ISO to the VM; non-zero (after saying why) on any failure.
# Runs as an `||` operand, where set -e is off, so every step checks its own status.
push_one() {
  local iso="$1" src local_size remote_size
  src="${OVERRIDE:-$main_root/build/state/$iso}"
  # Idempotent skip: same-size copy already staged -> nothing to do.
  local_size="$(stat -f%z "$src" 2>/dev/null || stat -c%s "$src")" || {
    echo "ERROR: cannot stat $src" >&2; return 1
  }
  remote_size="$(gcloud compute ssh "$target" "${gc[@]}" \
    --command "stat -c%s '$DEST_DIR/$iso' 2>/dev/null || echo 0")" || {
    echo "ERROR: remote size probe for $iso failed" >&2; return 1
  }
  if [ "$local_size" = "$remote_size" ]; then
    echo "already staged: $DEST_DIR/$iso ($local_size bytes); nothing to do."
    return 0
  fi

  echo "==> ensuring $DEST_DIR exists on the VM"
  gcloud compute ssh "$target" "${gc[@]}" --command "mkdir -p '$DEST_DIR'" || {
    echo "ERROR: could not create $DEST_DIR on the VM" >&2; return 1
  }

  echo "==> copying $iso ($(du -h "$src" | cut -f1)) -> $DEST_DIR/  (this takes a while at ~12.7G)"
  gcloud compute scp "$src" "$target:$DEST_DIR/$iso" "${gc[@]}" || {
    echo "ERROR: copy of $iso to the VM failed" >&2; return 1
  }

  echo "==> verifying on the VM"
  gcloud compute ssh "$target" "${gc[@]}" --command "ls -lh '$DEST_DIR/$iso'" || {
    echo "ERROR: $DEST_DIR/$iso is not on the VM after the copy" >&2; return 1
  }
  echo "done: $iso"
}

for iso in "${PUSH[@]}"; do
  [ "${#ISOS[@]}" -gt 1 ] && echo "==> $iso"
  push_one "$iso" || FAILED+=("$iso")
done
report_and_exit
