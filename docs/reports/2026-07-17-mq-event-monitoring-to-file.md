# IBM MQ instrumentation events to a file, as JSON

IBM MQ 9.4 for Multiplatforms (Linux / RHEL). Directs a queue manager's
instrumentation events to a **file** as JSON, via a managed queue-manager
service, for a file-monitoring agent to pick up. **The Quick start is the setup; the
Follow-on requirements after it are mandatory before production; the rest is
reference.**

**Validation status:** the mechanism below was exercised on a live IBM MQ
**9.4.5** queue manager (RHEL 9.6, 3-node Native HA) in a resiliency lab.
**Appendix C** records exactly what was verified, the dead ends found, and the
standing trade-off. Note up front: a file sink is
**not** a self-contained solution — it needs external forwarding, rotation, *and*
health-monitoring of the collector service (see **Follow-on requirements**). A
syslog sink would remove the first two; the third remains either way. The site has
chosen a file; this document makes that work and states the cost.

---

## Quick start

Five steps. Placeholders: `<QM>` = queue-manager name; `<launcher-path>` = the
full path where you install the launcher script (`mq-event-monitor.sh`);
`<event-log-path>` = the **data file** the launcher appends to and your agent
watches (writable by `mqm`, readable by the agent). The service also writes
diagnostics to `<event-log-path>.svc` / `.svc.err` — a catch-all, not the data.

**1. Ensure the MQ samples are installed** — they provide `amqsevt` (it is *not*
in the base MQ runtime):

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm
```

**2. Install the collector launcher** (`mq-event-monitor.sh`) — a one-line script.
Its entire contents — it **appends** the JSON to the data file:

```bash
#!/bin/bash
# Append (>>) the JSON event stream to the data file. Append — NOT the service's
# STDOUT, which truncates on every restart (see Appendix C). exec => the process
# the queue manager tracks (MQ_SERVER_PID) is amqsevt, so STOPCMD stops it cleanly.
exec /opt/mqm/samp/bin/amqsevt -m "$1" -o json >> <event-log-path>
```

Install it wherever your site keeps such scripts, owned `mqm:mqm`, mode `0755`
(the collector runs as `mqm`):

```bash
install -D -o mqm -g mqm -m 0755 mq-event-monitor.sh <launcher-path>   # -D creates parent dirs
```

**3. Enable events on the queue manager** (`runmqsc <QM>`):

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) LOGGEREV(ENABLED) PERFMEV(ENABLED) +
  REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

Performance events also need per-queue thresholds — set these on each application
(and transmission) queue that matters:

```mqsc
ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
```

**4. Define and start the collector service** (`runmqsc <QM>`) — set
`<launcher-path>` and `<event-log-path>`:

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('<launcher-path>') STARTARG('+QMNAME+') +
  STDOUT('<event-log-path>.svc') STDERR('<event-log-path>.svc.err') +
  STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') +
  DESCR('Append SYSTEM.ADMIN.*.EVENT as JSON to the event data file')

START SERVICE(MQ.EVENT.MONITOR)
```

The `STDOUT`/`STDERR` files are a **diagnostic catch** (so stray output or
`amqsevt` errors are not lost to `/dev/null`) — the event **data** is the
launcher's append target, `<event-log-path>`.

**5. Verify:**

```mqsc
DISPLAY SVSTATUS(MQ.EVENT.MONITOR)    * expect RUNNING with a PID
```

```bash
tail -f <event-log-path>              # one JSON object per event
```

Done. The queue manager starts and stops the collector automatically, and the
JSON event feed is appended to `<event-log-path>`.

---

## Follow-on requirements — not production-ready until these are owned

This document produces the JSON event feed; a working end-to-end pipeline needs
four more things, owned outside it. `amqsevt` drains the event queues
**destructively** — once an event is written to the file it exists **nowhere else
in MQ** — which is what makes the file-handling requirements below load-bearing
rather than optional.

1. **Monitor the file — and checkpoint its read position.** Something must tail
   the data file and forward it continuously, and it must **persist its read
   offset**. If that monitor itself crashes and restarts without knowing where it
   had reached, it will either **re-read the whole file and re-send everything** —
   duplicate events, some possibly very old — or seek to the end and **drop**
   everything written while it was down. So the monitor's own crash/restart
   behaviour is part of this requirement, not an afterthought. And if nothing
   monitors the file, or it falls behind, the backlog sitting in the file is the
   only copy that exists.
2. **Rotate the file.** The launcher appends forever, so the data file grows
   without bound and must be rotated externally. The collector holds it open with
   **no reopen-on-signal**, so rotation must be **copy-truncate** (copy aside, then
   truncate in place, which the open handle keeps writing to) — **never**
   rename-and-recreate, which strands the collector on the old inode. Size the
   cadence so the small copy-truncate window (the one residual loss window) is
   acceptable.
3. **Monitor the collector service — and restart it if it dies.** An MQ service
   has **no restart logic of its own.** `CONTROL(QMGR)` starts it when the queue
   manager starts, but if the collector process crashes — for example `amqsevt`
   meets an event it cannot parse and core-dumps — **nothing restarts it; it
   simply dies, and the feed stops silently.** The service is therefore a moving
   part that must be health-monitored, alerted on, and restarted on failure. This
   is not specific to this collector: **a running queue manager does not imply its
   services are running.** Monitoring a queue manager is more than "is the listener
   port up" — the status of its important services is part of its health, and this
   collector is now one of them.
4. **Enable the events and thresholds on every queue that matters — and keep
   doing it.** The queue-manager `ALTER` in step 3 turns the event *classes* on,
   but performance events (queue full, depth high/low) fire only where a queue
   carries the thresholds. Every application queue **and every transmission
   queue** in use must have the appropriate per-queue attributes set (`QDPMAXEV` /
   `QDPHIEV` with `QDEPTHHI`, etc.), and every queue added later must get them too
   — otherwise those queues are silently invisible to the feed. This per-queue
   tuning is a standing requirement, not a one-time step.

**What syslog would and would not change.** Requirements (1) and (2) exist only
because the sink is a file; a syslog sink inherits the platform's existing
forwarding and rotation and removes both — which is why syslog is the simpler
design and the standing recommendation. **Requirements (3) and (4) remain either
way:** the collector is still a service that can die and must be monitored, and
the per-queue thresholds are MQ-side configuration independent of where the events
go. The site chose a file; that choice adds (1) and (2) on top of the unavoidable
(3) and (4).

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
binary PCF messages on the `SYSTEM.ADMIN.*.EVENT` queues. Reading them
historically meant writing a PCF-parsing program.

IBM ships a sample, **`amqsevt`**, that reads those queues and formats each
message, including a structured **JSON** mode. Run it as a managed queue-manager
service whose launcher **appends** each event to a file, and you have an
event-driven JSON feed with no parsing code — a thin translator, not an
application.

**In scope:** installing the sample, enabling the event classes, and defining the
collector as a queue-manager `SERVICE` whose launcher appends JSON to a file.
**Out of scope (see Follow-on requirements):** forwarding that file, rotating it,
and monitoring the collector service. All are required for a working end-to-end
pipeline; none is solved here.

## R2. Installing the samples (Quick start step 1)

`amqsevt` is **not** part of the base MQ runtime; it ships in the **samples**
package and installs to `/opt/mqm/samp/bin/amqsevt`. On RHEL the package is
`MQSeriesSamples-*.rpm` (it depends on the MQ runtime already being present).

## R3. The collector launcher (Quick start step 2)

The launcher named by `STARTCMD` is a one-line script. `$1` is the queue-manager
name from `STARTARG('+QMNAME+')`. Two properties matter:

- **`exec`** replaces the launcher shell so the process the queue manager tracks
  (`MQ_SERVER_PID`) *is* `amqsevt` — which is what makes the `STOPCMD` below act
  directly on the collector.
- **Append (`>>`)** writes each event to the data file in append mode. This is
  deliberately **not** the service's `STDOUT`: testing showed the service
  **truncates** its `STDOUT` file on every (re)start (Appendix C), which would
  discard any events written but not yet forwarded. Appending in the launcher
  avoids that.

`amqsevt` flushes its output per event (verified on 9.4.5 — Appendix C), so each
event reaches the file promptly; there are no buffering concerns to handle.

## R4. Enabling event classes (Quick start step 3)

- **`CMDEV(NODISPLAY)`**, not `ENABLED` — captures the mutating admin commands
  (`ALTER`, `DEFINE`, `DELETE`, `STOP CHANNEL`, …) but excludes `DISPLAY`/inquire,
  so a polling metrics collector does not drown the signal.
- **`BRIDGEEV` is deliberately absent** — z/OS-only, rejected on a Multiplatforms
  queue manager. There is no `COMMEV` class.
- **Performance events need per-queue thresholds.** `PERFMEV(ENABLED)` emits
  nothing on its own; queue-depth events fire only where a queue carries
  thresholds (`QDPMAXEV` / `QDPHIEV` + `QDEPTHHI`).

Full class reference: **Appendix A**. Events worth alerting on first:
**Appendix B**.

## R5. The collector service (Quick start step 4)

Run `amqsevt -o json` (via the launcher) as a `SERVICE` with `CONTROL(QMGR)`, so
the queue manager starts and stops it and it runs wherever the queue manager is
active.

- **`STDOUT`/`STDERR` are diagnostic only.** The launcher sends the event data to
  `<event-log-path>` (append), so these files are a catch-all — `amqsevt`'s stderr
  lands in `.svc.err`; the `.svc` file is normally empty. They exist so nothing is
  silently lost to `/dev/null`; they are **not** the data.
- **`+QMNAME+` / `+MQ_SERVER_PID+`** are replaceable inserts; MQ substitutes the
  queue-manager name and the started PID at run time. The `+` delimiters are
  **required** — without them MQ passes the literal token, not the value.
- **`STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')`** stops the exact process
  the queue manager started; clean because the launcher `exec`s `amqsevt` (R3).

**Ownership and location:** the collector runs as `mqm`, so both the data file and
the `.svc`/`.svc.err` files must be writable by `mqm`, and the **data file**
readable by the agent. On RHEL with SELinux enforcing, a path outside the MQ data
tree may need an appropriate file context; placing the files where the agent
already has a labelled, watched location avoids that — confirm with whoever owns
the agent.

**Verify (Quick start step 5):** `DISPLAY QMGR` confirms the classes read back as
set; provoke an event (start/stop a channel, or push a queue past `QDEPTHHI`) and
watch the relevant `SYSTEM.ADMIN.*.EVENT` queue depth rise then fall to zero as
the collector drains it (proof the drain is destructive, not browsing); confirm
each appended JSON object carries `eventSource` (`objectName`, `objectType`),
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
| `LOGGEREV`  | Recovery-log events | Multiplatforms |
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
| Medium | Recovery-log events | `LOGGEREV` | Log-space pressure precedes a hard stop. |
| Medium | Get/put inhibited | `INHIBTEV` | A queue was inhibited — often intentional-but-forgotten. |
| Low | Configuration change | `CONFIGEV` | An object was created/altered/deleted — audit trail. |
| Low | Command issued (mutating) | `CMDEV(NODISPLAY)` | Who changed what, during a live triage. |

## Appendix C — Evaluation notes: dead ends and the standing trade-off

This design was shaped by live testing on IBM MQ **9.4.5** (RHEL 9.6, 3-node
Native HA). What follows is the record of what we verified, what we rejected and
why, and the trade-off the file approach leaves standing. Treat the boxed items as
warnings.

### Verified in the lab (observed behaviour)

- `amqsevt -o json` emits **one JSON object per event** and **flushes per event**,
  so events reach the file promptly — no buffering to handle on 9.4.5.
- MQ **word-splits** a space-separated `STARTARG` into separate arguments and
  expands `+QMNAME+`, so `amqsevt` can be driven directly from a service.
- `STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')` stops the collector cleanly.
- The service **`STDOUT` file is truncated on every (re)start** — it is *not*
  appended to.

### Rests on standard behaviour (not separately re-run)

- The launcher's `>>` opens the data file `O_APPEND`, so a restart **appends**
  rather than truncates. This is standard shell/OS behaviour, not MQ-specific — it
  is why the launcher, rather than the service `STDOUT`, owns the data file.

### Dead end — letting the service own the data file (the truncation trap)

> The tidiest-looking design is **no launcher at all**: point the service's
> `STDOUT` straight at the data file. **Rejected.** The service **truncates**
> `STDOUT` on every restart and every Native-HA failover, so any events written
> but not yet forwarded at that instant are **lost — silently and unrecoverably**,
> because the drain is destructive (no second copy exists). Worse, the events most
> at risk are exactly the pre-incident ones — queue-full, channel errors, the
> queue-manager-stop event itself — that you most want during an incident. The
> launcher's append (`>>`) closes this hole; that one line of shell is the reason
> the launcher exists.

### The standing trade-off — read this

Append closes the truncation hole, but it does **not** make the file approach
self-contained — it leaves the three **Follow-on requirements** the site must own:
forward the file, rotate it, and monitor/restart the collector service.

The first two exist only because the sink is a file: **a syslog sink removes both**
by reusing the platform's existing forwarding and rotation, which is why syslog is
the simpler design and the standing recommendation. The third — health-monitoring
the collector service — **remains either way**, because an MQ service does not
restart itself. The site has chosen a file; this document makes that choice work
and states its cost so the decision is made with eyes open.

## Appendix D — References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *ALTER QMGR*,
*ALTER queues*, *DEFINE SERVICE*, *Replaceable inserts on service definitions*,
*Event types*, and *Instrumentation events*.
