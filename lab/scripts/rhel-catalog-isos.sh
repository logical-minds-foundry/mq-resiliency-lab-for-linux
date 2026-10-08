#!/usr/bin/env bash
# lab/scripts/rhel-catalog-isos.sh [<catalog>] - print every RHEL DVD ISO filename the
# OS catalog names (each os.rhel.<major>.iso in lab/versions.yaml), one per line, in
# catalog order (#1395). It backs the --catalog mode of scripts/push-rhel-iso.sh (macOS
# host) and lab/scripts/stage-rhel-iso.sh (lab VM).
#
# Dependency-free on purpose: push-rhel-iso.sh runs on the macOS host, which has only
# bash 3.2 and BSD awk, with no mqlab, no PyYAML and possibly no python3. So this is a
# deliberately NARROW reader, not a YAML parser. It accepts exactly the shape the
# catalog uses today, a block-mapping `os:` whose `rhel:` block holds one ONE-LINE
# flow mapping per major:
#
#   os:
#     rhel:
#       <major>: { ..., iso: <file>.iso, ... }
#
# and fails loudly (exit 1, naming the line) on anything else: a missing os/rhel
# block, a multi-line or block-style entry, an entry with zero or several iso: keys,
# or an iso value that is not a plain *.iso filename. A catalog reformat therefore
# breaks this loudly instead of silently dropping a DVD, and
# tests/test_rhel_catalog_isos.py pins its output against mqlab.versions on the real
# catalog. mqlab itself never uses this; it reads the catalog through mqlab.versions.
set -euo pipefail

if [ "$#" -gt 1 ]; then
  echo "usage: rhel-catalog-isos.sh [<catalog>]   (default: lab/versions.yaml)" >&2
  exit 2
fi
CATALOG="${1:-$(dirname "$0")/../versions.yaml}"
test -f "$CATALOG" || {
  echo "ERROR: OS catalog not found: $CATALOG" >&2
  exit 1
}

# POSIX awk only (macOS ships the one-true-awk): no gawk extensions, no {n} intervals.
awk -v catalog="$CATALOG" '
function die(msg) {
  printf "ERROR: %s:%d: %s\n", catalog, NR, msg > "/dev/stderr"
  failed = 1
  exit 1
}
function indent(s) {
  match(s, /^ */)
  return RLENGTH
}
BEGIN {
  shape = "expected each os.rhel entry as a one-line flow mapping: <major>: { ..., iso: <file>.iso, ... }"
}
/^[[:space:]]*(#.*)?$/ { next }  # blank or comment-only line
{
  ind = indent($0)
  if (ind == 0) {
    # A top-level key: we are inside os: only until the next one.
    in_os = 0
    in_rhel = 0
    if ($0 ~ /^os:/) {
      if ($0 !~ /^os:[[:space:]]*(#.*)?$/) die("os: is not a block mapping")
      if (seen_os) die("duplicate top-level os:")
      seen_os = 1
      in_os = 1
    }
    next
  }
  if (!in_os) next
  if (in_rhel && ind <= rhel_ind) in_rhel = 0  # left os.rhel (a sibling family)
  if (!in_rhel) {
    if ($0 ~ /^ +rhel:/) {
      if ($0 !~ /^ +rhel:[[:space:]]*(#.*)?$/) die("os.rhel is not a block mapping")
      if (seen_rhel) die("duplicate os.rhel")
      seen_rhel = 1
      in_rhel = 1
      rhel_ind = ind
      entry_ind = -1
    }
    next
  }
  # Inside os.rhel: every line is exactly one <major>: { ... } entry.
  if (entry_ind < 0) entry_ind = ind
  if (ind != entry_ind) die(shape)
  if ($0 !~ /^ +[0-9]+:[[:space:]]*\{.*\}[[:space:]]*(#.*)?$/) die(shape)
  rest = $0
  n = 0
  # The iso key must open the mapping or follow a comma, so a nested value never matches.
  while (match(rest, /[{,][[:space:]]*iso:[[:space:]]*/)) {
    rest = substr(rest, RSTART + RLENGTH)
    val = rest
    n++
  }
  if (n == 0) die("os.rhel entry has no iso: key; " shape)
  if (n > 1) die("os.rhel entry has more than one iso: key")
  q = substr(val, 1, 1)
  if (q == "\"" || q == "\047") {
    val = substr(val, 2)
    end = index(val, q)
    if (end == 0) die("unterminated quoted iso: value")
    val = substr(val, 1, end - 1)
  } else {
    match(val, /^[^,}[:space:]]*/)
    val = substr(val, 1, RLENGTH)
  }
  if (val !~ /^[A-Za-z0-9._+-]+\.iso$/) die("iso: value \"" val "\" is not a plain *.iso filename")
  out = out val "\n"  # printed in END, so a failure never emits a partial list
  count++
}
END {
  if (failed) exit 1
  if (!seen_os) { printf "ERROR: %s: no top-level os: block\n", catalog > "/dev/stderr"; exit 1 }
  if (!seen_rhel) { printf "ERROR: %s: no os.rhel block\n", catalog > "/dev/stderr"; exit 1 }
  if (count == 0) { printf "ERROR: %s: os.rhel names no DVD ISO\n", catalog > "/dev/stderr"; exit 1 }
  printf "%s", out
}
' "$CATALOG"
