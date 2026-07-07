# IBM MQ instrumentation event monitoring — events as JSON via `amqsevt`

- **MQ version:** 9.4 (`amqsevt` with JSON output on MQ for Multiplatforms)
- **Status:** Draft
- **Last validated in lab:** pending (awaiting a cold-rebuild verification)
- **Related guides:** [JSON diagnostic logging](mq-json-logging-guide.md) — the
  complementary *log* stream (why something happened) to this *event* stream
  (what the queue manager did)

---

## 1. Purpose & audience

A queue manager continuously emits **instrumentation events** — authorization
failures, channel start/stop, queue depth high/full, configuration changes,
commands issued, queue-manager start/stop, and more — as **binary PCF messages**
on the `SYSTEM.ADMIN.*.EVENT` queues. Historically, reading them meant writing
the program that parsed PCF.

This guide removes that entirely. IBM ships a sample, **`amqsevt`**, that reads
the event queues and formats each message — including a structured **JSON** mode.
Enable the events you care about, run `amqsevt -o json` as a managed collector,
and you have an **event-driven JSON feed with no parsing code** — a thin
translator, not an application. It is for anyone who wants MQ's own account of
what a queue manager is doing as queryable JSON, alongside (not instead of) the
diagnostic logs.

## 2. Scope & version floor

In scope: the MQ-side configuration that **produces** the JSON event feed —
enabling event classes on the queue manager (`ALTER QMGR`), the per-queue
thresholds that make performance events fire, and running `amqsevt -o json` as a
managed collector (an MQ **service object**).

Out of scope: **shipping, storing, or visualizing** the JSON — that is your
pipeline (a log collector, a store, a dashboard), the same boundary the
[JSON logging guide](mq-json-logging-guide.md) draws. This guide gets clean JSON
onto your host's log stream; where it goes next is yours.

Version floor: instrumentation events are core to MQ; the `amqsevt` sample and
its `-o json` output are documented for **MQ for Multiplatforms**. Content is
pinned to **9.4**. Enabling event classes takes effect immediately; the collector
is a normal queue-manager service.

## 3. Recommendation

Four decisions define a production-shaped event feed. Each is ranked with its
compromise stated; your constraints may differ.

1. **Use `amqsevt`, not a bespoke PCF reader.** The sample ships prebuilt in the
   MQ samples (`amqsevt`, with source `amqsevta.c`) and is included in the
   redistributable client. It reads the event queues and emits JSON directly.
   *Compromise:* it is a sample — you own its lifecycle as you deploy it — but it
   removes all PCF-parsing code, which is the whole point.
2. **Run it as an MQ service object (`CONTROL(QMGR)`), not an external client.**
   A `SERVICE` with `CONTROL(QMGR)` starts and stops with the queue manager and
   runs in bindings mode, so under HA it **travels with the queue manager** to
   wherever it is active — no floating-IP client connection, no "which instance
   is live" logic. *Compromise:* one collector per queue manager, local to it;
   a fleet-wide external collector is the alternative if you prefer central
   deployment over travelling-with-the-QM (Appendix B).
3. **Drain destructively (the default), not browse (`-b`).** The collector *is*
   the event consumer: a normal destructive get removes each event as it is read,
   so the event queues never fill to `MAXDEPTH`. *Compromise:* a single consumer.
   If more than one tool must read the same events, route events to a **topic**
   (`amqsevt -t`) and fan out — do not browse, which leaves events on the queue
   until it fills and MQ silently discards new ones.
4. **`CMDEV(NODISPLAY)`, not `CMDEV(ENABLED)`.** Command events let you watch
   administrative commands as they are issued — genuinely useful during a live
   triage. `NODISPLAY` captures the mutating commands (`ALTER`, `DEFINE`,
   `DELETE`, `STOP CHANNEL`, …) but excludes `DISPLAY`/inquire, so a polling
   metrics collector does not drown the signal. *Compromise:* you lose visibility
   of who is *reading* configuration; that is rarely what you want to watch.

## 4. How to configure it

**Step 1 — enable the event classes on the queue manager.** Turn on the classes
you want with `ALTER QMGR`. To start broad and pare back once you know the
volume, enable the full Multiplatforms set (Appendix A lists every class):

```mqsc
ALTER QMGR AUTHOREV(ENABLED) CHADEV(ENABLED) CHLEV(ENABLED) CONFIGEV(ENABLED) +
  INHIBTEV(ENABLED) LOCALEV(ENABLED) LOGGEREV(ENABLED) PERFMEV(ENABLED) +
  REMOTEEV(ENABLED) SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

`BRIDGEEV` is deliberately absent — it is z/OS-only (Appendix A). There is no
`COMMEV` class.

**Step 2 — set the per-queue thresholds performance events need.** `PERFMEV` at
the queue-manager level emits nothing on its own; queue-depth events fire only
where a queue carries thresholds. Set them on the application queues that matter:

```mqsc
ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
```

**Step 3 — define the collector as a queue-manager service.** Define a `SERVICE`
that runs `amqsevt -o json` against this queue manager. With no `-q`, `amqsevt`
reads the standard `SYSTEM.ADMIN.*.EVENT` set; `-m` names the queue manager;
`-o json` selects JSON. Point `STARTCMD` at a small launcher that runs `amqsevt`
and forwards its standard output to your log pipeline, and make `STOPCMD` stop
the process the queue manager started:

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/path/to/your/launcher') STARTARG('<qmgr-name>') +
  STOPCMD('/bin/kill') STOPARG('MQ_SERVER_PID') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON')
```

The launcher itself is environment-specific and lives outside this guide (it is a
few lines: run `amqsevt -m "$1" -o json` and send its output to wherever your
host collects logs — journald, syslog, a file tailer). Two properties matter, and
are why the launcher exists rather than putting the command inline:

- **`exec` the collector** so the process the queue manager tracks
  (`MQ_SERVER_PID`) *is* `amqsevt`. Then `STOPCMD` stops the collector cleanly on
  queue-manager shutdown, instead of leaving it orphaned.
- **Keep the output line-buffered** so each JSON event reaches your pipeline
  promptly rather than sitting in a block buffer.

## 5. Verify it worked

- **The classes are enabled.** Display the queue manager and confirm the event
  attributes read as you set them — in particular `CMDEV(NODISPLAY)` and
  `PERFMEV(ENABLED)`.
- **Events are being written.** Provoke one (start or stop a channel, or push a
  queue past its `QDEPTHHI`) and confirm the depth of the relevant
  `SYSTEM.ADMIN.*.EVENT` queue moves — then returns to zero as the collector
  drains it (proof the drain is destructive, not browsing).
- **The feed is JSON.** Confirm the collector's output is one JSON object per
  event, each carrying `eventSource` (with `objectName` — the affected queue,
  channel, or queue manager — and `objectType`), `eventType`, `eventReason`,
  `eventCreation`, and an `eventData` block (which includes `queueMgrName`). Those
  are the fields you filter on downstream.
- **The service travels.** On an HA queue manager, fail it over and confirm the
  collector is running again on the newly active node. Behaviour varies by HA
  mechanism — a Native HA takeover is not a `strmqm`, so start/stop events may
  differ from a cold start; observe your own mechanism rather than assuming.

## 6. What stays / caveats

- **Performance events need per-queue thresholds** (Step 2). Enabling `PERFMEV`
  alone is silent.
- **`BRIDGEEV` is z/OS-only.** Issuing it on a Multiplatforms queue manager is
  rejected — do not include it (Appendix A).
- **Destructive drain is single-consumer by design.** The event queues have one
  reader. If you need more, fan out through a topic; do not add a second
  destructive consumer, and do not switch to browse (Appendix B).
- **Browsing fills the queue.** `-b` never removes events, so the queue climbs to
  `MAXDEPTH` and MQ then discards new events silently. Use it only for a
  short-lived look, never as the standing collector.
- **Event volume can be high** with every class enabled and a busy queue manager.
  Start broad to see the range, then pare back to the classes you act on. If your
  host log layer rate-limits (e.g. journald defaults), raise or disable the limit
  for the event source so bursts are not silently dropped.

!!! note "How this lab implements it"
    This lab wires the feed end to end: an Ansible role deploys the `SERVICE`
    (its launcher forwards `amqsevt` JSON to **journald**), Grafana **Alloy**
    ships the journal to **Loki** under a distinct `unit="mq-events"` label
    (separate from the diagnostic-log stream), and the Grafana boards carry a
    per-object events panel — the queue-manager board shows every event for the
    queue manager, and each queue and channel shows the events for that specific
    object, filtered on `eventSource.objectName`. The end-to-end design and the
    lab-specific wiring are recorded in the event-monitoring epic spec
    (`logical-minds-foundry/.github`, `epics/31-event-monitoring/spec.md`).

## Appendix A: Event-class reference

Every queue-manager event-control attribute. All are `ENABLED`/`DISABLED` except
`CMDEV`, which also takes `NODISPLAY`. The set below is confirmed against the
IBM MQ 9.4 *ALTER QMGR* reference; the platform column reflects its footnotes.

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
| `BRIDGEEV`  | IMS-bridge events | **z/OS only** — exclude on Multiplatforms |

Per-queue attributes for performance events: `QDPMAXEV` (queue full), `QDPHIEV` /
`QDPLOEV` with `QDEPTHHI` / `QDEPTHLO` (depth high/low), and `QSVCIEV` with
`QSVCINT` (service interval).

## Appendix B: Alternatives and tradeoffs

| Decision | Options (strongest → weakest) | Why |
|---|---|---|
| Reader | **`amqsevt` sample** → a bespoke PCF-parsing application | The sample emits JSON with zero parsing code; a custom app is only warranted for logic the sample cannot express |
| Deployment | **MQ `SERVICE`, `CONTROL(QMGR)`** → external client collector → external local daemon | The service travels with the queue manager across failover; an external client must chase the active instance; a local daemon must coordinate with the active node |
| Consumption | **Destructive drain** → topic fan-out (for multiple readers) → browse | Drain keeps the queue clear with one reader; a topic serves several readers; browse fills the queue and then drops events silently |
| Command events | **`CMDEV(NODISPLAY)`** → `CMDEV(ENABLED)` → `CMDEV(DISABLED)` | `NODISPLAY` keeps admin commands and drops inquire noise; `ENABLED` is drowned by polling collectors; `DISABLED` loses the triage view |

## Appendix D: Troubleshooting

Work the surfaces in order; the first one that is empty is your fault domain.

| Symptom | Likely cause | Fix |
|---|---|---|
| No events at all | the class is not enabled on the queue manager | `ALTER QMGR` with the class `ENABLED`; re-check with `DISPLAY QMGR` |
| No performance events, other classes fine | `PERFMEV` enabled but no queue thresholds | set `QDPMAXEV`/`QDPHIEV` (+ `QDEPTHHI`) on the queues |
| Event queue depth climbs and never falls | the collector is browsing (`-b`), or is not running | run a destructive collector (no `-b`); confirm the service is started |
| `ALTER QMGR` rejected | `BRIDGEEV` included on Multiplatforms | remove `BRIDGEEV` (z/OS only) |
| Collector keeps running after the queue manager stops | `STOPCMD` cannot reach the real process | `exec` the collector in the launcher so `MQ_SERVER_PID` is `amqsevt` |
| Events lag, then arrive in bursts | the collector's output is block-buffered | line-buffer the collector's standard output in the launcher |
| Bursts of events missing under load | the OS log layer is rate-limiting | raise or disable rate limiting for the event source |

## Appendix E: References

Search these topic titles in the IBM MQ 9.4 documentation at
<https://www.ibm.com/docs/en/ibm-mq/9.4>: *Sample program to monitor
instrumentation events (amqsevt)*, *ALTER QMGR*, *ALTER queues*, *DEFINE SERVICE*,
*Replaceable inserts on service definitions*, *Event types*, and *Instrumentation
events*.
