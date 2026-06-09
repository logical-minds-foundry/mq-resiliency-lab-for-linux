#!/usr/bin/env bash
# lab/scripts/lab-secret.sh — auto-generate and persist a named lab secret,
# then print it. Idempotent: generates on first use, returns the same value
# forever after.
#
# WHY THIS EXISTS: the lab is a disposable virtual illusion — its internal
# secrets (e.g. the Pacemaker `hacluster` password, used only for pcs auth
# between nodes) carry no security weight. The original design injected that
# password via an ephemeral env var (PCMK_HACLUSTER_PASSWORD); when the session
# that set it ended, the value was *lost*, leaving a running cluster whose
# password no one knew and a re-provision with nothing to supply. The fix is to
# treat such secrets like the fence key: auto-generate once, persist in the
# gitignored, host-mounted build/ tree (survives VM rebuilds), and reuse. A
# true ground-zero wipe of build/ simply regenerates them — full reproducibility
# with no human in the loop.
#
# Usage: lab-secret.sh <name>     # prints the value, creating+saving if absent
set -euo pipefail
NAME="${1:?usage: lab-secret.sh <name>}"
case "$NAME" in *[!a-z0-9_-]*) echo "bad secret name: $NAME" >&2; exit 1 ;; esac

DIR="$(cd "$(dirname "$0")/../.." && pwd)/build/secrets"
mkdir -p "$DIR"; chmod 700 "$DIR"
F="$DIR/$NAME"
if [ ! -s "$F" ]; then
  # 32 hex chars, shell/url-safe. Single command (no pipe) on purpose: a
  # `... | head -c 32` here would SIGPIPE the upstream and, under pipefail,
  # abort the script before the first value is returned (the inventory.sh
  # footgun). The value is irrelevant; persistence is the point.
  openssl rand -hex 16 > "$F"
  chmod 600 "$F"
fi
cat "$F"
