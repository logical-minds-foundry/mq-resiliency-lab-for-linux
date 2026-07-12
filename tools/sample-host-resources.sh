#!/usr/bin/env bash
# tools/sample-host-resources.sh — sample this Cloud VM's CPU / I-O / memory / load during a
# bootstrap, for the timing audit (#594). ansible+mqlab now emit per-task timings (profile_tasks);
# this adds the *host resource timeline* so each slow phase can be classified CPU- vs I/O- vs
# memory-bound — which decides whether the fix is a bigger instance, code changes, or both.
#
# Usage — launch it in the BACKGROUND right before the rebuild, stop it (Ctrl-C / kill) after:
#     tools/sample-host-resources.sh [interval_sec] [outfile] &   # default 5s, outfile under build/temp/
#     mqlab bootstrap rdqm-rhel
#     kill %1
#
# Columns:
#   ts       wall-clock time
#   load1    1-min load average        (> vCPU count sustained = CPU-oversubscribed)
#   runq     runnable procs (vmstat r) (> vCPU count = CPU-bound)
#   blk      procs blocked on I/O (b)  (high = I/O-bound)
#   us/sy/id/wa   CPU % user/system/idle/iowait
#             -> id low + wa low  = CPU-bound ;  wa high + blk high = I/O-bound
#   memfreeMB / memavailMB   memory headroom (watch for it approaching 0)
#   bi / bo   disk blocks in / out per second (I/O throughput)
set -uo pipefail

INTERVAL="${1:-5}"
default_out_dir() { command -v mqlab >/dev/null 2>&1 && mqlab build path temp 2>/dev/null || echo /tmp; }
OUT="${2:-$(default_out_dir)/host-resources-$(date +%Y%m%d-%H%M%S).log}"
mkdir -p "$(dirname "$OUT")"

trap 'echo "# sampling stopped $(date +%H:%M:%S) -> $OUT" >&2; exit 0' INT TERM

VCPU=$(nproc)
RAMMB=$(free -m | awk '/Mem:/{print $2}')
{
  echo "# host-resource sample every ${INTERVAL}s  (host: ${VCPU} vCPU, ${RAMMB} MB RAM)  started $(date '+%Y-%m-%d %H:%M:%S')"
  printf '%-8s %6s %4s %4s %3s %3s %3s %3s %10s %11s %8s %8s\n' \
    ts load1 runq blk us sy id wa memfreeMB memavailMB bi bo
} | tee -a "$OUT"

while true; do
  read -r l1 _ < /proc/loadavg
  # `vmstat INTERVAL 2` blocks for INTERVAL seconds; its 2nd line is the average over that window,
  # so the interval is the sampling window and no extra sleep is needed.
  vm=$(vmstat "$INTERVAL" 2 | tail -1)
  # vmstat columns: 1=r 2=b ... 9=bi 10=bo ... 13=us 14=sy 15=id 16=wa
  set -- $vm
  runq="${1:-?}"; blk="${2:-?}"; bi="${9:-?}"; bo="${10:-?}"
  us="${13:-?}"; sy="${14:-?}"; id="${15:-?}"; wa="${16:-?}"
  mf=$(awk '/^MemFree:/{print int($2/1024)}' /proc/meminfo)
  ma=$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo)
  printf '%-8s %6s %4s %4s %3s %3s %3s %3s %10s %11s %8s %8s\n' \
    "$(date +%H:%M:%S)" "$l1" "$runq" "$blk" "$us" "$sy" "$id" "$wa" "$mf" "$ma" "$bi" "$bo" \
    | tee -a "$OUT"
done
