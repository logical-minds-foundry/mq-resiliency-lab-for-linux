#!/usr/bin/env bash
# lab/boxes/_manifest-hash.sh <box> - the bake-manifest hash for a fat box.
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
# The <box> argument is the full box name (mq-rdqm-rhel9, infra-ubuntu2404, ...),
# but the bake playbook is named by the shorter STEM (bake-mq-rdqm.yml,
# bake-infra.yml, ...). That box->stem map is the same one build-fatbox.sh uses;
# it MUST be applied here too. Pre-#649 this script built the path straight from
# the box name (ansible/bake-<box>.yml), which matched NO existing file for any of
# the four boxes - so `[ -f "$BAKE" ]` never fired and the digest silently omitted
# the bake playbook (and every role) entirely. #649 fixes the mapping and adds the
# transitive role closure, so the digest now actually covers the bake work.
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

BOX="${1:?usage: _manifest-hash.sh <box>}"
cd "$(dirname "$0")"

# Map the full box name to its bake-playbook STEM (must match build-fatbox.sh).
case "$BOX" in
  mq-rdqm-rhel9)    BAKE_STEM=mq-rdqm ;;
  obs-ubuntu2404)   BAKE_STEM=obs ;;
  logsearch-ubuntu2404) BAKE_STEM=logsearch ;;
  infra-ubuntu2404) BAKE_STEM=infra ;;
  mq-ubuntu2404)    BAKE_STEM=mq-ubuntu ;;
  mq-nativeha-rhel9) BAKE_STEM=nativeha-rhel ;;
  mq-nativeha-ubuntu) BAKE_STEM=nativeha-ubuntu ;;
  pcmk-ubuntu)      BAKE_STEM=pcmk-ubuntu ;;
  *) echo "ERROR: unknown box: '${BOX}'" >&2; exit 2 ;;
esac

PINS="../../ansible/group_vars/all/versions.yml"
BAKE_REL="ansible/bake-${BAKE_STEM}.yml"
BAKE="../../${BAKE_REL}"
ROLES_DIR="../../ansible/roles"

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
  role="$(printf '%s\n' "$worklist" | head -n1)"
  worklist="$(printf '%s\n' "$worklist" | tail -n +2)"
  if [ -z "$role" ]; then continue; fi
  if printf '%s\n' "$seen" | grep -qxF "$role"; then continue; fi
  seen="$(printf '%s\n%s' "$seen" "$role")"
  resolved="$(printf '%s\n%s' "$resolved" "$role")"
  refs="$(find "${ROLES_DIR}/${role}" -type f \( -name '*.yml' -o -name '*.yaml' \) \
            -exec cat {} + 2>/dev/null | emit_role_refs)" || true
  if [ -n "$refs" ]; then worklist="$(printf '%s\n%s' "$worklist" "$refs")"; fi
done

{
  printf 'box=%s\n' "$BOX"
  printf 'bake=%s\n' "$BAKE_REL"
  if [ -f "$PINS" ]; then cat "$PINS"; fi
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
