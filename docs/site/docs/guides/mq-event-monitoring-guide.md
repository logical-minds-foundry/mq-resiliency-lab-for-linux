# IBM MQ instrumentation event monitoring — events as JSON via `amqsevt`

- **MQ version:** 9.4 (`amqsevt` with JSON output on MQ for Multiplatforms)
- **Status:** Adopted — the shared `mq-event-monitor` role configures this on
  **every** queue manager across every stack (a de-facto standard, not a
  single-QM proof of concept)
- **Last validated in lab:** 2026-07-20 — the enable command (11 classes) and the
  `amqsevt -o json_compact` collector mechanics were exercised on IBM MQ 9.4.5
  (RHEL 9.6). See the companion reports in the repo:
  `docs/reports/2026-07-28-mq-event-monitoring-to-file.md` and
  `docs/reports/2026-07-20-mq-service-stdout-open-mode-evidence.md` (the
  `STDOUT`-append proof). (The role is now deployed fleet-wide and the event
  mechanics were verified per arm during the rollout; a full clean cold-rebuild of
  some arms remains gated by separate lab-reliability issues, not by event
  monitoring itself.)
- **Resilience validated:** 2026-07-24 — the crash-restart collector (a supervising
  wrapper in its own process group via `setsid`; a negative-PID group stop; `amqsevt`
  restarted through the `MQRC_OBJECT_IN_USE` 2042 exclusive-handle reap window) passed
  its full B-matrix on nativeha-ubuntu (`NHAUAPP`). The collector's **sink is
  selectable** — **syslog** (journald; the lab default) or a **file** (`.json` data +
  `.error` diagnostics) — resolved at provisioning time (`mq_event_sink`) so the
  delivered script is **single-purpose**, and **both variants are independently
  tested**. See the engineering report
  `docs/reports/2026-07-21-mq-event-monitor-wrapper-resilience.md` (the *why*), and the
  vendor-neutral, install-as-written how-tos:
  `docs/reports/2026-07-28-mq-event-monitor-resilient-service.md` (syslog) and
  `docs/reports/2026-07-29-mq-event-monitor-file-sink-resilient.md` (file — clean stop,
  2042 recovery, JSONL, and destructive drain all validated on `NHAUAPP`, 2026-07-29).
- **Working with the data:** this guide covers *producing* the feed; two companion
  repo reports cover *consuming* and *generating* it — the schema/consume reference
  `docs/reports/2026-07-22-mq-event-json-working-with-the-data.md` (envelope, the
  PCF→JSON key rule, the unordered/conditional-keys caveat, the syslog-fidelity
  finding, and an annotated appendix of real captured events) and the deterministic
  event-generation reference
  `docs/reports/2026-07-26-mq-event-generation-lab-reference.md` (how to force one
  event of each class). Real captured fixtures live under
  `docs/reports/assets/110-mq-event-captures/`.
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
Enable the events you care about, run `amqsevt -o json_compact` as a managed
collector, and you have an **event-driven JSON feed with no parsing code** — a thin
translator, not an application. It is for anyone who wants MQ's own account of
what a queue manager is doing as queryable JSON, alongside (not instead of) the
diagnostic logs.

## 2. Scope & version floor

In scope: the MQ-side configuration that **produces** the JSON event feed —
enabling event classes on the queue manager (`ALTER QMGR`), the per-queue
thresholds that make performance events fire, and running `amqsevt -o json_compact`
as a managed collector (an MQ **service object**).

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
  INHIBTEV(ENABLED) LOCALEV(ENABLED) PERFMEV(ENABLED) REMOTEEV(ENABLED) +
  SSLEV(ENABLED) STRSTPEV(ENABLED) CMDEV(NODISPLAY)
```

`BRIDGEEV` is deliberately absent — it is z/OS-only (Appendix A). There is no
`COMMEV` class. **`LOGGEREV` is also omitted:** it is valid only on a
**linear-logging** queue manager; on a circular-logging one MQ raises `AMQ8518E`
and — because `ALTER QMGR` is atomic — **rejects the whole statement**, leaving no
classes enabled. Add `LOGGEREV(ENABLED)` only where the queue manager uses linear
logging.

**Step 2 — set the per-queue thresholds performance events need.** `PERFMEV` at
the queue-manager level emits nothing on its own; queue-depth events fire only
where a queue carries thresholds. Set them on the application queues that matter:

```mqsc
ALTER QLOCAL(YOUR.APP.QUEUE) QDPMAXEV(ENABLED) QDPHIEV(ENABLED) QDEPTHHI(80)
```

**Step 3 — define the collector as a queue-manager service.** Define a `SERVICE`
that runs `amqsevt -o json_compact` against this queue manager. With no `-q`,
`amqsevt` reads the standard `SYSTEM.ADMIN.*.EVENT` set; `-m` names the queue
manager; **`-o json_compact` emits one JSON object per line**. Use `json_compact`,
not the pretty `-o json`: line-oriented pipelines (`logger`/journald, syslog, a
file tailer) treat every line as a separate record, so a multi-line pretty event is
split into fragments; one-object-per-line keeps each event a single record. Run the
collector under a small **supervising wrapper**, started via `setsid` so the wrapper
leads its own process group, and stop that whole group with a negative-PID `kill`:

```mqsc
DEFINE SERVICE(MQ.EVENT.MONITOR) REPLACE +
  CONTROL(QMGR) SERVTYPE(SERVER) +
  STARTCMD('/usr/bin/setsid') STARTARG('-w -- /path/to/your/wrapper +QMNAME+') +
  STOPCMD('/bin/kill') STOPARG('-TERM -- -+MQ_SERVER_PID+') +
  DESCR('Drain SYSTEM.ADMIN.*.EVENT to JSON')
```

The `+QMNAME+` / `+MQ_SERVER_PID+` replaceable inserts **require the `+`
delimiters** — without them MQ passes the literal token, not the value, and
`STOPCMD` cannot reach the collector. MQ substitutes `+MQ_SERVER_PID+` **even when
embedded** in a larger argument, so `-TERM -- -+MQ_SERVER_PID+` expands to
`kill -TERM -- -<pid>`: because `setsid` made the wrapper the group leader, its PID
*is* the process-group ID, and the **negative** PID signals the whole group — the
wrapper and its `amqsevt` child together. The `--` is for procps-ng `/bin/kill`, so
`-<pid>` is read as a process group, not an option.

The wrapper itself is environment-specific and lives outside this guide, but three
properties are why it exists rather than putting `amqsevt` inline:

- **Own process group** (`setsid`) so a single negative-PID `STOPCMD` reaps the
  wrapper *and* `amqsevt` together — a clean stop with no orphan. (`-w` keeps the
  QM-tracked process alive if MQ ever spawns `STARTCMD` as a group leader, which would
  force `setsid` to fork rather than exec in place.)
- **Supervise, don't just `exec`** — loop and **restart `amqsevt` if it exits**, so a
  crashed collector self-heals instead of staying dead until the queue manager next
  restarts. A restart that races the previous run's not-yet-released handle hits
  `MQRC_OBJECT_IN_USE` (2042) — the event queues are opened for *exclusive* input — so
  the wrapper **retries on a short delay** until the queue manager reaps the stale
  handle (about a ~30 s housekeeping sweep on current builds). This crash-restart is
  the whole reason to run under a wrapper rather than a bare `exec`.
- **Line-delimited output** (`-o json_compact`) so each event is one line — one
  record to journald/syslog/a file reader — and `stdbuf -oL` keeps it flushed
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
- **It self-heals.** Kill the running `amqsevt` directly (not via MQ) and confirm the
  wrapper restarts it within a few retries — the JSON feed resumes without a
  queue-manager bounce. Expect a burst of `MQRC_OBJECT_IN_USE` (2042) log lines while
  the queue manager reaps the old exclusive handle; that fail-retry-succeed sequence is
  the wrapper working, not an error.

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
    This lab wires the feed end to end: the shared `mq-event-monitor` role deploys
    the `SERVICE` on **every queue manager across all stacks** — all four HA/DR
    arms plus the shared `SVCQM` counterparty — (its supervising wrapper restarts
    `amqsevt` on crash and forwards its JSON to **journald**; the proven mechanism and
    its live B-matrix are in
    `docs/reports/2026-07-21-mq-event-monitor-wrapper-resilience.md`), Grafana **Alloy**
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
| `LOGGEREV`  | Recovery-log events — **linear-logging QMs only** (rejected on circular; omit) | Multiplatforms |
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
| `ALTER QMGR` rejected, no classes enabled | `LOGGEREV(ENABLED)` on a **circular-logging** QM (`AMQ8518E`) — the atomic `ALTER` is rejected whole | remove `LOGGEREV` (linear-logging only); re-check with `DISPLAY QMGR` |
| Events arrive split across many log records / fragments | pretty `-o json` (multi-line) into a line-oriented pipeline (`logger`, syslog, file) | use `-o json_compact` — one JSON object per line, one record per event |
| Collector (or a stray `amqsevt`) left running after the queue manager stops | `STOPCMD` doesn't reach the whole process group | start the wrapper under `setsid` and stop with `kill -TERM -- -+MQ_SERVER_PID+` — the negative PID signals the whole group |
| `amqsevt` dies but the queue manager stays up, and the event feed just stops | the collector isn't supervised (a bare `exec`) | run `amqsevt` under a wrapper that restarts it on exit, retrying through the `MQRC_OBJECT_IN_USE` (2042) exclusive-handle reap window |
| Events lag, then arrive in bursts | the collector's output is block-buffered | line-buffer the collector's standard output in the wrapper |
| Bursts of events missing under load | the OS log layer is rate-limiting | raise or disable rate limiting for the event source |

## Appendix E: References

Search these topic titles in the IBM MQ 9.4 documentation at
<https://www.ibm.com/docs/en/ibm-mq/9.4>: *Sample program to monitor
instrumentation events (amqsevt)*, *ALTER QMGR*, *ALTER queues*, *DEFINE SERVICE*,
*Replaceable inserts on service definitions*, *Event types*, and *Instrumentation
events*.
