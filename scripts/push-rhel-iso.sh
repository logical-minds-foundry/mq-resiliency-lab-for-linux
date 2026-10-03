#!/usr/bin/env bash
# scripts/push-rhel-iso.sh - push an operator-supplied RHEL DVD ISO from the
# host build/state/ to an off-platform (GCP) lab VM's build/state/, over the VM's
# private IAP tunnel. The off-platform VM's build/ is a persistent volume (NOT the
# host mount), so the ~12.7G ISO must be pushed once after the volume is created;
# it then persists until the volume is destroyed. gcloud auth is host-local (macOS);
# the VM needs nothing installed beyond its stock sshd/git/coreutils.
#
# Source resolution mirrors lab/scripts/stage-rhel-iso.sh (git-common-dir +
# MQLAB_RHEL_ISO override), so the two agree on where the ISO lives. No mqlab
# dependency on either side. Idempotent: a re-run no-ops when the VM already holds
# a same-size copy.
#
# Usage:   ./scripts/push-rhel-iso.sh --iso <file>
#          MQLAB_RHEL_ISO=/path/to/<downloaded>.iso ./scripts/push-rhel-iso.sh --iso <file>
#
# --iso <file> is REQUIRED: the DVD ISO filename the lab expects, i.e. the catalog's
# os.rhel.<major>.iso in lab/versions.yaml. It names the source under build/state/
# (unless MQLAB_RHEL_ISO overrides the source path) and the file written on the VM.
set -euo pipefail

usage() { echo "usage: push-rhel-iso.sh --iso <file>   (the os.rhel.<major>.iso value in lab/versions.yaml)" >&2; }
ISO_NAME=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso) ISO_NAME="${2:-}"; shift ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage; exit 2 ;;
  esac
  shift
done
case "$ISO_NAME" in
  "") echo "ERROR: --iso is required" >&2; usage; exit 2 ;;
  */*) echo "ERROR: --iso is a filename, not a path (got '${ISO_NAME}')" >&2; usage; exit 2 ;;
esac

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

# Source ISO: env override, else the MAIN worktree's host build/state/ (git-common-dir
# finds main from any worktree, exactly like lab/scripts/stage-rhel-iso.sh).
SRC="${MQLAB_RHEL_ISO:-${RHEL_ISO:-}}"
if [ -z "$SRC" ]; then
  main_root="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
  SRC="$main_root/build/state/${ISO_NAME}"
fi
test -f "$SRC" || {
  echo "ERROR: RHEL DVD ISO not found at $SRC" >&2
  echo "       set MQLAB_RHEL_ISO=/path/to/<downloaded>.iso, or drop ${ISO_NAME} in build/state/." >&2
  exit 1
}

command -v gcloud >/dev/null || { echo "ERROR: gcloud not on PATH (run this on the macOS host)" >&2; exit 1; }

DEST_DIR="$VM_REPO_DIR/build/state"

# Resolve instance + zone by Vergil labels. Capture the query into a variable FIRST,
# then check it: piping straight into `read … < <(…)` under `set -e` aborts the whole
# script the moment the query is empty (read returns non-zero on EOF), silently —
# before the error below can ever fire. Capture-then-check makes the failure loud. (#329)
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

# Idempotent skip: same-size copy already staged -> nothing to do.
local_size="$(stat -f%z "$SRC" 2>/dev/null || stat -c%s "$SRC")"
remote_size="$(gcloud compute ssh "$target" "${gc[@]}" \
  --command "stat -c%s '$DEST_DIR/$ISO_NAME' 2>/dev/null || echo 0")"
if [ "$local_size" = "$remote_size" ]; then
  echo "already staged: $DEST_DIR/$ISO_NAME ($local_size bytes); nothing to do."
  exit 0
fi

echo "==> ensuring $DEST_DIR exists on the VM"
gcloud compute ssh "$target" "${gc[@]}" --command "mkdir -p '$DEST_DIR'"

echo "==> copying $ISO_NAME ($(du -h "$SRC" | cut -f1)) -> $DEST_DIR/  (this takes a while at ~12.7G)"
gcloud compute scp "$SRC" "$target:$DEST_DIR/$ISO_NAME" "${gc[@]}"

echo "==> verifying on the VM"
gcloud compute ssh "$target" "${gc[@]}" --command "ls -lh '$DEST_DIR/$ISO_NAME'"
echo "done."
