# IBM MQ instrumentation events to a file, as JSON

IBM MQ 9.4 for Multiplatforms (Linux / RHEL). Directs a queue manager's
instrumentation events to a **file** as JSON, via a managed queue-manager
service, for a file-monitoring agent to pick up. **The Quick start is the setup; the
Follow-on requirements after it are mandatory before production; the rest is
reference.**

## Changelog — corrections to the version dated 2026-07-17

This version corrects three defects in the 2026-07-17 document. The first two were
**load-bearing**: the earlier recipe, followed verbatim on a circular-logging queue
manager, configured **nothing**.

1. **Removed `LOGGEREV(ENABLED)` from the event-enable command.** `LOGGEREV` is
   valid only on a **linear-logging** queue manager; on a circular-logging queue
   manager MQ rejects it with `AMQ8518E` and — because `ALTER QMGR` is atomic —
   **rejects the entire statement**, so none of the other event classes are
   enabled either. The lab uses circular logging, so the previous command silently
   enabled no events. The corrected command (11 classes) is below.
2. **Removed the launcher script; the service now runs `amqsevt` directly.** The
   2026-07-17 version introduced a wrapper script that redirected `amqsevt` output
   to the data file with an explicit append (`>>`), justified by a claim that the
   service **truncates** its `STDOUT` file on every restart. **That claim is
   false.** A controlled test (two independent methods — content persistence and
   the kernel `open(2)` flags) shows an MQ service opens `STDOUT` with `O_APPEND`
   and **appends** across restarts; it does not truncate. The launcher solved a
   problem that does not exist, so it is gone: point the service's `STDOUT` at the
   data file and run `amqsevt` as the `STARTCMD`. See the companion evidence report
   `2026-07-20-mq-service-stdout-open-mode-evidence.md`.
3. **Expanded the event-loss discussion** to state the HA-failover window
   explicitly (below), and noted that the JSON is **pretty-printed multi-line**,
   not one-object-per-line.

**Validation status:** the corrected mechanism was exercised end-to-end on a live
IBM MQ **9.4.5** queue manager (RHEL 9.6) on **2026-07-20** — the 11-class enable
applied cleanly, the launcher-less service ran `amqsevt -m <QM> -o json` and
appended JSON events to the data file, and forced events (not-authorized 2035,
queue-full, config/command) were observed in the file. The `STDOUT` append
behaviour is separately proven in the evidence report cited above. **Appendix C**
records what was verified.

---

## Quick start

Four steps. Placeholders: `<QM>` = queue-manager name; `<event-log-path>` = the
**data file** the service appends to and your agent watches (writable by `mqm`,
readable by the agent). The service also writes `amqsevt`'s diagnostics to
`<event-log-path>.err` — a catch-all, not the data.

**1. Ensure the MQ samples are installed** — they provide `amqsevt` (it is *not*
in the base MQ runtime):

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm
```

**2. Enable events on the queue manager** (`runmqsc <QM>`):

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) +
  SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

(`LOGGEREV` is deliberately omitted — it is valid only on a linear-logging queue
manager and is rejected on a circular-logging one, taking the whole statement with
it. Add it *only* on a linear-logging queue manager.)

Performance events also need per-queue thresholds — set these on each application
(and transmission) queue that matters:

```mqsc
ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
```

**3. Define and start the collector service** (`runmqsc <QM>`) — set
`<event-log-path>`. The service runs `amqsevt` directly; MQ appends its `STDOUT`
to the data file (no wrapper needed):

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/opt/mqm/samp/bin/amqsevt') STARTARG('-m +QMNAME+ -o json') +
  STDOUT('<event-log-path>') STDERR('<event-log-path>.err') +
  STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') +
  DESCR('Append SYSTEM.ADMIN.*.EVENT as JSON to the event data file')

START SERVICE(MQ.EVENT.MONITOR)
```

`STARTARG` is word-split by MQ and `+QMNAME+` is expanded, so the running process
is `amqsevt -m <QM> -o json`. Its **stdout** (the JSON event stream) is appended to
`<event-log-path>`; its **stderr** goes to `<event-log-path>.err` (a diagnostic
catch, normally empty). `+MQ_SERVER_PID+` is the started `amqsevt` PID, so
`STOPCMD` stops the collector cleanly.

**4. Verify:**

```mqsc
DISPLAY SVSTATUS(MQ.EVENT.MONITOR)    * expect RUNNING with a PID
```

```bash
tail -f <event-log-path>              # JSON objects, one event each (pretty-printed, multi-line)
```

Done. The queue manager starts and stops the collector automatically, and the JSON
event feed is appended to `<event-log-path>`.

> **Note on format:** `amqsevt -o json` emits **pretty-printed, multi-line** JSON
> objects concatenated together — *not* line-delimited JSON (one object per line).
> A consumer that assumes one JSON object per line will break; parse the stream as
> concatenated JSON objects (or post-process to JSONL if your pipeline needs it).

---

## Follow-on requirements — not production-ready until these are owned

> **This is a best-effort feed, not exactly-once — events can be lost.** `amqsevt`
> consumes each event **non-transactionally**: the instant it reads a message, that
> message is gone from the queue, and then it writes. So if the collector crashes
> (or the disk fills and the write fails) **after the get but before the write
> lands**, that event is **dropped, permanently** — consumed, never written, no
> second copy. There is a second, file-specific window on **HA failover**: the data
> file is **host-local**, so any events written on the old node but **not yet
> forwarded** when the queue manager fails over do **not** travel with it — the new
> node starts a fresh file, and the unforwarded tail on the old node is stranded.
> Only a **transactional** consumer — one that gets each event under syncpoint and
> commits only after the downstream forward is acknowledged — closes these windows,
> and `amqsevt` is not that. **If lossless delivery of these events is a hard
> requirement, this pattern is the wrong tool** (build a transactional handler
> instead). This document takes the deliberate minimal-code trade-off: no bespoke
> code, best-effort delivery.

This document produces the JSON event feed; a working end-to-end pipeline needs
four more things, owned outside it. `amqsevt` drains the event queues
**destructively** — once an event is written to the file it exists **nowhere else
in MQ** — which is what makes the requirements below load-bearing rather than
optional.

1. **Monitor and forward the file — checkpointing its position.** Something must
   tail the data file and forward it continuously, persisting its read offset. A
   monitor that restarts without a saved offset re-reads from the top and
   **re-sends everything (duplicate events)**, or seeks to the end and **drops**
   what arrived while it was down — so checkpointing is the requirement. Keeping
   the forwarder current also bounds the HA-failover window above: the fewer
   unforwarded events sitting in the file, the fewer can be stranded on failover.
2. **Rotate the file.** It grows without bound, so it must be rotated externally.
   The collector holds it open with **no reopen-on-signal**, so rotation must be
   **copy-truncate** — never rename-and-recreate, which strands the collector on
   the old inode.
3. **Monitor the collector service — and restart it if it dies.** An MQ service
   has **no restart logic of its own.** `CONTROL(QMGR)` starts it when the queue
   manager starts, but if the collector process crashes — for example `amqsevt`
   meets an event it cannot parse and dies — **nothing restarts it; the feed stops
   silently.** The service is a moving part that must be health-monitored, alerted
   on, and restarted on failure: **a running queue manager does not imply its
   services are running.** The status of its important services is part of its
   health, and this collector is now one of them.
4. **Enable the events and thresholds on every queue that matters — and keep
   doing it.** The queue-manager `ALTER` in step 2 turns the event *classes* on,
   but performance events (queue full, depth high/low) fire only where a queue
   carries the thresholds. Every application queue **and every transmission queue**
   in use must have the appropriate per-queue attributes (`QDPMAXEV` / `QDPHIEV`
   with `QDEPTHHI`, etc.), and every queue added later must get them too — else
   those queues are silently invisible to the feed. This is a standing requirement,
   not a one-time step.

**What syslog would change.** Requirements (1) and (2), and the HA-failover
stranding window, exist because the sink is a **file**; a syslog sink inherits the
platform's existing forwarding and rotation and removes them — which is why syslog
is the simpler design and the standing recommendation. **Requirements (3) and (4)
remain either way:** the collector is still a service that can die and must be
monitored, and the per-queue thresholds are MQ-side configuration independent of
where the events go. The site chose a file; that choice adds (1), (2), and the
failover window on top of the unavoidable (3) and (4).

*(Volume, not a blocker: with every event class enabled on a busy queue manager the
feed can be high — start broad, then pare back the classes you act on.)*

---
---

# Reference (supplementary)

Rationale, requirements, and detail behind each Quick start step. Skip unless you
need the *why*.

## R1. Purpose and scope

A queue manager continuously emits **instrumentation events** — authorization
failures, channel start/stop, queue depth high/full, configuration changes,
administrative commands, queue-manager start/stop, TLS handshake failures — as
binary PCF messages on the `SYSTEM.ADMIN.*.EVENT` queues. Reading them historically
meant writing a PCF-parsing program.

IBM ships a sample, **`amqsevt`**, that reads those queues and formats each message,
including a structured **JSON** mode. Run it as a managed queue-manager service
whose `STDOUT` is a data file, and you have an event-driven JSON feed with no
parsing code — a thin translator, not an application.

**In scope:** installing the sample, enabling the event classes, and defining the
collector as a queue-manager `SERVICE` that appends JSON to a file. **Out of scope
(see Follow-on requirements):** forwarding that file, rotating it, and monitoring
the collector service. All are required for a working end-to-end pipeline; none is
solved here.

## R2. Installing the samples (Quick start step 1)

`amqsevt` is **not** part of the base MQ runtime; it ships in the **samples**
package and installs to `/opt/mqm/samp/bin/amqsevt`. On RHEL the package is
`MQSeriesSamples-*.rpm` (it depends on the MQ runtime already being present).

## R3. The collector service — no launcher needed (Quick start step 3)

The 2026-07-17 version used a one-line launcher script so that `amqsevt`'s output
was **appended** to the data file, on the belief that pointing the service's
`STDOUT` straight at the file would lose data because the service **truncates**
`STDOUT` on every restart. **Testing disproved that belief.** An MQ `SERVER`
service opens its `STDOUT` file with `O_APPEND` (verified via `/proc/<pid>/fdinfo`
open flags `0102001`) and appends across restarts (verified by content persistence
at a stable inode) — see `2026-07-20-mq-service-stdout-open-mode-evidence.md`.
Because the service appends, `amqsevt` can be the `STARTCMD` directly with `STDOUT`
pointed at the data file, and the launcher is unnecessary.

Two properties still matter and both hold in this simpler form:

- **The started process *is* `amqsevt`.** With `amqsevt` as `STARTCMD`,
  `+MQ_SERVER_PID+` is `amqsevt`'s own PID, so `STOPCMD('/bin/kill')` acts directly
  on the collector.
- **`STDOUT` appends.** MQ opens the `STDOUT` file `O_APPEND`, so restarts and
  Native-HA re-activations add to the file rather than truncating it. (The
  host-local nature of that file on failover is a *forwarding* concern — see the
  loss-window note — not a truncation one.)

`amqsevt` flushes its output per event (verified on 9.4.5), so each event reaches
the file promptly; there are no buffering concerns to handle.

## R4. Enabling event classes (Quick start step 2)

- **`CMDEV(NODISPLAY)`**, not `ENABLED` — captures the mutating admin commands
  (`ALTER`, `DEFINE`, `DELETE`, `STOP CHANNEL`, …) but excludes `DISPLAY`/inquire,
  so a polling metrics collector does not drown the signal.
- **`LOGGEREV` is omitted.** It is valid only on a **linear-logging** queue
  manager; on a circular-logging one MQ raises `AMQ8518E` and rejects the whole
  `ALTER QMGR`. Include it *only* where the queue manager uses linear logging.
- **`BRIDGEEV` is deliberately absent** — z/OS-only, rejected on a Multiplatforms
  queue manager. There is no `COMMEV` class.
- **Performance events need per-queue thresholds.** `PERFMEV(ENABLED)` emits
  nothing on its own; queue-depth events fire only where a queue carries thresholds
  (`QDPMAXEV` / `QDPHIEV` + `QDEPTHHI`).

Full class reference: **Appendix A**. Events worth alerting on first: **Appendix B**.

## R5. The collector service (Quick start step 3)

Run `amqsevt -o json` as a `SERVICE` with `CONTROL(QMGR)`, so the queue manager
starts and stops it and it runs wherever the queue manager is active.

- **`STARTARG('-m +QMNAME+ -o json')`** — MQ **word-splits** the space-separated
  argument string into separate arguments and expands `+QMNAME+` to the
  queue-manager name, so the running process is `amqsevt -m <QM> -o json`. The `+`
  delimiters are **required** — without them MQ passes the literal token.
- **`STDOUT('<event-log-path>')`** is the **event data** (append). **`STDERR`**
  (`<event-log-path>.err`) is a diagnostic catch — `amqsevt`'s stderr — so nothing
  is silently lost to `/dev/null`; it is normally empty and is **not** the data.
- **`STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')`** stops the exact `amqsevt`
  process the queue manager started.

**Ownership and location:** the collector runs as `mqm`, so both the data file and
the `.err` file must be writable by `mqm`, and the **data file** readable by the
agent.

**Verify (Quick start step 4):** `DISPLAY QMGR` confirms the classes read back as
set; provoke an event (start/stop a channel, or push a queue past `QDEPTHHI`) and
watch the relevant `SYSTEM.ADMIN.*.EVENT` queue depth rise then fall to zero as the
collector drains it (proof the drain is destructive, not browsing); confirm each
appended JSON object carries `eventSource` (`objectName`, `objectType`),
`eventType`, `eventReason`, `eventCreation`, and an `eventData` block.

---

## Appendix A — Event-class reference

Every queue-manager event-control attribute. All are `ENABLED`/`DISABLED` except
`CMDEV`, which also takes `NODISPLAY`.

| Attribute | Events | Platform |
|---|---|---|
| `AUTHOREV`  | Authorization (not-authorized) events | Multiplatforms |
| `CHADEV`    | Channel auto-definition events | Multiplatforms |
| `CHLEV`     | Channel start/stop/error events | All |
| `CMDEV`     | Command events (`ENABLED` / `NODISPLAY` / `DISABLED`) | All |
| `CONFIGEV`  | Configuration (object create/alter/delete) events | All |
| `INHIBTEV`  | Inhibit (get/put inhibited) events | All |
| `LOCALEV`   | Local error events (e.g. unknown object) | All |
| `LOGGEREV`  | Recovery-log events — **linear-logging QMs only** (rejected on circular; omit) | Multiplatforms |
| `PERFMEV`   | Performance events (queue depth, service interval) — **needs per-queue thresholds** | All |
| `REMOTEEV`  | Remote error events (remote-queue resolution) | All |
| `SSLEV`     | TLS (certificate / handshake) events | All |
| `STRSTPEV`  | Queue-manager start/stop events | All |
| `BRIDGEEV`  | IMS-bridge events | **z/OS only — exclude** |

Per-queue attributes for performance events: `QDPMAXEV` (queue full), `QDPHIEV` /
`QDPLOEV` with `QDEPTHHI` / `QDEPTHLO` (depth high/low), and `QSVCIEV` with
`QSVCINT` (service interval).

## Appendix B — Events worth alerting on first

A pragmatic shortlist to wire alerts on before the full firehose; filter on the
JSON `eventType` / `eventReason`.

| Priority | Event | Class | Why it matters |
|---|---|---|---|
| High | Queue full (put failed, `MAXDEPTH`) | `PERFMEV` (`QDPMAXEV`) | Messages are being rejected now — active outage. |
| High | Authorization failure (not authorized) | `AUTHOREV` | Failing app credentials, or access that should not happen. |
| High | Channel stopped / channel error | `CHLEV` | A connection to a partner is down — interface impact. |
| High | Queue-manager stop | `STRSTPEV` | The queue manager went away; on Native HA, expect a paired start on another node. |
| High | TLS / certificate error | `SSLEV` | Expiring or mismatched certs break secure channels. |
| Medium | Queue depth high | `PERFMEV` (`QDPHIEV`) | Early warning of a backlog before it reaches full. |
| Medium | Get/put inhibited | `INHIBTEV` | A queue was inhibited — often intentional-but-forgotten. |
| Low | Configuration change | `CONFIGEV` | An object was created/altered/deleted — audit trail. |
| Low | Command issued (mutating) | `CMDEV(NODISPLAY)` | Who changed what, during a live triage. |

*(Recovery-log events (`LOGGEREV`) are omitted here — the queue managers use
circular logging. On a linear-logging queue manager they are a useful Medium-
priority signal: log-space pressure precedes a hard stop.)*

## Appendix C — Evaluation notes: what was verified

This design was shaped and corrected by live testing on IBM MQ **9.4.5** (RHEL
9.6). What follows is the record of what was verified.

### Verified in the lab (observed behaviour)

- The 11-class enable (no `LOGGEREV`) applies cleanly on a circular-logging queue
  manager; the verbatim 12-class command with `LOGGEREV(ENABLED)` is rejected
  atomically (`AMQ8518E`), leaving all classes disabled.
- `amqsevt -o json` emits **one JSON object per event** and **flushes per event**,
  so events reach the file promptly on 9.4.5. Output is **pretty-printed
  multi-line** JSON (not line-delimited).
- MQ **word-splits** a space-separated `STARTARG` and expands `+QMNAME+`, so
  `amqsevt` can be driven directly as the service `STARTCMD` — no launcher.
- **An MQ `SERVER` service opens its `STDOUT` file `O_APPEND` and does not
  truncate on (re)start.** Proven two independent ways — the kernel open flags
  (`/proc/<pid>/fdinfo/1` = `0102001`, `O_APPEND` set) and content persistence at a
  stable inode across a stop/start. Full method and raw evidence:
  `2026-07-20-mq-service-stdout-open-mode-evidence.md`. **This corrects the
  2026-07-17 version, which claimed the opposite and built a launcher around it.**
- `STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')` stops the collector cleanly
  (with `amqsevt` as `STARTCMD`, `MQ_SERVER_PID` is `amqsevt` itself).

### Operational note

A second `amqsevt` cannot read the event queues while the first holds them
(`MQRC_OBJECT_IN_USE`, 2042). A too-fast `STOP`→`START` can transiently hit this
before the old reader releases the queue; let the stop complete before restarting.

## Appendix D — References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *ALTER QMGR*,
*ALTER queues*, *DEFINE SERVICE*, *Replaceable inserts on service definitions*,
*Event types*, and *Instrumentation events*.
