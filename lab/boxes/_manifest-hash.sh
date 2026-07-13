#!/usr/bin/env bash
# lab/boxes/_manifest-hash.sh <box> - the bake-manifest hash for a fat box.
#
# A fat box is only worth REUSEing from cache while the inputs that shaped it are
# unchanged. This digests those inputs into a stable sha256 the builder stores
# beside the cached .box and re-checks on every run (#603, epic .github#70):
#
#   * the package/version pin set (ansible/group_vars/all/versions.yml) - the
#     mq/grafana/etc. versions baked in;
#   * the box's bake playbook PATH and CONTENT (ansible/bake-<box>.yml, produced
#     by #602) - the recipe itself. Absent files still contribute their path, so
#     the hash flips the moment #602 lands the playbook.
#
# Inputs are read RELATIVE TO THIS SCRIPT (the worktree), not the host-durable
# main-worktree cache: they are code, so a feature branch that edits a pin or a
# bake recipe gets a distinct hash and forces a rebuild.
set -euo pipefail

BOX="${1:?usage: _manifest-hash.sh <box>}"
cd "$(dirname "$0")"

PINS="../../ansible/group_vars/all/versions.yml"
BAKE_REL="ansible/bake-${BOX}.yml"
BAKE="../../${BAKE_REL}"

{
  printf 'box=%s\n' "$BOX"
  printf 'bake=%s\n' "$BAKE_REL"
  if [ -f "$PINS" ]; then cat "$PINS"; fi
  if [ -f "$BAKE" ]; then cat "$BAKE"; fi
} | sha256sum | cut -d' ' -f1
