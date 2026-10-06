# shellcheck shell=bash
# lab/boxes/_components.sh - SOURCED (not executed) by build-fatbox.sh (epic .github#294 spec §5.8).
#
# The guest components a fat box bakes arrive as ONE flag from mqlab
# (--components "<name>@<tree>[,<name>@<tree>...]", "" for none). These helpers parse
# it and render the two JSON shapes the builder needs. Names are [a-z0-9._-] and trees
# are hex (components_check enforces both), so the printf'd JSON needs no escaping.

# components_check <value>: return 2 (message on stderr) unless every entry is
# <name>@<hex tree>. An empty value is valid (a box that bakes no component).
components_check() {
  local item items=()
  if [ -n "$1" ]; then IFS=, read -r -a items <<< "$1"; fi
  for item in ${items[@]+"${items[@]}"}; do
    if ! [[ "$item" =~ ^[a-z0-9][a-z0-9._-]*@[0-9a-f]+$ ]]; then
      echo "ERROR: --components entries are <name>@<tree hash> (got '${item}')" >&2
      return 2
    fi
  done
}

# components_names_json <value>: the baked component names as a JSON list, e.g. ["a","b"].
components_names_json() {
  local item items=() out="" sep=""
  if [ -n "$1" ]; then IFS=, read -r -a items <<< "$1"; fi
  for item in ${items[@]+"${items[@]}"}; do
    out="${out}${sep}\"${item%%@*}\""
    sep=","
  done
  printf '[%s]\n' "$out"
}

# components_record <runtime-pin> <value>: what a box baked, recorded beside its cached
# .box as <box>-<arch>.components.json and shown by `mqlab box status`:
#   {"runtime": "<pin>", "components": {"<name>": "<tree>", ...}}
components_record() {
  local item items=() out="" sep=""
  if [ -n "$2" ]; then IFS=, read -r -a items <<< "$2"; fi
  for item in ${items[@]+"${items[@]}"}; do
    out="${out}${sep}\"${item%%@*}\": \"${item#*@}\""
    sep=", "
  done
  printf '{"runtime": "%s", "components": {%s}}\n' "$1" "$out"
}
