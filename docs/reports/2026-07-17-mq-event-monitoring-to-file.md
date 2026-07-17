# IBM MQ instrumentation events to a file, as JSON — setup how-to

- **Product:** IBM MQ 9.4 for Multiplatforms (Linux / RHEL)
- **Audience:** MQ administrators standing this up on a Native HA queue manager
- **Goal:** produce a JSON event feed **into a file** on the queue-manager host, for
  the site's file-monitoring agent to pick up and forward downstream.
- **Status:** Draft for review

---

## 1. Purpose and scope

A queue manager continuously emits **instrumentation events** — authorization
failures, channel start/stop, queue depth high/full, configuration changes,
administrative commands, queue-manager start/stop, TLS handshake failures, and
more — as binary PCF messages on the `SYSTEM.ADMIN.*.EVENT` queues. Reading them
historically meant writing a PCF-parsing program.

IBM ships a sample, **`amqsevt`**, that reads those queues and formats each
message, including a structured **JSON** mode. Run it as a managed queue-manager
service with its output redirected to a file, and you have an event-driven JSON
feed with no parsing code — a thin translator, not an application.

**In scope:** installing the sample, enabling the event classes, and defining the
collector as a queue-manager `SERVICE` whose standard output is redirected to a
file.

**Out of scope (explicit dependencies — see §6):** rotating that file, shipping
its contents downstream, and configuring the file-monitoring agent that watches
it. This how-to gets clean JSON into a file; everything past the file is owned
elsewhere.

## 2. Prerequisite — install the MQ samples

`amqsevt` is **not** part of the base MQ runtime. It ships in the **samples**
package and installs to `/opt/mqm/samp/bin/amqsevt`.

On RHEL, install the samples RPM (it depends on the MQ runtime already being
present):

```bash
# from the MQ install media / package directory
rpm -ivh MQSeriesSamples-9.4.*.rpm      # or: dnf install ./MQSeriesSamples-9.4.*.rpm

# verify the binary is present
ls -l /opt/mqm/samp/bin/amqsevt
```

## 3. Enable the event classes on the queue manager

Events are off until enabled. Turn on the classes you want with `ALTER QMGR`.
Starting broad and paring back once volume is understood is reasonable:

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) LOGGEREV(ENABLED) PERFMEV(ENABLED) +
  REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

Notes:

- **`CMDEV(NODISPLAY)`**, not `ENABLED` — this captures the mutating admin
  commands (`ALTER`, `DEFINE`, `DELETE`, `STOP CHANNEL`, …) but excludes
  `DISPLAY`/inquire, so a polling metrics collector does not drown the signal.
- **`BRIDGEEV` is deliberately absent** — it is z/OS-only and is rejected on a
  Multiplatforms queue manager. There is no `COMMEV` class.
- **Performance events need per-queue thresholds.** `PERFMEV(ENABLED)` emits
  nothing on its own; queue-depth events fire only where a queue carries
  thresholds. Set them on the application queues that matter:

  ```mqsc
  ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
  ```

The full class reference is in **Appendix A**; the events worth alerting on first
are in **Appendix B**.

## 4. Define the collector as a queue-manager service

Run `amqsevt -o json` as a `SERVICE` object with `CONTROL(QMGR)`, so the queue
manager starts and stops it and it runs wherever the queue manager is active. Let
the **service object itself own the output redirection** via its `STDOUT`
attribute — MQ writes the process's standard output to that file, so nothing in
the launcher has to do the redirect.

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/opt/mq-event-monitor/run.sh') STARTARG('+QMNAME+') +
  STDOUT('<event-log-path>') +
  STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON on a file')
```

Then start it (the queue manager auto-starts it on every subsequent start, but it
is already running now, so start it once by hand):

```mqsc
START SERVICE(MQ.EVENT.MONITOR)
```

Four things make this definition correct:

1. **`STDOUT('<event-log-path>')` names the output file** — replace
   `<event-log-path>` with the path your file-monitoring agent will watch. The
   `STDERR` attribute can point at a companion `.err` file for diagnostics.
2. **`+QMNAME+` and `+MQ_SERVER_PID+` are replaceable inserts** — MQ substitutes
   the queue-manager name and the started process's PID at run time. The `+`
   delimiters are required; without them MQ passes the literal token.
3. **`STOPCMD('/bin/kill') STOPARG('+MQ_SERVER_PID+')`** stops the exact process
   the queue manager started, so shutdown is clean. This only works if that
   process **is** `amqsevt` — see the launcher below.
4. **The launcher `exec`s the collector, line-buffered.** Two properties matter,
   which is the only reason a one-line launcher exists rather than naming
   `amqsevt` directly in `STARTCMD`:
   - **`exec`** replaces the launcher shell so the process the queue manager
     tracks (`MQ_SERVER_PID`) *is* `amqsevt`. Without it, `STOPCMD` would kill the
     shell and orphan the collector.
   - **Line-buffering** (`stdbuf -oL`) — output to a regular file is
     block-buffered by default, so under low event volume a JSON line could sit in
     the buffer for a long time before it reaches the file. Line-buffering makes
     each event land promptly, which matters if you alert on these events.

The launcher is a single `exec` line; it is described in **Appendix C**.

### Output file — ownership and location

- The collector runs as the queue manager's service user (`mqm`), so
  `<event-log-path>` must be **writable by `mqm`**.
- The file-monitoring agent must be able to **read** it.
- On RHEL with SELinux enforcing, a path **outside** the MQ data tree may need an
  appropriate file context (and the agent needs read access under its own
  policy). Placing the file where the agent already has a labelled, watched
  location avoids that; confirm with whoever owns the agent.

## 5. Verify it worked

- **Classes are enabled.** `DISPLAY QMGR` and confirm the attributes read back as
  set — in particular `CMDEV(NODISPLAY)` and `PERFMEV(ENABLED)`.
- **The service is running.** `DISPLAY SVSTATUS(MQ.EVENT.MONITOR)` shows it
  `RUNNING` with a PID.
- **Events flow and drain.** Provoke one (start/stop a channel, or push a queue
  past its `QDEPTHHI`) and watch the relevant `SYSTEM.ADMIN.*.EVENT` queue depth
  rise, then fall back toward zero as the collector drains it — proof the drain is
  destructive, not merely browsing.
- **The file is JSON.** Confirm `<event-log-path>` receives one JSON object per
  event, each carrying `eventSource` (with `objectName` and `objectType`),
  `eventType`, `eventReason`, `eventCreation`, and an `eventData` block. Those are
  the fields the downstream tool filters on.

## 6. Acknowledged dependencies (not solved here)

This how-to deliberately stops at "clean JSON in a file." Three things must be
owned elsewhere; naming them is the point.

1. **File rotation — and a specific constraint on how.** The collector holds the
   output file **open for its entire lifetime**, and `amqsevt` has **no
   reopen-on-signal** behaviour. Therefore whoever rotates this file **cannot
   simply rename-and-recreate it** — that would strand the collector writing to
   the old (now-invisible) inode while the new file stays empty and the monitoring
   agent sees nothing. Rotation must instead be **copy-truncate style** (copy the
   file aside, then truncate the original in place, which the open handle keeps
   writing to), **or** it must **bounce the service**
   (`STOP SERVICE` / `START SERVICE`) around the rotation so the collector reopens
   the file. (MQ appends to `STDOUT` across restarts, so a bounce preserves prior
   content — worth confirming on your build if a rotate-by-bounce scheme relies on
   it.)
2. **Shipping downstream.** The site's file-monitoring agent tails the file and
   forwards its contents; adding this file to its watch list, and everything past
   that, is the agent's concern.
3. **Event volume.** With every class enabled on a busy queue manager the feed can
   be high-volume. Start broad to see the range, then pare back in §3 to the
   classes you actually act on.

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

A pragmatic shortlist to wire alerts on before the full firehose. Each is a
signal an operator would want to know about promptly; filter on the JSON
`eventType` / `eventReason`.

| Priority | Event | Class | Why it matters |
|---|---|---|---|
| High | Queue full (put failed, `MAXDEPTH`) | `PERFMEV` (`QDPMAXEV`) | Messages are being rejected now — active outage. |
| High | Authorization failure (not authorized) | `AUTHOREV` | Failing app credentials, or an access attempt that should not happen. |
| High | Channel stopped / channel error | `CHLEV` | A connection to a partner is down — interface impact. |
| High | Queue-manager stop | `STRSTPEV` | The queue manager went away; on Native HA, expect a paired start on another node. |
| High | TLS / certificate error | `SSLEV` | Expiring or mismatched certs break secure channels. |
| Medium | Queue depth high | `PERFMEV` (`QDPHIEV`) | Early warning of a backlog before it reaches full. |
| Medium | Recovery-log events | `LOGGEREV` | Log-space pressure precedes a hard stop. |
| Medium | Get/put inhibited | `INHIBTEV` | A queue was inhibited — often an intentional-but-forgotten change. |
| Low | Configuration change | `CONFIGEV` | An object was created/altered/deleted — useful audit trail. |
| Low | Command issued (mutating) | `CMDEV(NODISPLAY)` | Who changed what, during a live triage. |

## Appendix C — The launcher (strip or transcribe before sending as needed)

> This appendix contains a shell snippet. If it should not be sent as text, it can
> be dropped — the definition it supports is fully described in §4, and the script
> is a single line an administrator can retype by hand.

The launcher named by `STARTCMD` (`/opt/mq-event-monitor/run.sh`) exists only to
`exec` the collector line-buffered; the `SERVICE` object's `STDOUT` attribute does
the file redirection. `$1` is the queue-manager name passed by `STARTARG('+QMNAME+')`.

```bash
#!/bin/bash
# Runs amqsevt as the MQ.EVENT.MONITOR service. exec => the process the queue
# manager tracks (MQ_SERVER_PID) is amqsevt, so STOPCMD stops it cleanly.
# stdbuf -oL => line-buffered, so each JSON event reaches the file promptly.
exec stdbuf -oL /opt/mqm/samp/bin/amqsevt -m "$1" -o json
```

Make it executable and owned so the service user can run it:

```bash
install -o mqm -g mqm -m 0755 run.sh /opt/mq-event-monitor/run.sh
```

## Appendix D — References

In the IBM MQ 9.4 documentation (<https://www.ibm.com/docs/en/ibm-mq/9.4>), see:
*Sample program to monitor instrumentation events (amqsevt)*, *ALTER QMGR*,
*ALTER queues*, *DEFINE SERVICE*, *Replaceable inserts on service definitions*,
*Event types*, and *Instrumentation events*.
