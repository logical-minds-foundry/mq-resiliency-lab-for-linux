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
#   * the SHARED files outside ansible/roles/ that the closure includes (#1324) -
#     PATH and CONTENT of every existing file under ansible/ (not ansible/roles/)
#     that a reached role, the bake playbook, or another such shared file names in
#     a path literal: today the per-OS-version vars loader ansible/tasks/os-vars.yml,
#     which roles pull in as `include_tasks: ../../../tasks/os-vars.yml` (#1277).
#     The role closure alone missed it, so editing the loader left every box's hash
#     unchanged and the builder REUSEd a stale box - #649's bug, one level out.
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

# Emit, one per line, the SHARED task/vars files outside ansible/roles/ that the
# YAML file $1 references (#1324) - e.g. the per-OS-version loader that roles pull
# in as `include_tasks: ../../../tasks/os-vars.yml` (#1277). Every `.yml`/`.yaml`
# path literal on a non-comment line is a candidate, which covers include_tasks /
# import_tasks / include_vars / vars_files / import_playbook and their `file:`
# forms alike. Each candidate is resolved against the directories Ansible searches:
# the referencing file's own directory and the playbook dir (ansible/), plus, for a
# role file, that role's tasks/ dir (how a role's relative include resolves). A
# candidate is kept only if it is an existing regular file INSIDE ansible/ but
# OUTSIDE ansible/roles/: role files are already covered by the role closure, and a
# literal naming no real file (a task name mentioning prometheus.yml, a templated
# `{{ ... }}` path) drops out. Over-inclusive at the margin, like the role scan: a
# stray literal that happens to name a real shared file costs at most one spurious
# rebake, while a missed include is silent drift. Output is the path relative to the
# repo root (e.g. ansible/tasks/os-vars.yml).
#
# Same `|| true` contract as emit_role_refs at the call sites: grep exits non-zero
# on a file with no candidate literals (or only comments), which under `set -e` +
# `pipefail` would abort the capturing assignment. Every stage reads its input to
# EOF (no head / grep -q), so there is no SIGPIPE path (#1274).
emit_shared_refs() {
  local src="$1" src_dir role_tasks="" tok dir cand cdir canon
  src_dir="$(dirname "$src")"
  case "$src" in
    "${ROLES_DIR}"/*)
      role_tasks="${src#"${ROLES_DIR}"/}"
      role_tasks="${ROLES_DIR}/${role_tasks%%/*}/tasks"
      ;;
  esac
  grep -vE '^[[:space:]]*#' "$src" \
    | sed -E 's/[[:space:]]#.*$//' \
    | grep -oE '[A-Za-z0-9._/-]+\.ya?ml' \
    | while IFS= read -r tok; do
        for dir in "$src_dir" "$role_tasks" "$ANSIBLE_DIR"; do
          if [ -z "$dir" ] || [ ! -f "${dir}/${tok}" ]; then continue; fi
          cand="${dir}/${tok}"
          cdir="$(CDPATH="" cd "$(dirname "$cand")" && pwd -P)"
          canon="${cdir}/$(basename "$cand")"
          case "$canon" in
            "${ROLES_ABS}"/*) ;;
            "${ANSIBLE_ABS}"/*) printf '%s\n' "${canon#"${REPO_ABS}"/}" ;;
          esac
        done
      done
}

# Tag every non-empty line of $1 with prefix $2 ("r " / "f "), for the worklist.
# sed reads all its input, so the pipe cannot SIGPIPE the printf.
tag_lines() {
  printf '%s\n' "$1" | sed -e '/^$/d' -e "s|^|$2|"
}

# Transitive closure of bake roles AND the shared files they include, seeded from
# the bake playbook. BFS over one worklist of tagged items: `r <role>` scans every
# YAML file under ansible/roles/<role>; `f <path>` scans one shared file under
# ansible/ (path relative to the repo root). Either scan can enqueue further roles
# (nested include_role, meta dependencies) and further shared files (#1324), so a
# shared include that itself includes or include_role's is followed too. `seen` (a
# newline-delimited string, for bash-3.2 portability - no associative arrays)
# de-dupes and guarantees termination on cyclic references.
REPO_ABS="$(CDPATH="" cd ../.. && pwd -P)"
ANSIBLE_DIR="../../ansible"
ANSIBLE_ABS="${REPO_ABS}/ansible"
ROLES_ABS="${ANSIBLE_ABS}/roles"
seen=""
resolved=""
shared=""
refs="$(emit_role_refs < "$BAKE")" || true
worklist="$(tag_lines "$refs" "r ")"
refs="$(emit_shared_refs "$BAKE")" || true
worklist="$(printf '%s\n%s' "$worklist" "$(tag_lines "$refs" "f ")")"
while [ -n "$worklist" ]; do
  # Pop the first line with parameter expansion, not `printf | head -n1`: under
  # pipefail, head exiting early can SIGPIPE a printf still writing a long worklist
  # (exit 141), which set -e turned into an intermittent hash failure (#1274).
  item="${worklist%%$'\n'*}"
  case "$worklist" in
    *$'\n'*) worklist="${worklist#*$'\n'}" ;;
    *) worklist="" ;;
  esac
  if [ -z "$item" ]; then continue; fi
  # Membership by pattern match, not `printf | grep -q` (grep -q exits on the first
  # match and can SIGPIPE the printf, misreporting a seen item as unseen).
  case "$seen"$'\n' in
    *$'\n'"$item"$'\n'*) continue ;;
  esac
  seen="$(printf '%s\n%s' "$seen" "$item")"
  case "$item" in
    "r "*)
      role="${item#r }"
      resolved="$(printf '%s\n%s' "$resolved" "$role")"
      files="$(find "${ROLES_DIR}/${role}" -type f \( -name '*.yml' -o -name '*.yaml' \) | sort)"
      ;;
    *)
      # Only `r ` and `f ` items are ever enqueued (tag_lines above).
      shared="$(printf '%s\n%s' "$shared" "${item#f }")"
      files="../../${item#f }"
      ;;
  esac
  # Scan each file of the item for role and shared-file references. Iterate the
  # newline list by parameter expansion (same as the worklist pop) so the loop runs
  # in this shell and its worklist appends survive - a `printf | while` would run in
  # a subshell and drop them.
  while [ -n "$files" ]; do
    file="${files%%$'\n'*}"
    case "$files" in
      *$'\n'*) files="${files#*$'\n'}" ;;
      *) files="" ;;
    esac
    refs="$(emit_role_refs < "$file")" || true
    worklist="$(printf '%s\n%s' "$worklist" "$(tag_lines "$refs" "r ")")"
    refs="$(emit_shared_refs "$file")" || true
    worklist="$(printf '%s\n%s' "$worklist" "$(tag_lines "$refs" "f ")")"
  done
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
  # Every shared file outside ansible/roles/ the closure includes (#1324), path then
  # content, path-sorted. Without this an edit to ansible/tasks/os-vars.yml alone
  # left every box's hash unchanged and the builder REUSEd a stale box.
  printf '%s\n' "$shared" | sort -u | while IFS= read -r file; do
    [ -z "$file" ] && continue
    printf 'shared=%s\n' "$file"
    cat "../../${file}"
  done
} | sha256sum | cut -d' ' -f1
