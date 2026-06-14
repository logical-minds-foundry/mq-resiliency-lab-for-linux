#!/usr/bin/env bash
# lab/scripts/drbd-degrade.sh — degrade or break cross-site DRBD replication,
# for the forced-DR drills. Lets a measurable amount of data be committed at the
# primary that has NOT reached the secondary, so a subsequent forced promote of
# the secondary loses exactly that tail (the RPO>0 the framework quantifies).
#
#   break    : disconnect the DRBD link entirely (no replication at all)
#   throttle : cap the replication/resync rate to a crawl (a controllable lag)
#   connect  : restore — reconnect and clear the cap
#
# Usage: drbd-degrade.sh <break|throttle|connect> [NODE=san-a] [RES=mqlun] [RATE=250k]
set -euo pipefail
MODE="${1:?usage: drbd-degrade.sh <break|throttle|connect> [NODE] [RES] [RATE]}"
NODE="${2:-san-a}"
RES="${3:-mqlun}"
RATE="${4:-250k}"
cd "$(dirname "$0")/../../ansible"
run() { ansible "$1" -b -m shell -a "$2"; }

case "$MODE" in
  break)
    run "$NODE" "drbdadm disconnect $RES && drbdadm status $RES" ;;
  throttle)
    run "$NODE" "drbdadm disk-options --c-max-rate=$RATE $RES && drbdadm status $RES" ;;
  connect)
    run "$NODE" "drbdadm connect $RES; drbdadm disk-options --c-max-rate=0 $RES 2>/dev/null; drbdadm status $RES" ;;
  *)
    echo "unknown mode: $MODE (want break|throttle|connect)" >&2; exit 1 ;;
esac
