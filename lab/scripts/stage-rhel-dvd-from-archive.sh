#!/usr/bin/env bash
# lab/scripts/stage-rhel-dvd-from-archive.sh — host-side auto-stage of the RHEL DVD
# ISO(s) from the operator's static local archive DIRECTORY into the lab's build/state/
# bucket (#674, epic logical-minds-foundry/.github#91). The RHEL DVD is the one lab
# artifact a cold boot cannot fetch (Red Hat requires authentication); the operator
# downloads it ONCE per RHEL version and keeps it in a stable archive dir, and this
# rsync syncs it into build/state/ so a wiped /vergil never forces a manual re-copy.
#
# Intended to run as a vrg-vm post-build hook (declared, inert, in vergil.toml), but the
# hook capability does not exist yet (vergil-project/vergil-tooling#2407); until it ships,
# run this by hand after a nuclear rebuild.
#
# Idempotent: rsync --ignore-existing never re-copies an already-staged same-name ISO,
# so re-runs are cheap despite the ~12.7 GB blob size (the DVD rarely changes).
# Credential-less: no GitHub/Claude creds and no Red Hat auth — the download is manual.
#
# Usage: stage-rhel-dvd-from-archive.sh [--dry-run]
#   --dry-run                    print the planned rsync and copy nothing
#   MQLAB_RHEL_DVD_ARCHIVE=DIR   override the source archive directory
set -euo pipefail

DRY_RUN=0
case "${1:-}" in
  --dry-run) DRY_RUN=1 ;;
  "") ;;
  *) echo "usage: $(basename "$0") [--dry-run]" >&2; exit 2 ;;
esac

# Source: the operator's static DVD archive directory. Env override, else the documented
# default under the operator's home media archive (see docs/development/box-model.md).
SRC="${MQLAB_RHEL_DVD_ARCHIVE:-$HOME/dev/software/rhel-dvds}"

# Destination: the MAIN worktree's host-durable build/state/. git-common-dir finds the
# main worktree from any worktree — the same resolution stage-rhel-iso.sh uses, so both
# scripts agree on where the ISO lives.
common_dir="$(git rev-parse --git-common-dir)"
main_root="$(cd "$(dirname "$common_dir")" && pwd)"
DST="$main_root/build/state"

test -d "$SRC" || {
  echo "ERROR: RHEL DVD archive directory not found: $SRC" >&2
  echo "       Download the RHEL DVD ISO(s) once from Red Hat and keep them there," >&2
  echo "       or set MQLAB_RHEL_DVD_ARCHIVE=/path/to/your/dvd/archive." >&2
  exit 1
}

# Collect the DVD ISOs in the archive (nullglob: no match -> empty array, not a literal
# "*.iso" that rsync would choke on).
shopt -s nullglob
isos=("$SRC"/*.iso)
shopt -u nullglob
if [ "${#isos[@]}" -eq 0 ]; then
  echo "ERROR: no *.iso files found in RHEL DVD archive: $SRC" >&2
  echo "       Drop the RHEL DVD ISO(s) there, named to match the lab's canonical" >&2
  echo "       filename (e.g. rhel-9.6-x86_64-dvd.iso)." >&2
  exit 1
fi

# -a archive; --ignore-existing so an already-staged same-name ISO is never re-copied
# (idempotency + cheapness despite the blob size). rsync preserves the source filename,
# so the archived ISO must already carry the lab's canonical name.
RSYNC=(rsync -a --ignore-existing "${isos[@]}" "$DST/")

if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN — planned rsync (nothing copied):"
  printf '  %q' "${RSYNC[@]}"; echo
  exec "${RSYNC[@]}" --dry-run
fi

mkdir -p "$DST"
echo "staging RHEL DVD archive: $SRC -> $DST"
"${RSYNC[@]}"
echo "staged ${#isos[@]} RHEL DVD ISO(s) into $DST"
