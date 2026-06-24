#!/usr/bin/env bash
# lab/boxes/rhel96/await-install.sh — wait for the transient RHEL build domain to
# finish its unattended kickstart install (the ks ends with `poweroff`, so the domain
# reaches `shut off`), emitting a heartbeat — elapsed time + the latest console line —
# each poll instead of a silent sleep loop. The build domain logs its serial console
# to a file (build-domain.xml.tpl), and ks.cfg puts Anaconda on console=ttyS0, so the
# latest line is Anaconda's current step.
#
# No timeout: the install takes as long as it takes (minutes under KVM, 45–90 min under
# TCG); the heartbeat is the signal an operator uses to decide a box is wedged and abort
# by hand — matching the original wait's wait-forever semantics. (#331)
#
# Usage: await-install.sh <domain> <console-log> [poll_secs]
set -euo pipefail

DOMAIN="${1:?usage: await-install.sh <domain> <console-log> [poll_secs]}"
CONSOLE_LOG="${2:?usage: await-install.sh <domain> <console-log> [poll_secs]}"
POLL="${3:-30}"

start="$SECONDS"
while [ "$(virsh -c qemu:///system domstate "$DOMAIN" 2>/dev/null || true)" != "shut off" ]; do
  elapsed=$(( SECONDS - start ))
  # Latest console line = Anaconda's current step. Best-effort: a not-yet-written or
  # unreadable log degrades to a placeholder rather than aborting the wait. Strip CRs
  # and non-printables so a half-written serial line never garbles the heartbeat.
  last="$(tail -n1 "$CONSOLE_LOG" 2>/dev/null | tr -d '\r' | tr -cd '[:print:]' || true)"
  printf '[install] %dm%02ds — %s\n' \
    "$(( elapsed / 60 ))" "$(( elapsed % 60 ))" "${last:-(no console output yet)}"
  sleep "$POLL"
done
elapsed=$(( SECONDS - start ))
printf '[install] done in %dm%02ds (domain shut off)\n' "$(( elapsed / 60 ))" "$(( elapsed % 60 ))"
