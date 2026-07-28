# Running `amqsevt` as a resilient, self-healing queue-manager service

IBM MQ 9.4 for Multiplatforms (Linux). Runs the `amqsevt` event monitor as a
**managed queue-manager `SERVICE`** that starts and stops with the queue manager,
travels with it across failover, and **restarts itself if it dies** — riding out
the `MQRC_OBJECT_IN_USE` (2042) window that a naive restart would trip over.
**The Quick start is the whole setup; the rest explains why each piece is there.**

This is the *how to run it reliably* companion to
[`2026-07-28-mq-event-monitoring-to-file.md`](2026-07-28-mq-event-monitoring-to-file.md),
which covers enabling the events and the shape of the JSON. Read that first if you
just want the data; read this if you want the collector to survive a crash.

> **Scope and portability.** This is a generic recipe for any Linux queue manager
> — install the IBM sample, drop in one shell script, define one `SERVICE`. It uses
> only stock IBM MQ, `bash`, and standard `util-linux` / `procps-ng` tools; there
> is **no** dependency on any configuration-management system. If you provision with
> Ansible, Salt, Puppet, Chef, or shell, wrap the same steps in your tool of choice
> — the MQSC and the script below are the payload.

**Validation provenance.** The mechanism and the timings quoted here were exercised
end-to-end on IBM MQ **9.4.5** (RHEL 9.6): the group-kill stop reaps both processes
with no orphan, and a `kill -9` of the collector recovers through the 2042 window as
described. Where a number is build-specific (the stale-handle reap time), it is
called out as *observed*, not guaranteed.

---

## Why a wrapper at all

An MQ `SERVICE` is **start-once**. `CONTROL(QMGR)` makes the queue manager launch
the service's `STARTCMD` when it starts and stop it when it stops — but if the
started process *dies on its own*, MQ does **not** restart it. Unlike a systemd
unit, there is no `Restart=on-failure`. The feed simply stops, silently, until the
next queue-manager restart.

`amqsevt` can die on its own. It is a non-transactional PCF-parsing sample with
**no signal handlers** and no restart logic. A genuinely corrupt event message can
make it over-read a PCF structure and crash the process; because it consumes each
event destructively (outside syncpoint), that one event is lost and — without a
wrapper — so is every event after it, because nothing brings the collector back.

The fix is a thin **restart loop** around `amqsevt`, run *as* the service's
`STARTCMD`. When `amqsevt` exits for any reason, the still-alive wrapper logs it,
waits, and relaunches it. That is the entire idea; the rest of this document is the
two non-obvious details that make the loop safe: a clean **stop**, and surviving
**2042** on a fast restart.

---

## Architecture at a glance

```text
queue manager (CONTROL(QMGR))
  └─ STARTCMD: setsid -w -- /opt/mq-event-monitor/run.sh <QM>
        └─ run.sh            ← process-group leader (PGID == its PID), the restart loop
              └─ amqsevt -m <QM> -o json_compact   ← child in run.sh's group
                    │ stdout: one JSON object per line
                    └─▶ logger --size 32768 -t mq-events   → syslog / journald
```

Three moving parts:

1. **The `SERVICE`** (`CONTROL(QMGR)`) — ties the collector's lifecycle to the
   queue manager, so it starts, stops, and fails over with it.
2. **`setsid`** — makes `run.sh` its own **process-group leader**, so the service's
   stop can signal the wrapper *and* `amqsevt` together and nothing else.
3. **`run.sh`** — the restart loop, plus a bounded give-up so a permanently-stuck
   collector fails *loudly* instead of spinning forever.

---

## Quick start

Placeholders: `<QM>` = queue-manager name. The service runs as the `mqm` user.

### 1. Install the MQ samples (they provide `amqsevt`)

`amqsevt` is **not** in the base MQ runtime; it ships in the samples package and
installs to `/opt/mqm/samp/bin/amqsevt`.

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm
```

### 2. Enable the event classes on the queue manager

The classes you want must be turned on, or the event queues stay empty. See the
[events-to-JSON how-to](2026-07-28-mq-event-monitoring-to-file.md) for the full
class reference; the broad enable is (`runmqsc <QM>`):

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) +
  SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

(Omit `LOGGEREV` unless the queue manager uses **linear** logging — on a
circular-logging queue manager it is rejected and takes the whole `ALTER` with it.)

### 3. Install the wrapper script

Create the run directory and drop in `run.sh` (full listing in
[The wrapper script](#the-wrapper-script) below), then make it executable and
owned by `mqm`:

```bash
install -d -o mqm -g mqm /opt/mq-event-monitor
install -o mqm -g mqm -m 0755 run.sh /opt/mq-event-monitor/run.sh
```

### 4. Define and start the service

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/usr/bin/setsid') +
  STARTARG('-w -- /opt/mq-event-monitor/run.sh +QMNAME+') +
  STOPCMD('/bin/kill') +
  STOPARG('-TERM -- -+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON on syslog')

START SERVICE(MQ.EVENT.MONITOR)
```

### 5. Verify

```mqsc
DISPLAY SVSTATUS(MQ.EVENT.MONITOR)    * expect STATUS(RUNNING) with a PID
```

```bash
journalctl -t mq-events -f            # one JSON object per line
```

Done. The queue manager now starts the collector on start, restarts it if it
crashes, and stops it cleanly on stop.

---

## The wrapper script

`run.sh` — a restart loop with a bounded give-up. The configuration block at the
top is the only part you routinely touch.

```bash
#!/bin/bash
# amqsevt event-collector wrapper — self-healing restart loop, run AS an MQ SERVICE
# STARTCMD (see the DEFINE SERVICE in this document). $1 is the queue-manager name.
#
# Launched under `setsid` so this script is its own process-group leader
# (PGID == PID). amqsevt then runs as a child in that group, and the SERVICE STOPCMD
# `kill -TERM -- -<pid>` (negative pid) reaps the whole group — this script AND
# amqsevt — in one signal. See "The service definition" for why that is necessary.
set -u
QM="$1"

# --- Configuration -----------------------------------------------------------
AMQSEVT="/opt/mqm/samp/bin/amqsevt"   # the IBM MQ sample binary
TAG="mq-events"                        # syslog/journald tag for the event stream
SLEEP=10                               # wait before restarting amqsevt after it exits
OPEN_RETRY=1800                        # seconds of fast-failing before giving up (0 = forever)
LOGGER_MAX=32768                       # max bytes per `logger` message (avoid 1 KiB line-split)
HEALTHY_RUN=60                         # an amqsevt run >= this long counts as "worked"
ERRLOG="/tmp/mq-event-monitor.${QM}.stderr"   # last run's amqsevt stderr (mqm-writable)
# -----------------------------------------------------------------------------

say() { logger -t "$TAG" "run.sh[$$]: $*"; }   # wrapper lifecycle -> syslog

say "starting event collector for ${QM} (pgid $(ps -o pgid= -p $$ | tr -d ' '))"
fail_deadline=0
while true; do
  start=$(date +%s)
  # amqsevt stdout (pure single-line JSON) -> its own logger, so each event is one
  # syslog entry. stderr -> ERRLOG, so a failed run's MQ reason code can be surfaced.
  stdbuf -oL "${AMQSEVT}" -m "${QM}" -o json_compact \
    2>"${ERRLOG}" > >(exec logger --size "${LOGGER_MAX}" -t "${TAG}")
  rc=$?
  ran=$(( $(date +%s) - start ))
  # First "reason code" line is the root cause (e.g. MQOPEN ... 2042), before the
  # shutdown-path MQCLOSE noise.
  reason=$(grep -iE 'reason code|MQRC_' "${ERRLOG}" 2>/dev/null | head -1 | tr -s ' ')

  if [ "${ran}" -ge "${HEALTHY_RUN}" ]; then
    # Ran and consumed for a while, then exited -> a genuine crash; reset the burst.
    fail_deadline=0
    say "amqsevt exited rc=${rc} after ${ran}s${reason:+ [${reason}]}; restarting in ${SLEEP}s"
  else
    # Fast exit -> a crash-loop, or a 2042 open that has not reaped yet; count toward
    # the bound so a permanently-held queue eventually gives up loudly.
    [ "${fail_deadline}" -eq 0 ] && fail_deadline=$(( $(date +%s) + OPEN_RETRY ))
    if [ "${OPEN_RETRY}" -ne 0 ] && [ "$(date +%s)" -ge "${fail_deadline}" ]; then
      say "amqsevt kept failing fast (rc=${rc})${reason:+ [${reason}]} for >${OPEN_RETRY}s — giving up; SERVICE goes DOWN (loud)"
      exit "${rc}"
    fi
    say "amqsevt exited rc=${rc} after ${ran}s (fast)${reason:+ [${reason}]}; restarting in ${SLEEP}s"
  fi
  sleep "${SLEEP}"
done
```

What each non-obvious piece buys you:

- **`logger --size 32768`.** `logger` defaults to RFC 3164's 1 KiB and **splits**
  any longer line into multiple syslog entries — which shreds a large event (a full
  `CONFIG` object dump can run several KiB) into fragments, none of them valid JSON.
  Sizing the limit above your largest event keeps each event one parseable entry.
  Keep it under your syslog daemon's own line ceiling (journald's default `LineMax`
  is 48 KiB).
- **stderr → `ERRLOG`, then grep for the reason.** `amqsevt` writes diagnostics to
  stderr; capturing them lets the wrapper name the MQ reason (e.g. `...2042`) on a
  failed run **without** polluting the JSON stream on stdout.
- **`HEALTHY_RUN` vs. fast-fail.** A run that lasted a while and then exited is a
  real crash — log it and restart on a clean slate. A run that exits almost
  immediately is either a crash-loop or a not-yet-reaped 2042; those are counted
  toward `OPEN_RETRY` so the collector cannot fast-fail forever in silence.
- **`OPEN_RETRY` → loud give-up.** If fast failures continue past the bound (the
  event queue is genuinely held by a live owner, not a transient reap), the wrapper
  **exits non-zero** so the `SERVICE` goes `DOWN` visibly. Failing loud beats an
  invisible infinite retry.

> **The wrapper's own log lines use a direct `logger`, not the `amqsevt` pipe** —
> that pipe only exists while `amqsevt` is alive, so lifecycle messages have to go
> out on their own.

---

## The service definition

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/usr/bin/setsid') +
  STARTARG('-w -- /opt/mq-event-monitor/run.sh +QMNAME+') +
  STOPCMD('/bin/kill') +
  STOPARG('-TERM -- -+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON on syslog')
```

- **`CONTROL(QMGR)`** — the queue manager owns the collector's lifecycle: it starts
  with the queue manager, stops with it, and (on a multi-instance or Native HA queue
  manager) runs only on the **active** instance, so it follows the queue manager
  across failover with no extra plumbing.
- **`STARTCMD('/usr/bin/setsid')`, not `amqsevt` or `run.sh` directly** — this is
  the load-bearing trick. MQ spawns the `STARTCMD` process as a **child of its
  service manager (`amqzmgr0`), not as a process-group leader**. Running `run.sh`
  directly would leave it sharing the service manager's process group, and then
  neither stop option is safe (see below). `setsid` makes `run.sh` a **new
  session/group leader** (`PGID == PID`), with `amqsevt` as its child in that
  dedicated group.
- **`STARTARG('-w -- … +QMNAME+')`** — MQ word-splits the argument string and
  expands `+QMNAME+` to the queue-manager name, so the launched process is
  `setsid -w -- /opt/mq-event-monitor/run.sh <QM>`. `-w` makes `setsid` wait for the
  script and return its exit status (so MQ can track it); `--` ends option parsing.
- **`STOPARG('-TERM -- -+MQ_SERVER_PID+')` — the negative-PID group kill.** MQ
  substitutes `+MQ_SERVER_PID+` with the started process's PID (here `run.sh`, the
  group leader) **even when embedded** in a larger argument, so this expands to
  `kill -TERM -- -<pid>`. The **negative** PID makes `kill` signal the whole
  **process group** — reaping `run.sh` and `amqsevt` together, no orphan. The `--`
  is for `procps-ng` `/bin/kill`, so `-<pgid>` is read as a group rather than an
  option.

**Why the two obvious alternatives fail, and `setsid` fixes both:**

| Stop attempt | Without `setsid` | With `setsid` |
|---|---|---|
| `kill <pid>` (positive) | Kills only `run.sh`; `amqsevt` is **orphaned** and keeps running | n/a — the group kill is strictly better |
| `kill -<pid>` (negative) | Signals the **service manager's** group — hits `amqzmgr0` and its other children | Signals **only** our dedicated group: `run.sh` + `amqsevt` |

That is the whole reason `setsid` is in the `STARTCMD`: it manufactures a private
process group so the group-kill stop is both **complete** (no orphan) and
**contained** (nothing else signalled).

---

## Surviving 2042 on restart

When `amqsevt` crashes and the wrapper restarts it quickly, the restart often fails
with **`MQRC_OBJECT_IN_USE` (2042)** for the first couple of tries, then succeeds.
That is expected, and understanding it is what sizes `SLEEP`.

- **`amqsevt` opens the event queue for EXCLUSIVE input.** It opens with
  `MQOO_INPUT_AS_Q_DEF`, and `SYSTEM.ADMIN.QMGR.EVENT` defaults to `DEFSOPT(EXCL)`,
  so only one reader can hold it. That is the mode you want (event ordering; no
  second reader interleaving) — but it means a *second* opener gets 2042.
- **A crash leaves a stale handle.** `amqsevt` has no signal handlers, so a crash
  (or a `SIGTERM`) kills it **before** it can `MQCLOSE`. The exclusive handle is
  left held until the **queue manager reaps** the dead connection.
- **The reap is not instant.** On the tested build (MQ 9.4.5) the stale handle
  cleared in a **consistent ~29 s**, which looks like a periodic queue-manager
  housekeeping sweep of roughly 30 s rather than immediate detection. Worst case is
  therefore about one sweep interval; best case (killed just before a sweep) is
  near-zero. IBM does not publish this timing — treat ~30 s as a build-specific
  observation, and measure your own if it matters.

With `SLEEP` (10 s) shorter than the reap, a crash restart retries roughly three
times — hitting 2042 twice — then succeeds on the third once the handle is reaped.
Recovery lands at ~30 s either way, gated by the reap, so a longer `SLEEP` would not
recover faster; it would only hide the retries. Keeping `SLEEP` short makes that
**fail-twice-then-succeed** pattern a visible, no-cost heartbeat that the retry path
is alive. IBM's own 2042 guidance is exactly this — *"system design should specify
whether an application is to wait and retry"* — and this wrapper is the wait-and-retry.

---

## Testing it yourself

No special harness — provoke the two failure modes and read the log.

**Clean stop (no orphan).** With the service running, stop it and confirm both
processes are gone:

```bash
echo 'STOP SERVICE(MQ.EVENT.MONITOR)' | runmqsc <QM>
ps -u mqm -o pid,ppid,pgid,args | grep -E 'run\.sh|amqsevt'   # expect: nothing
```

You should see `AMQ8732I` and no surviving `run.sh` or `amqsevt`. "Clean" here means
**no orphan** — not a graceful `MQCLOSE`, which `amqsevt` never does on a signal; a
brief stale handle afterward is normal and self-heals on the next reap.

**Crash recovery through the 2042 window.** Start it again, then hard-kill the
`amqsevt` child and watch the wrapper bring it back:

```bash
echo 'START SERVICE(MQ.EVENT.MONITOR)' | runmqsc <QM>
kill -9 "$(pgrep -f 'amqsevt -m <QM>')"
journalctl -t mq-events -f
```

The expected heartbeat — fast-fail on 2042 a couple of times, then a healthy run:

```text
run.sh[…]: amqsevt exited rc=137 after 3s (fast); restarting in 10s
run.sh[…]: amqsevt exited rc=0 after 0s [... MQRC_OBJECT_IN_USE [2042]]; restarting in 10s
run.sh[…]: amqsevt exited rc=0 after 0s [... MQRC_OBJECT_IN_USE [2042]]; restarting in 10s
```

After the reap, `DISPLAY SVSTATUS(MQ.EVENT.MONITOR)` reads `RUNNING` again and the
JSON feed resumes.

---

## Choosing the sink

This document defaults to a **syslog** sink (`amqsevt | logger`), because syslog
inherits the platform's existing forwarding and rotation — you do not have to build
a file tailer or a rotation policy. Point your log pipeline at the `mq-events` tag
and you are done.

If you need a **file** instead, `run.sh` is a one-line change — redirect `amqsevt`'s
stdout to a file rather than piping it through `logger`:

```bash
stdbuf -oL "${AMQSEVT}" -m "${QM}" -o json_compact 2>"${ERRLOG}" >> "${DATA_FILE}"
```

A file sink then adds two responsibilities the syslog path gave you for free —
**forwarding with a checkpointed read offset** and **copy-truncate rotation** (never
rename-and-recreate; the collector holds the file open and does not reopen on a
signal). Those, plus the general best-effort-not-exactly-once caveat, are covered in
the [events-to-JSON file-sink how-to](2026-07-28-mq-event-monitoring-to-file.md).

Either way, remember the collector is **destructive and non-transactional**: it
removes each event from MQ as it reads it, so a crash *between* the get and the write
loses that one event permanently. If lossless delivery is a hard requirement, this
sample-based pattern is the wrong tool — build a transactional consumer instead.

---

## Operational notes

- **A running queue manager does not imply a running collector.** The `SERVICE` is a
  moving part with its own health. Monitor `DISPLAY SVSTATUS(MQ.EVENT.MONITOR)` for
  `RUNNING`; a `DOWN` service means the wrapper hit its `OPEN_RETRY` bound and gave
  up loudly — investigate what is holding the event queue.
- **"Clean stop" ≠ graceful close.** Because `amqsevt` has no signal handler, a stop
  reaps the processes and lets the queue manager reap the connection; do not expect
  an immediate handle close.
- **Bursts and rate-limiting.** With every class enabled on a busy queue manager the
  feed can be high. If your syslog daemon rate-limits (journald does by default), a
  burst can be **silently dropped** — raise or disable the rate limit for this tag,
  or you will lose events without any error. Start with a broad enable, then pare the
  classes back to the ones you act on.
- **`STOP` then `START` too fast** hits the same 2042 as a crash restart — let the
  stop complete before restarting.

---

## References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *DEFINE SERVICE*,
*Replaceable inserts on service definitions*, *Developing a service*, and
*2042 (07FA) (RC2042): MQRC_OBJECT_IN_USE*. For the event classes, the JSON
envelope, and captured example events, see the companion report
[`2026-07-28-mq-event-monitoring-to-file.md`](2026-07-28-mq-event-monitoring-to-file.md).
