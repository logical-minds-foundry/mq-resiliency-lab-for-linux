#!/usr/bin/env bash
# lab/boxes/_manifest-hash.sh <box> --bake-stem S --mq-bearing 0|1 --os-pin P
#   - the bake-manifest hash for a fat box.
#
# A fat box is only worth REUSEing from cache while the inputs that shaped it are
# unchanged. This digests those inputs into a stable sha256 the builder stores
# beside the cached .box and re-checks on every run (#603, epic .github#70):
#
#   * the package/version pin set (ansible/group_vars/all/versions.yml) - the
#     mq/grafana/etc. versions baked in;
#   * the box's bake playbook PATH and CONTENT (ansible/bake-<stem>.yml, produced
#     by #602) - the recipe itself. Absent files still contribute their path, so
#     the hash flips the moment #602 lands the playbook;
#   * the TRANSITIVE bake ROLES the playbook pulls in (#649) - every file under
#     each role the bake include_role/import_role reaches, directly or through a
#     nested include (e.g. mq-exporter -> mq-install; rdqm-install ->
#     mq-diag-logging). The bake playbooks are thin: the real bake work
#     (tasks/install.yml, configure.yml, main.yml, templates/, defaults/,
#     handlers/, vars/, meta/) lives in those roles. Digesting only the playbook
#     + pins missed role-level bake edits (#642's inert-service change, #659's
#     guards), so build-fatbox.sh decided REUSE and the box silently drifted from
#     the code. Now a role-level bake change flips the hash and forces a rebuild.
#
#   * for the MQ-BEARING boxes only, the lab/mq-version pin CONTENT (#1087) - the
#     single authoritative MQ-version pin (mqlab.paths.mq_version_pin_path). The
#     baked MQ version is sourced from that pin via an Ansible
#     `lookup('file', '.../lab/mq-version')` in versions.yml, so the RESOLVED
#     version never appears in versions.yml's own text - digesting only
#     versions.yml missed a pin bump entirely (a flipped 9.4.5 -> 10.0 left an
#     MQ-bearing box at REUSE, so a bootstrap silently cloned the old-version box
#     and, via the skip-if-baked guard, came up on the old MQ). Folding the pin
#     content in flips those boxes to BUILD on a bump. The MQ-version-independent
#     commons boxes (obs/infra, pcmk) DELIBERATELY exclude it, so a
#     version bump never spuriously rebakes them.
#
#   * the OS PIN (epic .github#280) - the base box plus its point release or box-version
#     pin (e.g. <base_box>@<pin>), passed as --os-pin. A re-pin (a new point release, a
#     new cloud-image version) flips the digest and forces a rebake.
#
# Dumb hasher (epic .github#280): every input that used to come from a hand-written
# box table is now a REQUIRED flag, supplied by mqlab from the catalog
# (lab/versions.yaml) through build-fatbox.sh. The bake playbook is named by its STEM
# (--bake-stem; ansible/bake-<stem>.yml), which is shorter than the box name.
# Pre-#649 the path was built straight from the box name (ansible/bake-<box>.yml),
# which matched NO existing file - so the digest silently omitted the bake playbook
# and every role. A missing flag is a usage error (exit 2), never an empty digest.
#
# Role-set precision vs. safety: we resolve the roles the box actually bakes
# (transitive closure from the playbook) rather than hashing the whole
# ansible/roles/ tree, so editing a per-run-only CONFIGURE role (app-requester,
# mq-qmgr, ...) does NOT spuriously invalidate every fat box's cache - which
# would undercut the fat-box optimisation on every cold rebuild. The closure is
# deliberately OVER-inclusive at the margins (it follows every `name:`/`role:`
# token that names a real ansible/roles/<x> directory, so a role reached only
# behind a `when:` still counts): under-inclusion causes silent drift (the bug
# this fixes), while over-inclusion causes at most one spurious rebuild (safe).
#
# Inputs are read RELATIVE TO THIS SCRIPT (the worktree), not the host-durable
# main-worktree cache: they are code, so a feature branch that edits a pin, a
# bake recipe, or a baked role gets a distinct hash and forces a rebuild.
set -euo pipefail

USAGE="usage: _manifest-hash.sh <box> --bake-stem <stem> --mq-bearing <0|1> --os-pin <base@pin>"
BOX="${1:-}"
case "$BOX" in
  "" | --*) echo "ERROR: <box> is required" >&2; echo "$USAGE" >&2; exit 2 ;;
esac
shift
BAKE_STEM=""
MQ_BEARING=""
OS_PIN=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bake-stem) BAKE_STEM="${2:-}"; shift ;;
    --mq-bearing) MQ_BEARING="${2:-}"; shift ;;
    --os-pin) OS_PIN="${2:-}"; shift ;;
    *) echo "ERROR: unknown arg: $1" >&2; echo "$USAGE" >&2; exit 2 ;;
  esac
  shift
done
for flag in bake-stem mq-bearing os-pin; do
  case "$flag" in
    bake-stem) value="$BAKE_STEM" ;;
    mq-bearing) value="$MQ_BEARING" ;;
    os-pin) value="$OS_PIN" ;;
  esac
  if [ -z "$value" ]; then
    echo "ERROR: --${flag} is required" >&2
    echo "$USAGE" >&2
    exit 2
  fi
done
case "$MQ_BEARING" in
  0|1) ;;
  *) echo "ERROR: --mq-bearing must be 0 or 1 (got '${MQ_BEARING}')" >&2; exit 2 ;;
esac
cd "$(dirname "$0")"

PINS="../../ansible/group_vars/all/versions.yml"
# The single authoritative MQ-version pin (mqlab.paths.mq_version_pin_path), read
# relative to this script (the worktree) like every other input above.
MQ_VERSION_PIN="../../lab/mq-version"
BAKE_REL="ansible/bake-${BAKE_STEM}.yml"
BAKE="../../${BAKE_REL}"
ROLES_DIR="../../ansible/roles"
# A stem with no bake playbook is a hard error, never a silently empty digest (#649): the
# stem comes from the catalog (roles.<role>.bake), so a typo there must fail loudly.
if [ ! -f "$BAKE" ]; then
  echo "ERROR: no bake playbook for --bake-stem '${BAKE_STEM}': ${BAKE_REL} not found" >&2
  exit 2
fi

# Emit, one per line, the role names referenced on stdin: values of a `name:` or
# `role:` key that name an actual ansible/roles/<x> directory. Task/play names use
# `- name:` (a dash before the key) so they don't match this anchored pattern, and
# any that did wouldn't name a role dir anyway - so they're filtered out; a stray
# match would only over-include (safe). Hyphens/dots/underscores are role-name
# chars, so the token class includes them.
#
# Callers capture this in `$(...)`; guard both sites with `|| true`. The trailing
# `while read` exits non-zero at EOF, and grep exits non-zero when a file has no
# name:/role: lines at all - either would, under `set -e`+`pipefail`, abort the
# capturing assignment. All output is produced before that non-zero exit, so the
# capture is never truncated; only the status needs swallowing.
emit_role_refs() {
  grep -hoE '^[[:space:]]*(name|role):[[:space:]]*"?[A-Za-z0-9._-]+' \
    | sed -E 's/^[[:space:]]*(name|role):[[:space:]]*"?//' \
    | while IFS= read -r ref; do
        if [ -d "${ROLES_DIR}/${ref}" ]; then printf '%s\n' "$ref"; fi
      done
}

# Transitive closure of bake roles, seeded from the bake playbook and expanded by
# scanning each reached role's YAML for further role references (nested
# include_role, meta dependencies). BFS over a worklist; `seen` (a newline-
# delimited string, for bash-3.2 portability - no associative arrays) de-dupes and
# guarantees termination on cyclic references.
seen=""
resolved=""
worklist=""
if [ -f "$BAKE" ]; then
  worklist="$(emit_role_refs < "$BAKE")" || true
fi
while [ -n "$worklist" ]; do
  # Pop the first line with parameter expansion, not `printf | head -n1`: under
  # pipefail, head exiting early can SIGPIPE a printf still writing a long worklist
  # (exit 141), which set -e turned into an intermittent hash failure (#1274).
  role="${worklist%%$'\n'*}"
  case "$worklist" in
    *$'\n'*) worklist="${worklist#*$'\n'}" ;;
    *) worklist="" ;;
  esac
  if [ -z "$role" ]; then continue; fi
  # Membership by pattern match, not `printf | grep -q` (grep -q exits on the first
  # match and can SIGPIPE the printf, misreporting a seen role as unseen).
  case "$seen"$'\n' in
    *$'\n'"$role"$'\n'*) continue ;;
  esac
  seen="$(printf '%s\n%s' "$seen" "$role")"
  resolved="$(printf '%s\n%s' "$resolved" "$role")"
  refs="$(find "${ROLES_DIR}/${role}" -type f \( -name '*.yml' -o -name '*.yaml' \) \
            -exec cat {} + 2>/dev/null | emit_role_refs)" || true
  if [ -n "$refs" ]; then worklist="$(printf '%s\n%s' "$worklist" "$refs")"; fi
done

{
  printf 'box=%s\n' "$BOX"
  printf 'bake=%s\n' "$BAKE_REL"
  printf 'os_pin=%s\n' "$OS_PIN"
  if [ -f "$PINS" ]; then cat "$PINS"; fi
  # MQ-bearing boxes only: the resolved MQ version lives in lab/mq-version (a
  # lookup() in versions.yml), so fold its content in — a pin bump must flip the
  # digest and force a rebake (#1087). Commons boxes skip this block entirely.
  if [ "$MQ_BEARING" = 1 ] && [ -f "$MQ_VERSION_PIN" ]; then
    printf 'mq_version_pin='
    cat "$MQ_VERSION_PIN"
  fi
  if [ -f "$BAKE" ]; then cat "$BAKE"; fi
  # Every file of every resolved role, name-sorted then path-sorted for a stable
  # digest. Paths (not just content) enter the stream, so a rename/move flips it.
  printf '%s\n' "$resolved" | sort -u | while IFS= read -r role; do
    [ -z "$role" ] && continue
    printf 'role=%s\n' "$role"
    find "${ROLES_DIR}/${role}" -type f | sort | while IFS= read -r file; do
      printf 'file=%s\n' "${file#../../}"
      cat "$file"
    done
  done
} | sha256sum | cut -d' ' -f1
