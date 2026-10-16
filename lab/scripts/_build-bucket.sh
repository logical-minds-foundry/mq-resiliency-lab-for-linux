# lab/scripts/_build-bucket.sh — sourced helper: resolve a build/ bucket through the one
# authority (#1569). Never derive build/<bucket> from a script's own location: in a git
# worktree whose shared-bucket symlinks are not wired yet, `$(dirname "$0")/../../build/
# state` is a fresh local directory, and writing there forks the live-lab state (a
# secret regenerated there no longer matches the running cluster).
#
#   mqlab_bucket <cache|state|work|temp>   # prints the bucket's absolute path
#
# Resolution: MQLAB_<BUCKET>_DIR, which every mqlab verb exports to the commands it
# spawns, else `mqlab build path <bucket>` (the same buildenv authority). The bucket must
# already exist — `mqlab build ensure` creates it; this helper never creates a tree. Any
# failure is loud (non-zero, with the reason on stderr), never a silent fallback.
# Sourced by bash scripts that run under `set -euo pipefail`; defines a function only.

mqlab_bucket() {
  local bucket="${1:-}" var path
  case "$bucket" in
    cache | state | work | temp) ;;
    *)
      echo "mqlab_bucket: unknown bucket '$bucket' (expected cache|state|work|temp)" >&2
      return 2
      ;;
  esac
  var="MQLAB_$(tr '[:lower:]' '[:upper:]' <<<"$bucket")_DIR"
  path="${!var:-}"
  if [ -z "$path" ]; then
    if ! command -v mqlab >/dev/null 2>&1; then
      echo "ERROR: cannot resolve the build/$bucket bucket: $var is unset and mqlab is not on PATH." >&2
      echo "       Run this through mqlab (or 'uv run'), or export $var=\"\$(uv run mqlab build path $bucket)\"." >&2
      return 1
    fi
    if ! path="$(mqlab build path "$bucket")"; then
      echo "ERROR: cannot resolve the build/$bucket bucket: 'mqlab build path $bucket' failed" >&2
      return 1
    fi
  fi
  case "$path" in
    /*) ;;
    *)
      echo "ERROR: the build/$bucket bucket '$path' is not an absolute path" >&2
      return 1
      ;;
  esac
  if [ ! -d "$path" ]; then
    echo "ERROR: the build/$bucket bucket $path does not exist; run 'uv run mqlab build ensure' first." >&2
    echo "       Refusing to create it here: a tree made outside mqlab forks the shared lab." >&2
    return 1
  fi
  printf '%s\n' "$path"
}
