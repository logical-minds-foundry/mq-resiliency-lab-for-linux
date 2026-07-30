# Running `amqsevt` as a resilient queue-manager service — the **file-sink** variant

IBM MQ 9.4 for Multiplatforms (Linux). Runs the `amqsevt` event monitor as a
**managed queue-manager `SERVICE`** that starts and stops with the queue manager,
travels with it across failover, and **restarts itself if it dies** — writing the
events as **line-delimited JSON to a file** for a file-monitoring agent to pick up,
and the wrapper's own diagnostics to a companion `.error` file.

**This is the tested file-sink script, delivered as installed — not a syslog
script with a "change one line" footnote.** Its companion,
[`2026-07-28-mq-event-monitor-resilient-service.md`](2026-07-28-mq-event-monitor-resilient-service.md),
documents the **syslog** sink; this document is the file sink as a first-class,
independently tested artifact. Pick the sink your platform wants — syslog if you
have a log pipeline already, a file if a file-watching agent is what forwards your
events — and install that sink's script verbatim.

> **Scope and portability.** A generic recipe for any Linux queue manager: install
> the IBM sample, drop in one shell script, define one `SERVICE`. Stock IBM MQ,
> `bash`, and `util-linux` / `procps-ng` only — **no** `logger`, **no** syslog, and
> no dependency on any configuration-management system. In the lab the sink is a
> render-time choice of the `mq-event-monitor` Ansible role (`mq_event_sink: file`),
> so the script that lands on the box is single-purpose; provisioned by another
> tool, or by hand, the script below is the payload.

**Validation provenance.** The mechanism and timings here were exercised
end-to-end on IBM MQ **9.4.x** running a **Native HA** queue manager (`NHAUAPP`),
on 2026-07-29: the file variant passed the full B-matrix plus the two file-output
asserts — **A1** (well-formed JSONL), **A2** (destructive drain), **B2** (clean
stop, no orphan), **B3** (crash recovery through the `MQRC_OBJECT_IN_USE` 2042
window). The captured run is
[`assets/mq-event-monitor-file-sink/harness-run.txt`](assets/mq-event-monitor-file-sink/harness-run.txt).

> **A note on the test platform.** The lab's RHEL Native HA arm is an **x86_64**
> baked box that cannot be built on the arm64 lab host (cross-arch emulated box
> builds are disabled by design), so the validation ran on the **Ubuntu** Native HA
> arm. It is the *same* mechanism — the same `mq-event-monitor` role, the same
> `amqsevt` sample, the same `SERVICE`/`setsid` model — so the corner cases proven
> here are OS- and arch-independent.

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
destructively (outside syncpoint), that one event is lost and — without a wrapper —
so is every event after it, because nothing brings the collector back.

The fix is a thin **restart loop** around `amqsevt`, run *as* the service's
`STARTCMD`. When `amqsevt` exits for any reason, the still-alive wrapper logs it,
waits, and relaunches it. The two non-obvious details that make the loop safe — a
clean **stop** and surviving **2042** on a fast restart — are identical to the
syslog variant; they are sink-independent.

## What "file sink" changes

Only where the two streams go:

- **Events** — `amqsevt` stdout (`-o json_compact`, one JSON object per line) is
  **appended to a `.json` data file** (`DATA_FILE`). That file *is* the sink; a
  downstream agent tails and forwards it.
- **Diagnostics** — the wrapper's own lifecycle lines and `amqsevt`'s stderr both
  land in a companion **`.error` file**, via the `SERVICE`'s `STDOUT`/`STDERR`
  redirect. `.error` is a catch-all, normally quiet; it is **not** the data.

Everything else — the `setsid` process-group model, the restart loop,
`HEALTHY_RUN`/`OPEN_RETRY`, and the reason-code enrichment that makes the 2042
heartbeat legible — is the same code as the syslog variant.

---

## Quick start

Placeholders: `<QM>` = queue-manager name. The service runs as `mqm`. The two file
paths are the only site-specific values; in the lab they are the role variables
`mq_event_data_file` / `mq_event_error_file` (default
`/var/mqm/event-monitor/<QM>.events.json` and `…/<QM>.error`) — set them in your
management layer, **not** by editing the script.

### 1. Install the MQ samples (they provide `amqsevt`)

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm   # (apt/zypper equivalent per distro)
```

### 2. Enable the event classes on the queue manager

The classes you want must be turned on, or the event queues stay empty
(`runmqsc <QM>`):

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

### 4. Define and start the service

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

`run.sh`, exactly as installed for the file sink — the two `DATA_FILE`/`ERROR_FILE`
paths are the site config (shown here with the tested queue manager's paths); no
other line is site-specific, and there is nothing to hand-edit.

```bash
#!/bin/bash
# MQ event collector launcher — self-healing wrapper. Started by the queue manager as an MQ
# SERVICE object (CONTROL(QMGR)), so it travels with the QM across every failover.
#
# LAUNCHED UNDER setsid: the SERVICE STARTCMD is `setsid -w -- /opt/mq-event-monitor/run.sh <QM>`.
# MQ spawns the STARTCMD process as a child of amqzmgr0, NOT as a group leader, so `setsid` execs
# THIS script as its own session/process-group leader (PGID == its PID); amqsevt runs as our child
# in that group. The SERVICE STOPCMD is `kill -TERM -- -<pid>` (the +MQ_SERVER_PID+ insert, this
# wrapper's pid) — a NEGATIVE pid that SIGTERMs the WHOLE group, reaping wrapper AND amqsevt in one
# shot. That is why the setsid group leader is load-bearing: a positive kill would orphan amqsevt.
#
# WHY A WRAPPER: amqsevt is a non-transactional PCF-parsing sample with NO signal handlers and NO
# restart of its own — an MQ SERVICE is start-once, unlike systemd. This loop restores it: on any
# amqsevt exit the wrapper (still alive) logs, sleeps, and re-runs it.
#
# 2042 ON RESTART (deliberately visible): amqsevt opens the event queue for EXCLUSIVE input, and the
# QM takes ~29s to reap a dead handle. A fast restart re-opens before the reap and exits with 2042;
# with SLEEP < reap the loop retries ~3x, hitting 2042 twice, then succeeds. That fail-twice-then-
# succeed pattern is a DELIBERATE, no-cost tell that the retry path is alive. Fast failures past
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

What the file-specific pieces buy you:

- **`>> "${DATA_FILE}"`.** The events are the *only* thing on the wrapper's
  arrangement for stdout, appended straight to the data file. An MQ `SERVER`
  service opens its own `STDOUT`/`STDERR` `O_APPEND`, so restarts and failovers add
  to the `.error` file rather than truncating it (proven in
  `2026-07-20-mq-service-stdout-open-mode-evidence.md`); the in-script `>>` gives
  the data file the same append semantics.
- **`say` → stderr, and `cat "${ERRLOG}" >&2`.** With no `logger`, the wrapper's
  lifecycle lines go to its stderr, which the `SERVICE` redirects to `.error`; the
  per-run `ERRLOG` capture is flushed there too, so `.error` carries both the
  wrapper narrative *and* the raw `amqsevt` diagnostics (including the root-cause
  2042). The reason-grep is the **same** `head -1` root-cause logic as the syslog
  variant, so the 2042 heartbeat reads identically.

---

## The service definition

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

- **`STDOUT`/`STDERR` → `.error`** is the file-sink difference from the syslog
  definition; everything else (`CONTROL(QMGR)`, the `setsid` `STARTCMD`, the
  negative-PID group-kill `STOPCMD`) is identical and sink-independent.
- **`DESCR` must be ≤ 64 characters.** MQ rejects a longer service description
  with `AMQ8413E` (String Length Error) and aborts the whole `DEFINE` — which is
  exactly how the first live deploy of this variant failed, when the description
  embedded the full data-file path. Keep it short and path-free.
- `+QMNAME+` and `+MQ_SERVER_PID+` are MQ replaceable inserts; MQ expands them even
  when embedded in a larger argument.

---

## Surviving 2042 on restart

When `amqsevt` crashes and the wrapper restarts it quickly, the restart often fails
with **`MQRC_OBJECT_IN_USE` (2042)** for the first couple of tries, then succeeds —
because `amqsevt` opens the event queue for **exclusive** input and a crash leaves a
stale handle the queue manager reaps in a consistent **~29 s**. With `SLEEP` (10 s)
shorter than the reap, the loop retries a few times — hitting 2042 twice — then
lands a healthy run. Recovery is gated by the reap either way; keeping `SLEEP` short
makes that **fail-twice-then-succeed** pattern a visible, no-cost heartbeat. This is
IBM's own wait-and-retry guidance for 2042, and it is identical to the syslog
variant; the only difference is that here the heartbeat is legible in **`.error`**
rather than journald.

---

## Testing it yourself

No special harness beyond the lab's `tools/validate-event-monitor-wrapper.sh`, run
in **file** mode; or provoke the corner cases by hand and read the two files.

```bash
tools/validate-event-monitor-wrapper.sh <QM> MQ.EVENT.MONITOR file \
  /var/mqm/event-monitor/<QM>.events.json /var/mqm/event-monitor/<QM>.error
```

The captured run —
[`assets/mq-event-monitor-file-sink/harness-run.txt`](assets/mq-event-monitor-file-sink/harness-run.txt) —
ends `== summary: ALL SCENARIOS PASS ==`, with:

```text
PASS  B1 up + checkpoint (service PID == wrapper PID == PGID = 10562)
PASS  A1 .json is well-formed JSONL (last 20 lines parse)
PASS  A2 destructive drain (events 1->5 in .json; SYSTEM.ADMIN.QMGR.EVENT CURDEPTH=0)
PASS  B2 clean stop — wrapper + amqsevt both reaped, no orphan
PASS  B3 crash recovery (wrapper restarted amqsevt: 10857 -> 10920)
```

and the B3 lifecycle showing the 2042 heartbeat, its root cause captured in
`.error`:

```text
run.sh[…]: amqsevt exited rc=0 after 0s (fast) [MQOPEN of 'SYSTEM.ADMIN.COMMAND.EVENT' ended with reason code MQRC_OBJECT_IN_USE [2042]]; restarting in 10s
run.sh[…]: amqsevt exited rc=137 after 5s (fast); restarting in 10s
… then a healthy run; SVSTATUS RUNNING, .json resumes
```

A live sample of both files (real events in `.json`, lifecycle + diagnostics in
`.error`) is
[`assets/mq-event-monitor-file-sink/live-file-output.txt`](assets/mq-event-monitor-file-sink/live-file-output.txt).

- **Clean stop (no orphan).** `STOP SERVICE(MQ.EVENT.MONITOR)`, then
  `ps -u mqm | grep -E 'run\.sh|amqsevt'` should show **nothing**. "Clean" means no
  orphan, not a graceful `MQCLOSE` (`amqsevt` never does one on a signal; a brief
  stale handle afterward is normal and self-heals on the next reap).
- **Crash recovery.** `kill -9 "$(pgrep -f 'amqsevt -m <QM>')"`, then watch
  `tail -f /var/mqm/event-monitor/<QM>.error` for the fail-twice-then-succeed
  heartbeat, and `/var/mqm/event-monitor/<QM>.events.json` resume.

---

## Follow-on requirements — the file sink's, not the collector's

The collector is resilient; a working end-to-end **pipeline** still needs these,
owned outside this document. They are the price of choosing a file over syslog (a
syslog sink inherits the platform's forwarding and rotation and removes the first
two):

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

And two properties that are inherent, not fixable here:

- **Best-effort, not exactly-once.** `amqsevt` consumes each event
  **non-transactionally** — read, then written — so a crash *between* the get and
  the write loses that one event permanently. If lossless delivery is a hard
  requirement, this sample-based pattern is the wrong tool; build a transactional
  consumer.
- **The `.json`/`.error` files are host-local — an HA-failover stranding window.**
  On failover the queue manager moves to another node and starts a **fresh** data
  file there; any events written on the old node but **not yet forwarded** are
  **stranded** — they do not travel with the queue manager. Keeping the forwarder
  current bounds the window but cannot close it. This window is **specific to the
  file sink**: a syslog sink forwards off-box as events arrive and has no host-local
  tail to strand. It is one of the concrete reasons **syslog is the standing
  recommendation**, and the file sink is the deliberate choice of a site whose
  forwarding agent watches files.

---

## References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *DEFINE SERVICE*,
*Replaceable inserts on service definitions*, *Developing a service*, and
*2042 (07FA) (RC2042): MQRC_OBJECT_IN_USE*. Companion documents: the syslog variant
[`2026-07-28-mq-event-monitor-resilient-service.md`](2026-07-28-mq-event-monitor-resilient-service.md),
the events-to-JSON how-to
[`2026-07-28-mq-event-monitoring-to-file.md`](2026-07-28-mq-event-monitoring-to-file.md),
the `STDOUT`-append evidence
[`2026-07-20-mq-service-stdout-open-mode-evidence.md`](2026-07-20-mq-service-stdout-open-mode-evidence.md),
and the captured event reference in the events-to-JSON how-to's Appendix D.
