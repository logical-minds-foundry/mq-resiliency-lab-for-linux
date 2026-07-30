# Running `amqsevt` as a resilient queue-manager service — writing events to a file

IBM MQ 9.4 for Multiplatforms (Linux). Runs the `amqsevt` event monitor as a
**managed queue-manager `SERVICE`** that starts and stops with the queue manager,
travels with it across failover, and **restarts itself if it dies** — writing the
events as **line-delimited JSON to a file** for a file-monitoring agent to pick up,
and the wrapper's own diagnostics to a companion `.error` file.

This is the script **as installed** — a single-purpose, tested artifact you drop in
as written. It has been exercised end-to-end on IBM MQ 9.4 running a **Native HA**
queue manager: clean stop with no orphaned process, crash recovery through the
`MQRC_OBJECT_IN_USE` (2042) exclusive-handle window, well-formed JSONL output, and
destructive drain of the event queues all verified.

> **Portability.** Stock IBM MQ, `bash`, and `util-linux` / `procps-ng` only — no
> syslog daemon and no configuration-management dependency. Install the sample, drop
> in one shell script, define one `SERVICE`.

---

## Why a wrapper at all

An MQ `SERVICE` is **start-once**. `CONTROL(QMGR)` makes the queue manager launch
the service's `STARTCMD` when it starts and stop it when it stops — but if the
started process *dies on its own*, MQ does **not** restart it. Unlike a systemd
unit, there is no `Restart=on-failure`. The feed simply stops, silently, until the
next queue-manager restart.

`amqsevt` can die on its own. It is a non-transactional PCF-parsing sample with
**no signal handlers** and no restart logic. A genuinely corrupt event message can
make it over-read a PCF structure and crash; because it consumes each event
destructively, that one event is lost and — without a wrapper — so is every event
after it, because nothing brings the collector back.

The fix is a thin **restart loop** around `amqsevt`, run *as* the service's
`STARTCMD`. When `amqsevt` exits for any reason, the still-alive wrapper logs it,
waits, and relaunches it. Two non-obvious details make the loop safe: a clean
**stop** (no orphaned `amqsevt`) and surviving **2042** on a fast restart. Both are
handled below.

## Where the two streams go

- **Events** — `amqsevt` stdout (`-o json_compact`, one JSON object per line) is
  **appended to a `.json` data file**. That file *is* the sink; a downstream agent
  tails and forwards it.
- **Diagnostics** — the wrapper's own lifecycle lines and `amqsevt`'s stderr both
  land in a companion **`.error` file**, via the `SERVICE`'s `STDOUT`/`STDERR`
  redirect. It is a catch-all, normally quiet; it is **not** the data.

---

## Quick start

Placeholders: `<QM>` = queue-manager name. The service runs as `mqm`. The only
site-specific values are the two file paths.

### 1. Install the MQ samples (they provide `amqsevt`)

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm   # (apt/zypper equivalent per distro)
```

### 2. Enable the event classes on the queue manager (`runmqsc <QM>`)

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) +
  SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

(Omit `LOGGEREV` unless the queue manager uses **linear** logging — on a
circular-logging queue manager it is rejected and takes the whole `ALTER` with it.)

### 3. Create the data directory and install the wrapper

```bash
install -d -o mqm -g mqm -m 0750 /var/mqm/event-monitor
install -o mqm -g mqm -m 0755 run.sh /opt/mq-event-monitor/run.sh
```

### 4. Define and start the service (`runmqsc <QM>`)

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/usr/bin/setsid') +
  STARTARG('-w -- /opt/mq-event-monitor/run.sh +QMNAME+') +
  STDOUT('/var/mqm/event-monitor/+QMNAME+.error') +
  STDERR('/var/mqm/event-monitor/+QMNAME+.error') +
  STOPCMD('/bin/kill') +
  STOPARG('-TERM -- -+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT as JSONL to a file (.error=diag)')

START SERVICE(MQ.EVENT.MONITOR)
```

### 5. Verify

```mqsc
DISPLAY SVSTATUS(MQ.EVENT.MONITOR)    * expect STATUS(RUNNING) with a PID
```

```bash
tail -f /var/mqm/event-monitor/<QM>.events.json   # one JSON object per line (JSONL)
```

Done. The queue manager starts the collector on start, restarts it if it crashes,
stops it cleanly on stop, and appends JSON events to the data file.

---

## The wrapper script

`run.sh`, exactly as installed. The only lines you set for your site are the two
file paths at the top; nothing else is edited.

```bash
#!/bin/bash
# MQ event collector launcher — self-healing wrapper. Started by the queue manager as an MQ
# SERVICE object (CONTROL(QMGR)), so it travels with the QM across every failover.
#
# LAUNCHED UNDER setsid: the SERVICE STARTCMD is `setsid -w -- /opt/mq-event-monitor/run.sh <QM>`.
# MQ spawns the STARTCMD process as a child of its service manager, NOT as a group leader, so
# `setsid` execs THIS script as its own session/process-group leader (PGID == its PID); amqsevt runs
# as our child in that group. The SERVICE STOPCMD is `kill -TERM -- -<pid>` (the +MQ_SERVER_PID+
# insert, this wrapper's pid) — a NEGATIVE pid that SIGTERMs the WHOLE group, reaping wrapper AND
# amqsevt in one shot. That is why the setsid group leader is load-bearing: a positive kill would
# orphan amqsevt.
#
# WHY A WRAPPER: amqsevt is a non-transactional PCF-parsing sample with NO signal handlers and NO
# restart of its own — an MQ SERVICE is start-once, unlike systemd. This loop restores it: on any
# amqsevt exit the wrapper (still alive) logs, sleeps, and re-runs it.
#
# 2042 ON RESTART (deliberately visible): amqsevt opens the event queue for EXCLUSIVE input, and the
# QM takes ~30s to reap a dead handle. A fast restart re-opens before the reap and exits with 2042;
# with SLEEP < reap the loop retries a few times, hitting 2042 twice, then succeeds. That fail-twice-
# then-succeed pattern is a DELIBERATE, no-cost tell that the retry path is alive. Fast failures past
# OPEN_RETRY make the wrapper exit non-zero so the SERVICE goes DOWN visibly (fail loud).
#
# Output (FILE sink): amqsevt stdout (`-o json_compact`) is APPENDED to the .json data file
# (DATA_FILE), one JSON object per line (JSONL). amqsevt diagnostics go to STDERR -> captured so the
# wrapper can surface the MQ reason (e.g. "...2042") on a failed run, then flushed to the .error file
# (the SERVICE STDOUT/STDERR redirect), where the wrapper's own lifecycle lines (`say`) land too.
#
# $1 is the queue-manager name (the SERVICE STARTARG).
set -u
QM="$1"
DATA_FILE="/var/mqm/event-monitor/<QM>.events.json"   # JSONL event stream — THIS is the sink (append)
ERROR_FILE="/var/mqm/event-monitor/<QM>.error"        # wrapper lifecycle + amqsevt diagnostics (SERVICE STDOUT/STDERR)
SLEEP=10
OPEN_RETRY=1800                              # seconds of fast-failing before giving up; 0 = forever
HEALTHY_RUN=60                               # an amqsevt run >= this long counts as "worked" (resets the burst)
ERRLOG="/tmp/mq-event-monitor.${QM}.stderr"  # last run's amqsevt stderr (mqm-writable), overwritten each loop

say() { echo "run.sh[$$]: $*" >&2; }         # wrapper lifecycle -> stderr -> SERVICE STDERR (.error)

say "starting event collector for ${QM} (pgid $(ps -o pgid= -p $$ | tr -d ' '))"
fail_deadline=0
while true; do
  start=$(date +%s)
  stdbuf -oL /opt/mqm/samp/bin/amqsevt -m "${QM}" -o json_compact \
    2>"${ERRLOG}" >> "${DATA_FILE}"
  rc=$?
  ran=$(( $(date +%s) - start ))
  # first 'reason code' line = the ROOT cause (e.g. MQOPEN ... 2042), before MQCLOSE shutdown noise
  reason=$(grep -iE 'reason code|MQRC_' "${ERRLOG}" 2>/dev/null | head -1 | tr -s ' ')
  cat "${ERRLOG}" >&2   # flush this run's raw amqsevt diagnostics into .error (SERVICE STDERR redirect)

  if [ "${ran}" -ge "${HEALTHY_RUN}" ]; then
    # Ran and consumed for a while, then exited -> a genuine crash; start a fresh burst.
    fail_deadline=0
    say "amqsevt exited rc=${rc} after ${ran}s${reason:+ [${reason}]}; restarting in ${SLEEP}s"
  else
    # Fast exit -> crash-loop or a 2042 open that has not reaped yet; count toward the bound.
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

Two file-specific points:

- **`>> "${DATA_FILE}"`.** The events are appended straight to the data file. An MQ
  `SERVER` service opens its `STDOUT`/`STDERR` `O_APPEND`, so restarts and failovers
  add to the `.error` file rather than truncating it; the in-script `>>` gives the
  data file the same append semantics.
- **`say` → stderr, and `cat "${ERRLOG}" >&2`.** With no syslog, the wrapper's
  lifecycle lines go to its stderr, which the `SERVICE` redirects to `.error`; each
  run's captured `amqsevt` stderr is flushed there too, so `.error` carries both the
  wrapper narrative and the raw diagnostics (including the root-cause 2042).

---

## The service definition — two things that bite

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/usr/bin/setsid') +
  STARTARG('-w -- /opt/mq-event-monitor/run.sh +QMNAME+') +
  STDOUT('/var/mqm/event-monitor/+QMNAME+.error') +
  STDERR('/var/mqm/event-monitor/+QMNAME+.error') +
  STOPCMD('/bin/kill') +
  STOPARG('-TERM -- -+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT as JSONL to a file (.error=diag)')
```

- **`STARTCMD('/usr/bin/setsid')`, not `run.sh` directly.** MQ spawns the `STARTCMD`
  inside its service manager's process group. `setsid` gives `run.sh` its own group
  (PGID == PID); the negative-PID `STOPCMD` (`-TERM -- -+MQ_SERVER_PID+` →
  `kill -TERM -- -<pid>`) then reaps the wrapper *and* `amqsevt` together — a clean
  stop with no orphan.
- **`DESCR` must be ≤ 64 characters.** MQ rejects a longer service description with
  `AMQ8413E` (String Length Error) and aborts the whole `DEFINE`. Keep it short and
  path-free — do **not** embed the data-file path in it.

`+QMNAME+` and `+MQ_SERVER_PID+` are MQ replaceable inserts; MQ expands them even
when embedded in a larger argument.

---

## Surviving 2042 on restart

When `amqsevt` crashes and the wrapper restarts it quickly, the restart often fails
with **`MQRC_OBJECT_IN_USE` (2042)** for the first couple of tries, then succeeds —
because `amqsevt` opens the event queue for **exclusive** input and a crash leaves a
stale handle the queue manager reaps in about **30 s**. With `SLEEP` (10 s) shorter
than the reap, the loop retries a few times — hitting 2042 twice — then lands a
healthy run. Recovery is gated by the reap either way; keeping `SLEEP` short makes
that **fail-twice-then-succeed** pattern a visible, no-cost heartbeat in `.error`.

---

## Testing it yourself

Provoke the two failure modes and read the files.

**Clean stop (no orphan).** With the service running, stop it and confirm both
processes are gone:

```bash
echo 'STOP SERVICE(MQ.EVENT.MONITOR)' | runmqsc <QM>
ps -u mqm -o pid,pgid,args | grep -E 'run\.sh|amqsevt'   # expect: nothing
```

**Crash recovery through the 2042 window.** Start it again, hard-kill the `amqsevt`
child, and watch the wrapper bring it back:

```bash
echo 'START SERVICE(MQ.EVENT.MONITOR)' | runmqsc <QM>
kill -9 "$(pgrep -f 'amqsevt -m <QM>')"
tail -f /var/mqm/event-monitor/<QM>.error       # fail-twice-on-2042, then a healthy run
```

After the reap, `DISPLAY SVSTATUS(MQ.EVENT.MONITOR)` reads `RUNNING` again and the
`.json` feed resumes.

---

## Before you rely on it — the file sink's responsibilities

The collector is resilient; a working end-to-end **pipeline** still needs these:

1. **Forward the file with a checkpointed read offset.** Something must tail the
   `.json` and forward it continuously, persisting its position — restart without a
   saved offset and you either re-send everything or drop what arrived while it was
   down.
2. **Rotate the file — copy-truncate only.** It grows without bound. The collector
   holds it open with **no reopen-on-signal**, so rotation must be **copy-truncate**;
   rename-and-recreate strands the collector on the old inode.
3. **Monitor the collector service.** A running queue manager does not imply a
   running collector. Watch `DISPLAY SVSTATUS(MQ.EVENT.MONITOR)` for `RUNNING`; a
   `DOWN` service means the wrapper hit `OPEN_RETRY` and gave up loudly.

And two properties that are inherent:

- **Best-effort, not exactly-once.** `amqsevt` consumes each event
  **non-transactionally** — read, then written — so a crash *between* the get and
  the write loses that one event permanently. If lossless delivery is a hard
  requirement, this sample-based pattern is the wrong tool; build a transactional
  consumer.
- **The `.json`/`.error` files are host-local.** On failover the queue manager moves
  to another node and starts a **fresh** data file there; any events written on the
  old node but **not yet forwarded** are **stranded** — they do not travel with the
  queue manager. Keeping the forwarder current bounds the window but cannot close it.

---

## References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *DEFINE SERVICE*,
*Replaceable inserts on service definitions*, *Developing a service*, and
*2042 (07FA) (RC2042): MQRC_OBJECT_IN_USE*.
