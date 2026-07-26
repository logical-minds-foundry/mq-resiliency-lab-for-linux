# Generating IBM MQ instrumentation events — a lab reference

IBM MQ 9.4 for Multiplatforms (Linux / RHEL). The **produce-side** companion to
[Report A — working with the event JSON](2026-07-22-mq-event-json-working-with-the-data.md).
Report A documents what each event *looks like*; this report is the deterministic
recipe for **forcing** one representative event of each enabled class on a live
queue manager — for testing an event pipeline, demoing, or mechanising a
validation suite.

- **MQ version:** 9.4.5 (RHEL 9.6)
- **Verified on:** queue manager `NHARAPP` (Native HA RHEL arm), 2026-07-26
- **Fixtures:** every recipe below produced a real captured event under
  [`assets/110-mq-event-captures/`](assets/110-mq-event-captures/) (JSON per
  reason code + the `amqsevt -b` authoritative copies)
- **Status:** reference

## How to use this

Each event class is enabled at the queue-manager level (`ALTER QMGR …EV(ENABLED)`,
already done by the `mq-event-monitor` role — see the produce how-to). To capture
an **authoritative** copy while forcing an event, pause the collector so the event
queue is browsable, force it, then browse non-destructively:

```bash
# on the active QM node, as mqm
echo 'STOP SERVICE(MQ.EVENT.MONITOR)' | runmqsc NHARAPP     # pause the collector
#   … run a force recipe below …
amqsevt -m NHARAPP -b -w 5 -o json_compact                 # authoritative browse (non-destructive)
echo 'START SERVICE(MQ.EVENT.MONITOR)' | runmqsc NHARAPP    # resume; collector ships to journald
```

Route on `eventReason.value` (the numeric reason code in each recipe). `runmqsc`
input is shown as `echo '<mqsc>' | runmqsc NHARAPP`; `amqsput` runs from
`/opt/mqm/samp/bin`.

---

## Queue-manager events (`eventType` 44) — `SYSTEM.ADMIN.QMGR.EVENT`

### AUTHOREV — Not Authorized (2035)
- **Precondition:** a user **not** in the `mqm` group, with the MQ client/runtime
  libraries loaded (`. /opt/mqm/bin/setmqenv -s`).
- **Force:** connect as that user — `echo x | amqsput APP.REPLY NHARAPP`.
- **Expect:** reason **2035**, `reasonQualifier` = *Conn Not Authorized* (a connect
  check); `userIdentifier` + `applName` name who/what was refused.
- **Note:** on a QM where local `root`/`mqm` are authorized and no unprivileged
  user is available, force it instead over a **client** channel as an unauthorized
  user. A real captured example is in Report A §A.1.
- **Cleanup:** none.

### LOCALEV — Unknown Object Name (2085)
- **Force (as mqm):** `echo x | amqsput A.MISSING.QUEUE NHARAPP` — open a queue
  name that does not exist.
- **Expect:** reason **2085**; `queueName` = the missing name.
- **Cleanup:** none.

### INHIBTEV — Put Inhibited (2051) / Get Inhibited (2016)
- **Force:** `ALTER QLOCAL(APP.REPLY) PUT(DISABLED)`, then
  `echo x | amqsput APP.REPLY NHARAPP`.
- **Expect:** reason **2051**; `queueName` = the inhibited queue. (Use
  `GET(DISABLED)` + `amqsget` for the get analogue, **2016**.)
- **Cleanup:** `ALTER QLOCAL(APP.REPLY) PUT(ENABLED)`.

### REMOTEEV — Unknown Xmit Queue (2196)
- **Force:** `DEFINE QREMOTE(EVT.RMT) RNAME(X) RQMNAME(Y) XMITQ(NO.XMIT)`, then
  `echo x | amqsput EVT.RMT NHARAPP`.
- **Expect:** reason **2196**; `queueName` + `xmitQueueName` name the broken
  definition. (A missing remote queue manager gives *Unknown Remote Qmgr* **2087**.)
- **Cleanup:** `DELETE QREMOTE(EVT.RMT)`.

### STRSTPEV — Queue Manager Active (2222) / Not Active
- **Force:** *Active* is emitted at `strmqm`; on a running QM read it from the
  event queue history (or journald). *Not Active* is emitted at controlled
  `endmqm` — **disruptive on the active HA instance** (triggers failover); capture
  it deliberately, not casually.
- **Expect:** reason **2222**; `hostName` = the node it became active on — the
  Native HA failover signal.
- **Cleanup:** none.

## Performance events (`eventType` 45) — `SYSTEM.ADMIN.PERFM.EVENT`

### PERFMEV — Queue Depth High (2224) and Queue Full (2053)
- **Precondition:** per-queue thresholds (the role sets these; a bespoke queue:
  `DEFINE QLOCAL(EVT.HI) MAXDEPTH(5) QDPHIEV(ENABLED) QDEPTHHI(80) QDPMAXEV(ENABLED)`).
- **Force:** put past the high threshold then past `MAXDEPTH` —
  `for i in 1 2 3 4 5 6; do echo m$i | amqsput EVT.HI NHARAPP; done`.
- **Expect:** **2224** (Depth High, at 80% of 5 = depth 4) then **2053** (Queue
  Full, on the 6th put). `baseObjectName` = the queue; note the performance
  payload (`highQueueDepth`, `msgEnqCount`/`msgDeqCount`) differs from QMGR events.
  (Depth-Low **2225** needs draining below `QDEPTHLO`.)
- **Cleanup:** `DELETE QLOCAL(EVT.HI)`.

## Channel events (`eventType` 46) — `SYSTEM.ADMIN.CHANNEL.EVENT`

### CHLEV — Channel Started (2282) / Stopped (2283)
- **Precondition:** a running MCA channel (sender/receiver) — e.g. the arm's
  `NHARAPP.SVCQM` sender to the SVCQM counterparty. SVRCONN client channels do
  **not** emit start/stop.
- **Force:** `STOP CHANNEL(NHARAPP.SVCQM)` then `START CHANNEL(NHARAPP.SVCQM)`.
- **Expect:** **2283** *Channel Stopped* (+ **2279** *Channel Stopped By User*),
  then **2282** *Channel Started*. These are the production-normal channel signal
  (Report A's *Channel Blocked* 2577 is the atypical CHLAUTH case).
- **Cleanup:** leave the channel running (it restarts).

### CHADEV — Channel Auto-definition — *deferred*
- Needs `CHAD(ENABLED)` and an inbound channel with no matching definition (auto-def
  OK/error). Not exercised in the lab's normal topology; stage deliberately.

### SSLEV — Channel SSL Error (2371) — *deferred*
- The most setup-heavy: a TLS-configured channel plus a **staged handshake fault**
  (expired/mismatched/absent certificate).

## Configuration events (`eventType` 43) — `SYSTEM.ADMIN.CONFIG.EVENT`

### CONFIGEV — Create (2367) / Change (2368) / Delete (2369)
- **Force:** `DEFINE QLOCAL(EVT.CFG)`; `ALTER QLOCAL(EVT.CFG) DESCR('event demo')`;
  `DELETE QLOCAL(EVT.CFG)`.
- **Expect:** **2367** create (full attribute dump), **2368** change (a **Before/After
  pair** via `objectState`), **2369** delete. Config events are the largest — the
  captured change event was ~2.4 KB (still far under the syslog cap; see the
  fidelity table). Each carries a `correlationID` tying it to its command event.
- **Cleanup:** the `DELETE` above is itself the delete recipe.

## Command events (`eventType` 99) — `SYSTEM.ADMIN.COMMAND.EVENT`

### CMDEV — Command MQSC (2412)
- **Precondition:** `CMDEV(ENABLED)` or `CMDEV(NODISPLAY)` (the role uses NODISPLAY
  — every mutating MQSC/PCF command, excluding inquire/DISPLAY).
- **Force:** any mutating command — the `DEFINE/ALTER/DELETE` recipes above each
  emit one.
- **Expect:** **2412**; nested `commandContext` (who/what) + `commandData` (the
  parameters); shares a `correlationID` with the config event it produced — join on
  it for "who ran what, and what it changed."
- **Cleanup:** none.

---

## Coverage from this pass

Forced and captured live (fixtures under `assets/110-mq-event-captures/json/`):
**2051, 2053, 2085, 2196, 2222, 2224, 2279, 2282, 2283, 2367, 2368, 2369, 2412** —
13 reason codes across all five `eventType` families. **AUTHOREV (2035)** is
documented from the prior capture set (Report A §A.1); **CHADEV** and **SSLEV** are
deferred (staging-heavy) and derive-later. This report is the seed for mechanising
event generation in the live-lab validation framework.
