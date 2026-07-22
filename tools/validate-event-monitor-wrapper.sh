#!/usr/bin/env bash
# Resilient event-monitor wrapper — live validation harness (epic .github#122).
#
# Runs ON the lab node where the amqsevt collector is active. It induces the B-matrix against the
# running MQ.EVENT.MONITOR service and prints PASS/FAIL evidence to stdout, exiting non-zero if any
# scenario fails. It is deliberately self-contained (no ansible/pymqi) so it can be dropped onto a
# node and run, or invoked from the repo via `ansible <active-node> -b -m script -a ...`.
#
# See docs/reference/event-monitor-wrapper-validation.md for how to find the active node per arm,
# how to invoke this, and where the captured evidence belongs.
#
#   B1  normal start + checkpoint : wrapper + amqsevt up; service PID == wrapper PGID; events flowing
#   B2  clean stop                : STOP SERVICE reaps BOTH wrapper and amqsevt — no orphan
#   B3  crash recovery            : kill -9 amqsevt; the wrapper restarts it, retrying through the
#                                   ~29s exclusive-handle reap window (MQRC_OBJECT_IN_USE 2042)
#
# Usage: validate-event-monitor-wrapper.sh <QM> [SERVICE_NAME]
set -u

QM="${1:?usage: validate-event-monitor-wrapper.sh <QM> [SERVICE_NAME]}"
SVC="${2:-MQ.EVENT.MONITOR}"
fails=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

mqsc()        { su - mqm -c "runmqsc ${QM}" 2>/dev/null; }               # MQSC from stdin
wrapper_pid() { pgrep -f "run.sh ${QM}" | head -1; }
sevt_pid()    { pgrep -x amqsevt | head -1; }
pgid_of()     { ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '; }
service_pid() { printf 'DISPLAY SVSTATUS(%s)\n' "${SVC}" | mqsc | grep -oE 'PID\([0-9]+\)' | grep -oE '[0-9]+'; }
# shellcheck disable=SC2009  # want the formatted ps line (pid/ppid/pgid/args) for evidence, not just pids
procs()       { ps -eo pid,ppid,pgid,args | grep -E 'run\.sh|amqsevt' | grep -v grep; }
running()     { pgrep -f "run.sh ${QM}" >/dev/null 2>&1 || pgrep -x amqsevt >/dev/null 2>&1; }

# Echo an amqsevt pid that is stable across 3s (past any startup 2042-retry dance), else nothing.
wait_stable_sevt() {
  local w=0 p
  while [ "${w}" -lt 20 ]; do
    p=$(sevt_pid)
    if [ -n "${p}" ]; then
      sleep 3
      [ "${p}" = "$(sevt_pid)" ] && { printf '%s' "${p}"; return 0; }
    fi
    sleep 2
    w=$((w + 1))
  done
  return 0
}

printf '== event-monitor wrapper validation :: QM=%s SERVICE=%s :: %s ==\n' \
  "${QM}" "${SVC}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---- B1: normal start + checkpoint ----
printf '\n-- B1: wrapper + amqsevt up; service PID == wrapper PGID; events flowing --\n'
sevt=$(wait_stable_sevt)
procs || true
wpid=$(wrapper_pid)
spid=$(service_pid)
wpgid=$(pgid_of "${wpid:-0}")
printf 'wrapper_pid=%s  service_PID=%s  amqsevt=%s  wrapper_PGID=%s\n' \
  "${wpid:-none}" "${spid:-none}" "${sevt:-none}" "${wpgid:-none}"
if [ -n "${wpid}" ] && [ -n "${sevt}" ] && [ "${wpid}" = "${wpgid}" ] && [ "${spid}" = "${wpid}" ]; then
  pass "B1 up + checkpoint (service PID == wrapper PID == PGID = ${wpid})"
else
  fail "B1 start/checkpoint (wrapper=${wpid} pgid=${wpgid} servicePID=${spid} amqsevt=${sevt})"
fi
events=$(journalctl -t mq-events --no-pager -o cat 2>/dev/null | grep -c '"eventSource"')
if [ "${events:-0}" -gt 0 ]; then pass "B1 events flowing (${events} JSON events in journald)"; else fail "B1 no events in journald"; fi

# ---- B2: clean stop reaps both ----
printf '\n-- B2: STOP SERVICE reaps BOTH wrapper + amqsevt (no orphan) --\n'
printf 'STOP SERVICE(%s)\n' "${SVC}" | mqsc | grep -iE 'AMQ8732|AMQ8147' || true
sleep 5
if running; then
  fail "B2 orphan present after STOP"
  procs
else
  pass "B2 clean stop — wrapper + amqsevt both reaped, no orphan"
fi

# ---- B3: crash recovery through the 2042 window ----
printf '\n-- B3: kill -9 amqsevt; wrapper restarts it (retrying through the ~29s 2042 window) --\n'
printf 'START SERVICE(%s)\n' "${SVC}" | mqsc | grep -iE 'AMQ8733' || true
old=$(wait_stable_sevt)
if [ -z "${old}" ]; then
  fail "B3 no stable amqsevt came up to crash-test"
else
  printf 'amqsevt before crash: %s\n' "${old}"
  kill -9 "${old}"
  i=0
  new=""
  while [ "${i}" -lt 15 ]; do
    sleep 5
    new=$(sevt_pid)
    [ -n "${new}" ] && [ "${new}" != "${old}" ] && break
    i=$((i + 1))
  done
  if [ -n "${new}" ] && [ "${new}" != "${old}" ]; then
    pass "B3 crash recovery (wrapper restarted amqsevt: ${old} -> ${new})"
  else
    fail "B3 amqsevt did not come back"
  fi
fi
printf 'run.sh lifecycle in journald (fail-retry-succeed, incl. MQRC_OBJECT_IN_USE 2042):\n'
journalctl -t mq-events --no-pager -o cat 2>/dev/null | grep 'run.sh\[' | tail -8

printf '\n== summary: '
if [ "${fails}" -eq 0 ]; then printf 'ALL SCENARIOS PASS ==\n'; else printf '%s SCENARIO(S) FAILED ==\n' "${fails}"; fi
procs || true
exit "${fails}"
