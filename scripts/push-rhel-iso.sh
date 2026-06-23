#!/usr/bin/env bash
# scripts/push-rhel-iso.sh - push the operator-supplied RHEL 9.6 DVD ISO from the
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
# Usage:   ./scripts/push-rhel-iso.sh
#          MQLAB_RHEL_ISO=/path/to/rhel-9.6-x86_64-dvd.iso ./scripts/push-rhel-iso.sh
set -euo pipefail

# Off-platform target (GCP). Project is stable; instance/zone are resolved by
# name-match below so a `vrg-vm rebuild` (new instance suffix) does not break this.
PROJECT_ID="vergil-project-500213-a1"
NAME_MATCH="mq-resiliency-lab"
VM_USER="ubuntu"
VM_REPO_DIR="/vergil/projects/logical-minds-foundry/mq-resiliency-lab-for-linux"

# Source ISO: env override, else the MAIN worktree's host build/state/ (git-common-dir
# finds main from any worktree, exactly like lab/scripts/stage-rhel-iso.sh).
SRC="${MQLAB_RHEL_ISO:-${RHEL_ISO:-}}"
if [ -z "$SRC" ]; then
  main_root="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
  SRC="$main_root/build/state/rhel-9.6-x86_64-dvd.iso"
fi
test -f "$SRC" || {
  echo "ERROR: RHEL DVD ISO not found at $SRC" >&2
  echo "       set MQLAB_RHEL_ISO=/path/to/rhel-9.6-x86_64-dvd.iso, or drop it in build/state/." >&2
  exit 1
}

command -v gcloud >/dev/null || { echo "ERROR: gcloud not on PATH (run this on the macOS host)" >&2; exit 1; }

ISO_NAME="$(basename "$SRC")"
DEST_DIR="$VM_REPO_DIR/build/state"

# Resolve instance + zone by name-match (survives a rebuild's changed suffix).
read -r INSTANCE ZONE < <(gcloud compute instances list \
  --project="$PROJECT_ID" --filter="name~${NAME_MATCH}" --format='value(name,zone)' | head -1)
[ -n "${INSTANCE:-}" ] || {
  echo "ERROR: no running instance matching '${NAME_MATCH}' in ${PROJECT_ID}" >&2
  exit 1
}

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
gcloud compute scp "$SRC" "$target:$DEST_DIR/" "${gc[@]}"

echo "==> verifying on the VM"
gcloud compute ssh "$target" "${gc[@]}" --command "ls -lh '$DEST_DIR/$ISO_NAME'"
echo "done."
