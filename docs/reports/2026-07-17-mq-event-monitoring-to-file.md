# IBM MQ instrumentation events to a file, as JSON

IBM MQ 9.4 for Multiplatforms (Linux / RHEL). Directs a queue manager's
instrumentation events to a **file** as JSON, via a managed queue-manager
service, for a file-monitoring agent to pick up. **The Quick start is the whole
setup; everything after it is reference.**

---

## Quick start

Five steps. Placeholders: `<QM>` = queue-manager name; `<launcher-path>` = where
you install the collector launcher (e.g. `/opt/mq-event-monitor/run.sh`);
`<event-log-path>` = the file your monitoring agent will watch (must be writable
by `mqm`, readable by the agent).

**1. Ensure the MQ samples are installed** — they provide `amqsevt` (it is *not*
in the base MQ runtime):

```bash
ls /opt/mqm/samp/bin/amqsevt || rpm -ivh MQSeriesSamples-9.4.*.rpm
```

**2. Install the collector launcher** at `<launcher-path>`, owned `mqm:mqm`,
mode `0755`. Its entire contents:

```bash
#!/bin/bash
exec stdbuf -oL /opt/mqm/samp/bin/amqsevt -m "$1" -o json
```

```bash
install -D -o mqm -g mqm -m 0755 run.sh <launcher-path>    # -D creates parent dirs
```

**3. Enable events on the queue manager** (`runmqsc <QM>`):

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) LOGGEREV(ENABLED) PERFMEV(ENABLED) +
  REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)

* Performance events also need per-queue thresholds — repeat per app queue:
ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
```

**4. Define and start the collector service** (`runmqsc <QM>`) — set
`<event-log-path>`:

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('<launcher-path>') STARTARG('+QMNAME+') +
  STDOUT('<event-log-path>') +
  STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON on a file')

START SERVICE(MQ.EVENT.MONITOR)
```

**5. Verify:**

```mqsc
DISPLAY SVSTATUS(MQ.EVENT.MONITOR)    * expect RUNNING with a PID
```

```bash
tail -f <event-log-path>              # one JSON object per event
```

Done. The queue manager now starts and stops the collector automatically, and the
JSON event feed appears in `<event-log-path>`.

> **One dependency this does not solve: file rotation.** The collector holds the
> file open for its lifetime and cannot reopen it, so rotation must be
> **copy-truncate** or must **bounce the service** — never a plain
> rename-and-recreate. See R6.

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
service with its output redirected to a file, and you have an event-driven JSON
feed with no parsing code — a thin translator, not an application.

**In scope:** installing the sample, enabling the event classes, and defining the
collector as a queue-manager `SERVICE` whose standard output is redirected to a
file. **Out of scope (see R6):** rotating that file, shipping its contents
downstream, and configuring the agent that watches it.

## R2. Installing the samples (Quick start step 1)

`amqsevt` is **not** part of the base MQ runtime; it ships in the **samples**
package and installs to `/opt/mqm/samp/bin/amqsevt`. On RHEL the package is
`MQSeriesSamples-*.rpm` (it depends on the MQ runtime already being present).

## R3. The collector launcher (Quick start step 2)

The launcher named by `STARTCMD` exists only to `exec` the collector
line-buffered; the `SERVICE` object's `STDOUT` attribute does the file
redirection. `$1` is the queue-manager name passed by `STARTARG('+QMNAME+')`. Two
properties are why it exists rather than naming `amqsevt` directly in `STARTCMD`:

- **`exec`** replaces the launcher shell so the process the queue manager tracks
  (`MQ_SERVER_PID`) *is* `amqsevt`. Without it, `STOPCMD` would kill the shell and
  orphan the collector.
- **Line-buffering** (`stdbuf -oL`) — output to a regular file is block-buffered
  by default, so under low event volume a JSON line could sit in the buffer for a
  long time before it reaches the file. Line-buffering makes each event land
  promptly, which matters if you alert on these events.

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

Run `amqsevt -o json` as a `SERVICE` with `CONTROL(QMGR)`, so the queue manager
starts and stops it and it runs wherever the queue manager is active. The service
object's `STDOUT` attribute owns the redirection.

- **`STDOUT('<event-log-path>')`** — the file your agent watches. `STDERR` can
  point at a companion `.err` file for diagnostics.
- **`+QMNAME+` / `+MQ_SERVER_PID+`** are replaceable inserts; MQ substitutes the
  queue-manager name and the started PID at run time. The `+` delimiters are
  required — without them MQ passes the literal token.
- **`STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')`** stops the exact process
  the queue manager started; clean because the launcher `exec`s `amqsevt` (R3).

**Output file — ownership and location:** the collector runs as `mqm`, so
`<event-log-path>` must be writable by `mqm` and readable by the agent. On RHEL
with SELinux enforcing, a path outside the MQ data tree may need an appropriate
file context; placing the file where the agent already has a labelled, watched
location avoids that — confirm with whoever owns the agent.

**Verify (Quick start step 5):** `DISPLAY QMGR` confirms the classes read back as
set; provoke an event (start/stop a channel, or push a queue past `QDEPTHHI`) and
watch the relevant `SYSTEM.ADMIN.*.EVENT` queue depth rise then fall to zero as
the collector drains it (proof the drain is destructive, not browsing); confirm
each JSON object carries `eventSource` (`objectName`, `objectType`), `eventType`,
`eventReason`, `eventCreation`, and an `eventData` block.

## R6. Acknowledged dependencies (not solved here)

1. **File rotation — with a specific constraint.** The collector holds the output
   file **open for its entire lifetime**, and `amqsevt` has **no
   reopen-on-signal**. So rotation **cannot rename-and-recreate** the file — that
   strands the collector writing to the old (now-invisible) inode while the new
   file stays empty. Rotation must be **copy-truncate style** (copy aside, then
   truncate in place, which the open handle keeps writing to) **or** must
   **bounce the service** (`STOP SERVICE` / `START SERVICE`) so the collector
   reopens the file. (MQ appends to `STDOUT` across restarts, so a bounce
   preserves prior content — confirm on your build if a rotate-by-bounce scheme
   relies on it.)
2. **Shipping downstream.** The file-monitoring agent tails the file and forwards
   its contents; its watch list and everything past it are the agent's concern.
3. **Event volume.** With every class enabled on a busy queue manager the feed can
   be high-volume. Start broad, then pare back the classes you actually act on.

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

## Appendix C — References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *ALTER QMGR*,
*ALTER queues*, *DEFINE SERVICE*, *Replaceable inserts on service definitions*,
*Event types*, and *Instrumentation events*.
