#!/usr/bin/env bash
# obs-kit.sh — the black-box recording kit for the RDQM observation drills (#1666).
#
# Runs on the lab host (the Vergil VM) against the six rdqm-rhel nodes over SSH. It only
# OBSERVES: it reads Pacemaker, DRBD, journal and log state and writes it to the host. It
# never runs a disassembler, tracer or `strings` on an IBM binary, never copies one, and
# never reads keystores, the corosync authkey or DRBD TLS keys. The one change it can make
# on a node is `prep`, which creates /var/log/journal so the journal survives
# `virsh destroy` (the issue's recording-kit step 1), and says so when it does.
#
#   obs-kit.sh prep    <scen>              persistent-journal check (+ create), per node
#   obs-kit.sh clocks  <scen> <label>      chronyc tracking + timedatectl, per node and host
#   obs-kit.sh rec     <scen> [node ...]   (re)start the drbdsetup events2 recorder
#   obs-kit.sh snap    <scen> <label>      step-boundary snapshot of every reachable node
#   obs-kit.sh poll    <scen> start|stop   5 s poller of every node (background); each poll
#                                          also runs `journalctl --sync; sync` (from S1 on), so a
#                                          `virsh destroy` loses at most ~5 s of journal/events2
#   obs-kit.sh mark    <scen> <text>       an operator-action line in <scen>/actions.log
#   obs-kit.sh collect <scen> <T0> <T1>    journal, pacemaker.log, changed text logs, events2
#   obs-kit.sh x       <scen> <name> <sh>  run a read-only <sh> on every node -> <node>/<name>.txt
#
# Output: $OUT_ROOT/<scen>/<node>/..., OUT_ROOT defaulting to
# $(mqlab build path temp)/rdqm-observe. Times are UTC throughout.
set -euo pipefail
shopt -s inherit_errexit

KEY="${HOME}/.vagrant.d/insecure_private_key"
NODES=(rdqm-a1 rdqm-a2 rdqm-a3 rdqm-b1 rdqm-b2 rdqm-b3)
ip() {
  case "$1" in
    rdqm-a1) echo 10.50.0.31 ;; rdqm-a2) echo 10.50.0.32 ;; rdqm-a3) echo 10.50.0.33 ;;
    rdqm-b1) echo 10.50.0.41 ;; rdqm-b2) echo 10.50.0.42 ;; rdqm-b3) echo 10.50.0.43 ;;
    *) echo "obs-kit: unknown node $1" >&2; return 1 ;;
  esac
}
OUT_ROOT="${OUT_ROOT:?set OUT_ROOT (e.g. \$(mqlab build path temp)/rdqm-observe)}"
SSHO=(-i "$KEY" -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR
      -o ConnectTimeout=4 -o BatchMode=yes -o ServerAliveInterval=2 -o ServerAliveCountMax=2)
# The DRBD shared-secret redaction of ansible/capture-rdqm-blueprint.yml (#1603).
SECRET_SED='s/(shared-secret[[:space:]]+)("[^"]*"|[^;[:space:]]+)/\1"REDACTED"/'

die() { echo "obs-kit: $*" >&2; exit 1; }
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
# on <node> <script> — run <script> as root on <node>; a hard 60 s cap so a node that dies
# mid-call cannot hang the kit.
on() { local n="$1"; shift; timeout 60 ssh "${SSHO[@]}" "vagrant@$(ip "$n")" "sudo bash -c $(printf %q "$*")"; }
up() { timeout 8 ssh "${SSHO[@]}" "vagrant@$(ip "$1")" true 2>/dev/null; }

[ "$#" -ge 2 ] || die "usage: obs-kit.sh <verb> <scen> ..."
VERB="$1"; SCEN="$2"; shift 2
OUT="$OUT_ROOT/$SCEN"
mkdir -p "$OUT"

prep() {
  local n
  for n in "${NODES[@]}"; do
    on "$n" 'if [ -d /var/log/journal ]; then echo "present: $(ls -ld /var/log/journal)";
      else mkdir -p /var/log/journal && systemd-tmpfiles --create --prefix /var/log/journal &&
      systemctl restart systemd-journald && echo "CREATED: $(ls -ld /var/log/journal)"; fi;
      grep -hE "^[[:space:]]*(Storage|SyncIntervalSec)=" /etc/systemd/journald.conf \
        /etc/systemd/journald.conf.d/*.conf 2>/dev/null || echo "journald.conf: no Storage/SyncIntervalSec override"' \
      | sed "s/^/$(now) $n /" | tee -a "$OUT/journal-prep.log"
  done
}

clocks() {
  local label="$1" n
  { echo "== $(now) host $(hostname)"; chronyc tracking 2>&1 || timedatectl 2>&1; } >>"$OUT/clocks-$label.txt"
  for n in "${NODES[@]}"; do
    { echo "== $(now) $n"; on "$n" 'chronyc tracking; timedatectl' 2>&1 || echo "UNREACHABLE"; } >>"$OUT/clocks-$label.txt"
  done
}

rec() {
  local n nodes=("$@")
  [ "${#nodes[@]}" -gt 0 ] || nodes=("${NODES[@]}")
  for n in "${nodes[@]}"; do
    # Append, so a restart after `virsh destroy` continues the same file; each start is
    # marked with the UTC time and the boot id.
    on "$n" "pkill -f '^drbdsetup events2' || true
      echo \"# events2 recorder start \$(date -u +%Y-%m-%dT%H:%M:%S.%NZ) boot \$(cat /proc/sys/kernel/random/boot_id)\" >>/root/obs-events2-$SCEN.log
      nohup setsid drbdsetup events2 --timestamps --statistics all >>/root/obs-events2-$SCEN.log 2>&1 </dev/null &
      sleep 1; pgrep -af '^drbdsetup events2'" | sed "s/^/$(now) $n rec /" | tee -a "$OUT/actions.log"
  done
}

snap_node() {  # <node> <dir>
  local n="$1" d="$2"
  mkdir -p "$d"
  if ! up "$n"; then echo "UNREACHABLE at $(now)" >"$d/UNREACHABLE"; return 0; fi
  on "$n" 'cibadmin -Q 2>&1' >"$d/cib.xml" || true
  on "$n" 'crm_mon -1 -rA --include=all 2>&1' >"$d/crm_mon.txt" || true
  on "$n" 'drbdsetup status --verbose --statistics all 2>&1' >"$d/drbd-status.txt" || true
  on "$n" 'drbdsetup show --show-defaults all 2>&1' | sed -E "$SECRET_SED" >"$d/drbd-show.txt" || true
  on "$n" '/opt/mqm/bin/rdqmstatus 2>&1; echo "[rc=$?]"' >"$d/rdqmstatus.txt" || true
  on "$n" '/opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1; echo "[rc=$?]"' >"$d/rdqmstatus-qm.txt" || true
  on "$n" 'systemctl status rdqm.service pacemaker corosync --no-pager 2>&1' >"$d/systemctl.txt" || true
  on "$n" 'ls -la --time-style=full-iso /var/mqm/rdqm/ 2>&1' >"$d/ls-var-mqm-rdqm.txt" || true
  now >"$d/TAKEN"
}

snap() {
  local label="$1" n seq
  seq=0
  [ ! -f "$OUT/snaps.log" ] || seq=$(wc -l <"$OUT/snaps.log")
  seq=$(printf '%02d' "$seq")
  echo "$(now) $seq-$label begin" >>"$OUT/snaps.log"
  for n in "${NODES[@]}"; do snap_node "$n" "$OUT/$n/snap/$seq-$label" & done
  wait
  echo "snap $seq-$label done at $(now)"
}

poll_loop() {
  local n ts
  while :; do
    ts=$(now)
    for n in "${NODES[@]}"; do
      ( mkdir -p "$OUT/$n"
        out=$(timeout 8 ssh "${SSHO[@]}" "vagrant@$(ip "$n")" "sudo bash -c '
          /opt/mqm/bin/rdqmstatus -m RDQMAPP 2>&1; echo \"--- crm_mon\"; crm_mon -1 -rA 2>&1;
          echo \"--- drbdsetup\"; drbdsetup status --verbose 2>&1;
          journalctl --sync >/dev/null 2>&1; sync'" 2>&1) || out="POLL-FAILED rc=$? ${out:-}"
        printf '%s\n' "$out" | sed "s/^/$ts /" >>"$OUT/$n/poll.log" ) &
    done
    wait
    sleep 5
  done
}

poll() {
  case "$1" in
    start) [ ! -f "$OUT/poll.pid" ] || die "poller already running (pid $(cat "$OUT/poll.pid"))"
           poll_loop >/dev/null 2>&1 & echo $! >"$OUT/poll.pid"
           echo "$(now) poll start pid $(cat "$OUT/poll.pid")" | tee -a "$OUT/actions.log" ;;
    stop)  [ -f "$OUT/poll.pid" ] || die "no poller"
           kill "$(cat "$OUT/poll.pid")"; rm -f "$OUT/poll.pid"
           echo "$(now) poll stop" | tee -a "$OUT/actions.log" ;;
    *) die "poll start|stop" ;;
  esac
}

collect_node() {  # <node> <T0> <T1>
  local n="$1" t0="$2" t1="$3" d="$OUT/$1"
  mkdir -p "$d/files"
  if ! up "$n"; then echo "UNREACHABLE at collect $(now)" >"$d/COLLECT-UNREACHABLE"; return 0; fi
  on "$n" "journalctl --list-boots --no-pager 2>&1" >"$d/journal-boots.txt" || true
  on "$n" "TZ=UTC journalctl -o short-iso-precise --no-pager --since '$t0 UTC' --until '$t1 UTC' 2>&1" >"$d/journal.txt" || true
  on "$n" "TZ=UTC journalctl -b -1 -o short-iso-precise --no-pager --since '$t0 UTC' --until '$t1 UTC' 2>&1" >"$d/journal-boot-1.txt" || true
  on "$n" "cat /root/obs-events2-$SCEN.log 2>&1" >"$d/events2.log" || true
  on "$n" "TZ=UTC date; date; cat /etc/localtime >/dev/null; timedatectl show -p Timezone" >"$d/tz.txt" || true
  # Every file changed in the window under /var/mqm and /var/log, as a listing (data).
  on "$n" "find /var/mqm /var/log -type f -newermt '$t0 UTC' -printf '%TY-%Tm-%TdT%TH:%TM:%TS %s %p\n' 2>/dev/null | sort" >"$d/changed-files.txt" || true
  # Copy the changed TEXT logs only. Never keystores, keys, certs, the corosync authkey,
  # the mqweb user registry, the journal (binary) or queue-manager data.
  on "$n" "cd / && find var/mqm var/log -type f -newermt '$t0 UTC' -size -20M \
      ! -path 'var/log/journal/*' ! -path '*/ssl/*' ! -path '*/web/*' ! -path '*/queues/*' \
      ! -name '*.kdb' ! -name '*.sth' ! -name '*.p12' ! -name '*.pem' ! -name '*key*' \
      ! -name '*.crt' ! -name '*.jks' ! -name '*.rdb' ! -name '*.crl' ! -name 'authkey' \
      ! -name 'mqwebuser.xml' ! -name '*.FDC' ! -name 'wtmp' ! -name 'btmp' ! -name 'lastlog' \
      -print0 2>/dev/null | while IFS= read -r -d '' f; do grep -Iq . \"\$f\" && printf '%s\0' \"\$f\"; done \
      | tar --null -T - -czf - 2>/dev/null" | tar -xzf - -C "$d/files" || true
  # FFST (FDC) files: header block only — the probe, component and time — never the dump.
  on "$n" "find /var/mqm/errors -name '*.FDC' -newermt '$t0 UTC' 2>/dev/null | while read -r f; do
      echo \"=== \$f\"; sed -n '1,30p' \"\$f\"; done" >"$d/fdc-headers.txt" || true
  echo "$(now) collected $n" >>"$OUT/actions.log"
}

case "$VERB" in
  prep) prep ;;
  clocks) clocks "${1:?label}" ;;
  rec) rec "$@" ;;
  snap) snap "${1:?label}" ;;
  poll) poll "${1:?start|stop}" ;;
  x)
    name="${1:?name}"; script="${2:?script}"
    for n in "${NODES[@]}"; do
      mkdir -p "$OUT/$n"
      { on "$n" "$script" 2>&1 || echo "[rc=$?]"; } >"$OUT/$n/$name.txt" &
    done
    wait ;;
  mark) echo "$(now) ACTION $*" | tee -a "$OUT/actions.log" ;;
  collect)
    t0="${1:?T0}"; t1="${2:?T1}"
    for n in "${NODES[@]}"; do collect_node "$n" "$t0" "$t1" & done
    wait ;;
  *) die "unknown verb $VERB" ;;
esac
