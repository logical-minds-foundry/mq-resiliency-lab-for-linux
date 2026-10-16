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
#
# NAME GUARD (#1570): because any accepted name becomes a real secret file, the
# script takes exactly one argument and no options, and mints only names listed
# in the lab/secret-names registry. An option-like argument (`--help`, `-h`, ...)
# or a wrong argument count prints usage and exits 2; an unregistered name (a
# caller typo, `get`, ...) exits 1. Both happen before anything is written.
set -euo pipefail
usage() {
  cat >&2 <<'EOF'
usage: lab-secret.sh <name>
  Print the persisted lab secret <name>, generating and saving it on first use.
  <name> must be registered in lab/secret-names. Takes no options.
EOF
}
if [ "$#" -ne 1 ]; then usage; exit 2; fi
NAME="$1"
case "$NAME" in
  -*) usage; exit 2 ;;
  '' | *[!a-z0-9_-]*) echo "lab-secret.sh: bad secret name: '$NAME'" >&2; exit 1 ;;
esac
REGISTRY="$(cd "$(dirname "$0")/.." && pwd)/secret-names"
if [ ! -r "$REGISTRY" ]; then
  echo "lab-secret.sh: secret-name registry not readable: $REGISTRY" >&2
  exit 1
fi
if ! grep -qxF -e "$NAME" "$REGISTRY"; then
  echo "lab-secret.sh: unknown secret name '$NAME': not registered in lab/secret-names ($REGISTRY). Refusing to mint it; register it there if it is a new lab secret." >&2
  exit 1
fi

# The shared state bucket, via the one authority (#1569) — never derived from this
# script's own location, which forked the lab's secrets in an unwired worktree.
# shellcheck source=lab/scripts/_build-bucket.sh
. "$(dirname "$0")/_build-bucket.sh"
STATE="$(mqlab_bucket state)"
DIR="$STATE/secrets"
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
