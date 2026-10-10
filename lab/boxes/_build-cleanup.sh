# lab/boxes/_build-cleanup.sh - SOURCED (not executed) by the box builders (#1404).
#
# Both builders (build-fatbox.sh, rhel/build-box.sh) run under `set -euo pipefail` and
# tear their transient build domain down only on the SUCCESS path. A bake that failed
# after the domain started used to exit with the guest still RUNNING, its build disk and
# console log left in the libvirt pool, and its scratch files on disk until the next run
# of that same box cleared them. That cost host RAM shared with the lab and broke the
# "no build domain left behind" check after every failed bake.
#
# Contract for a builder:
#   . ./_build-cleanup.sh                   # (or ../_build-cleanup.sh)
#   BUILD_DOM=...; CONSOLE=...; BUILD_EVIDENCE_DIR=...   # before arming
#   trap build_cleanup_on_exit EXIT
#   build_cleanup_add <path>...             # each scratch file/dir as it is created
#
# On a NON-ZERO exit the handler:
#   1. keeps the evidence: copies $CONSOLE into $BUILD_EVIDENCE_DIR (the serial log is
#      what diagnoses the failed bake), and says where;
#   2. destroys + undefines $BUILD_DOM (--nvram) when it is defined;
#   3. removes every registered path;
#   4. exits with the ORIGINAL code.
# A step that fails prints a WARNING naming it (never swallowed), and the handler carries
# on with the rest. On exit 0 it does nothing, so the success path is unchanged. The box
# cache and the staged DVD are never registered, so they are always kept.

build_cleanup_paths=()

build_cleanup_add() {
  build_cleanup_paths+=("$@")
}

_build_cleanup_warn() {
  echo "WARNING: bake-failure cleanup: $*" >&2
}

build_cleanup_on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] && return 0
  set +e  # cleanup must run every step; each failure is reported below, not fatal
  local dom="${BUILD_DOM:-}"
  echo "bake failed (exit $rc): cleaning up${dom:+ transient build domain $dom} (#1404)" >&2

  local console="${CONSOLE:-}" evidence="${BUILD_EVIDENCE_DIR:-}"
  if [ -n "$console" ] && sudo test -f "$console"; then
    if [ -n "$evidence" ]; then
      local kept
      kept="$evidence/$(basename "$console" .log)-$(date -u +%Y%m%dT%H%M%SZ).log"
      if mkdir -p "$evidence" && sudo cp "$console" "$kept" && sudo chown "$(id -u)" "$kept"; then
        echo "kept the build console log: $kept" >&2
      else
        _build_cleanup_warn "could not keep the console log $console in $evidence"
      fi
    else
      _build_cleanup_warn "BUILD_EVIDENCE_DIR is unset; console log $console not kept"
    fi
  fi

  if [ -n "$dom" ] && virsh -c qemu:///system dominfo "$dom" >/dev/null 2>&1; then
    if [ "$(virsh -c qemu:///system domstate "$dom" 2>/dev/null)" != "shut off" ]; then
      virsh -c qemu:///system destroy "$dom" >/dev/null ||
        _build_cleanup_warn "virsh destroy $dom failed"
    fi
    virsh -c qemu:///system undefine "$dom" --nvram >/dev/null ||
      _build_cleanup_warn "virsh undefine $dom failed"
  fi

  local p
  for p in ${console:+"$console"} "${build_cleanup_paths[@]}"; do
    sudo rm -rf -- "$p" || _build_cleanup_warn "could not remove $p"
  done
  exit "$rc"
}
