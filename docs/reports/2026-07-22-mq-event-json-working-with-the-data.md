# Working with IBM MQ instrumentation events as JSON

IBM MQ 9.4 for Multiplatforms (Linux / RHEL). This is the **consume-side**
companion to the produce-side how-to
([`2026-07-20-mq-event-monitoring-to-file.md`](2026-07-20-mq-event-monitoring-to-file.md))
and the site guide
([`mq-event-monitoring-guide.md`](../site/docs/guides/mq-event-monitoring-guide.md)):
those cover **getting** the `amqsevt` JSON event feed flowing; this covers
**working with the data** once it is — the envelope, the key-naming rule, the
caveats that bite a parser, and an annotated reference of real captured events.

- **MQ version:** 9.4.5 (`amqsevt` JSON output, MQ for Multiplatforms)
- **Grounded in:** real events captured on the live lab (QM `EVTCAP`, RHEL 9.6),
  reproduced from the `mq-event-monitor` role now standard on every lab QM
- **Status:** reference

## 1. Why this exists — there is no formal schema

IBM publishes **no JSON schema** for `amqsevt` output. The envelope is defined
only by the sample program (`amqsevta.c`, shipped in the redistributable client),
and the `eventData` payload is documented one layer down, in prose, in the
*Event message reference → Event message descriptions*. Anyone parsing this feed
is working against a **sample-defined, convention-only** format. This report
writes that convention down and grounds it in captured events, so consumers can
build against something concrete instead of IBM's single illustrative snippet.

The one authoritative origin source is **Mark Taylor** (the ex-IBM MQ developer
who wrote the JSON formatter): *"Formatting MQ Events as JSON"*
(<https://marketaylor.synology.me/?p=401>). He is explicit that JSON here is
"a simple format … capable of being parsed and searched" — **not** a versioned
schema. Treat it accordingly: **parse defensively, by key name.**

## 2. The envelope — five top-level keys, plus two conditional ones

Every event, regardless of class, is a JSON object with the **same five
top-level keys**:

| Key | Shape | Meaning |
|---|---|---|
| `eventSource` | `{objectName, objectType, queueMgr}` | which `SYSTEM.ADMIN.*.EVENT` queue the event was read from |
| `eventType` | `{name, value}` | the event **class** (see §5) — this is your primary routing key |
| `eventReason` | `{name, value}` | the specific reason (name + PCF reason code) — your secondary routing key |
| `eventCreation` | `{timeStamp, epoch}` | ISO-8601 `timeStamp` and Unix `epoch` seconds |
| `eventData` | object | the **per-event payload** — shape varies by event (§3) |

Two further top-level keys appear **only on some classes** — do not assume them:

| Key | Appears on | Meaning |
|---|---|---|
| `correlationID` | Config + Command events | 48-hex-char ID tying a command event to the config event it caused (identical value on both) |
| `objectState` | Config **change** events | `"Before Change"` / `"After Change"` — a change emits a **pair**, one of each |

`eventType`, `eventReason`, and `eventCreation` are stable; `eventSource` is
stable in shape; **everything that varies lives in `eventData`.**

## 3. `eventData` — the PCF→JSON key-naming rule

`eventData` keys are the underlying **PCF/MQI parameter names, camelCased**. The
derivation from the MQI constant (as seen in the C header files, or in
`amqsevt -d` "definitions" output) to the JSON key is mechanical:

1. Drop the type prefix — `MQCA_`, `MQIA_`, `MQCACF_`, `MQIACF_`, `MQCACH_`, …
2. Lowercase and split on underscores.
3. camelCase the remaining words, **expanding MQ's abbreviations to whole
   words** (`Q` → `Queue`, `APPL` → `appl`, `MGR` → `Mgr`).

Verified mappings (from IBM's own `amqsevt -d` vs. JSON sample output):

| MQI/PCF constant | JSON key |
|---|---|
| `MQCA_Q_MGR_NAME` | `queueMgrName` |
| `MQCACF_APPL_NAME` | `applName` |
| `MQIA_APPL_TYPE` | `applType` |
| `MQCA_BASE_OBJECT_NAME` | `baseObjectName` |

**`amqsevt -d` is the tool that proves this** — it prints the raw MQI constant
names ("exactly as they appear in the header files"), so running `-d` and `-o json`
against the same event class shows the mapping directly. See
[Appendix&nbsp;B](#appendix-b-the-amqsevt--d-key-derivation-proof) for the worked
`-d`↔JSON mapping.

Because the keys are derived, not schema-fixed, **a parser should look them up by
name, never assume a fixed set**, and should tolerate keys it does not recognise
(a later MQ version can add fields — see Mark Taylor, *"Event formatter changes
with MQ 9.2.4"*, `?p=959`).

## 4. The two caveats that bite a parser — unordered & conditional

This is the single most important section for anyone consuming the feed, and it
rests on **IBM's own words**, not just observed behaviour. From *Event message
descriptions* (IBM MQ 9.4):

> "The PCF structures in the message data are **not returned in a defined
> order**. They must be identified from the parameter identifiers…"

> "for some events, certain parameters … are **optional, and are returned only
> if they contain information that is relevant** to the circumstances."

Concretely, for the JSON:

- **Keys are unordered.** Two events of the same reason can serialise their
  `eventData` keys in different orders. **Never** parse positionally; index by key.
- **Keys are conditional.** A key present on one instance of an event can be
  absent on another (it is emitted only when relevant). **Never** assume a key
  exists; treat a missing key as "not relevant here," not an error.
- **`connTag`-style fixed-width fields** are present but often all-zeros when
  they carry no value.

A consumer that hard-codes key positions, or that fails when an "expected" key is
missing, **will** break on this feed. This is exactly why the lab's own event
handlers were always written to process events **transactionally and by key**.

## 5. `eventType` taxonomy — the five classes and their queues

`eventType.value` routes an event to one of five families, each drained from its
own `SYSTEM.ADMIN.*.EVENT` queue:

| `eventType.value` | `eventType.name` | Source queue | Reason codes seen |
|---|---|---|---|
| 43 | Config Event | `SYSTEM.ADMIN.CONFIG.EVENT` | Create 2367, Change 2368, Delete 2369, Refresh |
| 44 | Queue Mgr Event | `SYSTEM.ADMIN.QMGR.EVENT` | Not Authorized 2035, Unknown Object 2085, Put/Get Inhibited 2051/2016, Unknown Xmit Queue 2196, Qmgr Active 2222, … |
| 45 | Perfm Event | `SYSTEM.ADMIN.PERFM.EVENT` | Queue Full 2053, Depth High 2224, Depth Low 2225, Service Interval 2226/2227 |
| 46 | Channel Event | `SYSTEM.ADMIN.CHANNEL.EVENT` | Channel Started 2282, Stopped 2283, Blocked 2577, SSL Error 2371, … |
| 99 | Command Event | `SYSTEM.ADMIN.COMMAND.EVENT` | Command MQSC 2412, Command PCF 2413 |

**Route on `eventType.value` first, then `eventReason.value`.** The `name` fields
are human-readable but the numeric `value` is the stable key.

## 6. Consuming the feed — practical guidance

- **Filter/alert on `eventType.value` + `eventReason.value`.** These are the
  stable, machine-friendly keys. The `.name` strings are for humans.
- **The lab ships JSONL (`-o json_compact`)** — one complete JSON object per
  line, newline-delimited. Parse line-by-line; each line is independently valid
  JSON. (The pretty-printed `-o json` in the appendices below is the *same data*,
  re-indented for reading.)
- **Security events are high-value.** *Not Authorized* (2035) carries
  `userIdentifier` + `applName` — who was refused and by what program — and is the
  first thing to alert on.
- **Config + Command events pair up** via `correlationID` — join them to get
  "who ran what command, and what object state it produced."
- **Config events are large** — they dump the object's *full* attribute set
  (~50+ keys on a queue). Store them as audit records; do not try to alert on
  every field.
- **Handle failover.** On a Native HA queue manager, expect a *Queue Manager
  Not Active* (stop) on the old node paired with a *Queue Manager Active* (start)
  on the new one across a failover; the collector travels with the QM
  (`CONTROL(QMGR)`), so the feed continues from the new active instance.

## 7. Output format — `json`, `json_compact`, `json_array`

`amqsevt -o` accepts (in this MQ build): `text`, `json`, `json_compact`,
`json_array`.

- **`json`** — pretty-printed, multi-line per event (used in the appendices here).
- **`json_compact`** — one event per line (JSONL). **What the lab ships**, because
  it is line-oriented — one journald entry per event, one Loki line per event.
- **`json_array`** — a single JSON array of all events; convenient for a bounded
  batch, wrong for a continuous feed.

> **Correctness note.** IBM's 9.4 `amqsevt` documentation lists only `text` and
> `json` under `-o`. **`json_compact` and `json_array` are real and lab-verified
> but are not documented by IBM for 9.4** — a reader checking the IBM page will
> not find them. They work; they are simply undocumented. Do not rely on IBM docs
> to describe them.

## 8. Syslog fidelity — do events fit, and is the journald copy faithful?

A fair worry about the journald/`logger` path: does anything get **truncated**
between the event queue and Loki? Measured live (2026-07-26, `NHARAPP`) by reading
each event two ways — the authoritative `amqsevt -m NHARAPP -b -o json_compact`
browse vs. the journald copy the collector shipped through `logger --size 32768`:

- The **largest** event observed was **2,424 bytes** (a `Config Change` full
  attribute dump) — about **13× under** the 32,768-byte `logger --size` cap.
- **11 of 12** reason types were **byte-identical** between the authoritative read
  and the journald copy; the single difference was a different command *instance*,
  not truncation.

**Finding: MQ instrumentation events fit comfortably in the journald/syslog path,
and the shipped copy is byte-faithful — no truncation.** The `logger --size 32768`
setting is headroom for pathological config objects, not a limit that normal
events approach. Full per-reason table and the real captured fixtures (one JSON
per reason code) are under
[`assets/110-mq-event-captures/`](assets/110-mq-event-captures/README.md)
([syslog-fidelity.md](assets/110-mq-event-captures/syslog-fidelity.md)). To force
each event class yourself, see the companion
[event-generation reference](2026-07-26-mq-event-generation-lab-reference.md).

---

## Appendix A — Captured events, annotated

Real events captured on IBM MQ 9.4.5 (RHEL 9.6, QM `EVTCAP`), one per reason we
can force in the lab, pretty-printed (`-o json`). Each is followed by an
annotation of its **`eventData`** keys (the envelope is per §2). The raw
capture set also lives in
[the produce-side report's Appendix&nbsp;D](2026-07-20-mq-event-monitoring-to-file.md#appendix-d--captured-event-examples-the-de-facto-schema);
here each is annotated for consumers.

### A.1 Not Authorized — Queue Mgr Event (44), reason 2035 — `AUTHOREV`

The highest-value security event.

```json
{
"eventSource" : { "objectName": "SYSTEM.ADMIN.QMGR.EVENT", "objectType" : "Queue", "queueMgr" : "EVTCAP"},
"eventType"   : { "name" : "Queue Mgr Event", "value" : 44 },
"eventReason" : { "name" : "Not Authorized", "value" : 2035 },
"eventCreation" : { "timeStamp" : "2026-07-20T15:17:56Z", "epoch" : 1784560676 },
"eventData" : {
  "queueMgrName" : "EVTCAP",
  "reasonQualifier" : "Conn Not Authorized",
  "userIdentifier" : "nobody",
  "applType" : "Unix",
  "applName" : "amqsput",
  "connTag" : "0000...0000"
}
}
```

| `eventData` key | Meaning |
|---|---|
| `reasonQualifier` | which authority check failed — `Conn Not Authorized` = a **connect** (type 1); open/close/sub give other qualifiers |
| `userIdentifier` | the OS user that was refused (`nobody`) — **who** |
| `applName` / `applType` | the program and its type (`amqsput`, `Unix`) — **what** |
| `connTag` | fixed-width connection tag; all-zeros when none (conditional/placeholder field) |

### A.2 Unknown Object Name — Queue Mgr Event (44), reason 2085 — `LOCALEV`

```json
"eventData" : { "queueMgrName" : "EVTCAP", "applType" : "Unix", "applName" : "amqsput", "queueName" : "A.MISSING.QUEUE" }
```

| key | Meaning |
|---|---|
| `queueName` | the name the application tried to open that does not exist |
| `applName` / `applType` | the offending program |

### A.3 Put Inhibited — Queue Mgr Event (44), reason 2051 — `INHIBTEV`

```json
"eventData" : { "queueMgrName" : "EVTCAP", "queueName" : "EVT.INH", "applType" : "Unix", "applName" : "amqsput" }
```

| key | Meaning |
|---|---|
| `queueName` | the inhibited queue a put was attempted against (Get Inhibited 2016 is the get analogue) |

### A.4 Unknown Xmit Queue — Queue Mgr Event (44), reason 2196 — `REMOTEEV`

```json
"eventData" : { "queueMgrName" : "EVTCAP", "queueName" : "EVT.RMT", "xmitQueueName" : "NO.XMIT", "applType" : "Unix", "applName" : "amqsput" }
```

| key | Meaning |
|---|---|
| `queueName` | the `QREMOTE` definition that failed to resolve |
| `xmitQueueName` | the transmission queue it named, which does not exist (a missing remote qmgr instead gives *Unknown Remote Qmgr* 2087) |

### A.5 Queue Full — Perfm Event (45), reason 2053 — `PERFMEV`

Note the **entirely different `eventData` shape** for performance events.

```json
{
"eventSource" : { "objectName": "SYSTEM.ADMIN.PERFM.EVENT", "objectType" : "Queue", "queueMgr" : "EVTCAP"},
"eventType"   : { "name" : "Perfm Event", "value" : 45 },
"eventReason" : { "name" : "Queue Full", "value" : 2053 },
"eventCreation" : { "timeStamp" : "2026-07-20T15:16:44Z", "epoch" : 1784560604 },
"eventData" : {
  "queueMgrName" : "EVTCAP",
  "baseObjectName" : "EVT.FULL",
  "timeSinceReset" : 2,
  "highQueueDepth" : 1,
  "msgEnqCount" : 1,
  "msgDeqCount" : 0
}
}
```

| key | Meaning |
|---|---|
| `baseObjectName` | the queue (note: **not** `queueName` — performance events use `baseObjectName`) |
| `highQueueDepth` | peak depth reached in the interval |
| `timeSinceReset` | seconds since the statistics were last reset |
| `msgEnqCount` / `msgDeqCount` | messages enqueued / dequeued in the interval |

### A.6 Queue Depth High — Perfm Event (45), reason 2224 — `PERFMEV`

Same shape as A.5 (`baseObjectName` = `EVT.HI`, `highQueueDepth` = 5). The
early-warning backlog signal; requires the per-queue `QDPHIEV` + `QDEPTHHI`
threshold the role sets. Depth Low (2225) and Service Interval (2226/2227) share
this shape.

### A.7 Config Create Object — Config Event (43), reason 2367 — `CONFIGEV`

Config events dump the object's **full** attribute set — the canonical audit
record. Abbreviated here; the full ~50-key block is in the produce-side report.

```json
{
"eventType"   : { "name" : "Config Event", "value" : 43 },
"eventReason" : { "name" : "Config Create Object", "value" : 2367 },
"correlationID" : "414D5120455654434150202020202020CA3B5E6A49260040",
"eventData" : {
  "eventUserId" : "mqm", "eventOrigin" : "Console", "eventQueueMgr" : "EVTCAP",
  "objectType" : "Queue", "queueName" : "EVT.CFG", "queueType" : "Local",
  "maxQueueDepth" : 5000, "definitionType" : "Predefined"
  /* … ~50 further attributes: the full object definition … */
}
}
```

| key | Meaning |
|---|---|
| `eventUserId` / `eventOrigin` | who/what performed the config action (`mqm`, `Console`) |
| `objectType` / `queueName` / `queueType` | the object created |
| `correlationID` (top-level) | ties this to the command event that caused it (A.9) |
| *(remaining ~50 keys)* | the object's full attribute set, verbatim |

### A.8 Config Change Object — Config Event (43), reason 2368 — `CONFIGEV`

A change emits a **Before/After pair**, distinguished by the top-level
`objectState` key; each carries the full attribute block. The diff in the capture
is `queueDesc: "" → "event demo"`.

```json
{ "eventReason": {"name":"Config Change Object","value":2368}, "objectState": "Before Change",
  "correlationID": "414D5120…53260040", "eventData": { "queueName": "EVT.CFG", "queueDesc": "" /* …full block… */ } }
{ "eventReason": {"name":"Config Change Object","value":2368}, "objectState": "After Change",
  "correlationID": "414D5120…53260040", "eventData": { "queueName": "EVT.CFG", "queueDesc": "event demo" /* …full block… */ } }
```

| key | Meaning |
|---|---|
| `objectState` (top-level) | `"Before Change"` / `"After Change"` — **the pair is one logical change**; diff the two |
| `correlationID` (top-level) | identical on both halves and on the triggering command event |

### A.9 Command MQSC — Command Event (99), reason 2412 — `CMDEV`

Records **who ran what**. `eventData` has a distinct **nested** shape.

```json
"eventData" : {
  "commandContext" : { "eventUserId" : "mqm", "eventOrigin" : "Console", "eventQueueMgr" : "EVTCAP", "command" : "Change Queue" },
  "commandData" : { "queueName" : "EVT.CFG", "queueType" : "Local", "queueDesc" : "event demo" }
}
```

| key | Meaning |
|---|---|
| `commandContext` | **who/what** — user, origin, and the command verb (`Change Queue`) |
| `commandData` | **what** — the command's parameters |
| `correlationID` (top-level) | shared with the config event the command produced (A.7/A.8) — join on it |

### A.10 Channel Blocked — Channel Event (46), reason 2577 — `CHLEV`

```json
"eventData" : {
  "queueMgrName" : "EVTCAP",
  "connectionName" : "localhost (127.0.0.1)",
  "connectionNameList" : [ "localhost" ],
  "reasonQualifier" : "Channel Blocked Noaccess",
  "channelName" : "SYSTEM.DEF.SVRCONN",
  "clientUserId" : "mqm",
  "applName" : "amqsputc",
  "applType" : "Unix"
}
```

| key | Meaning |
|---|---|
| `channelName` | the channel a CHLAUTH rule blocked |
| `connectionName` / `connectionNameList` | the blocked peer (note `connectionNameList` is a **JSON array** — a value that is not a scalar) |
| `reasonQualifier` | why it was blocked (`Channel Blocked Noaccess`) |
| `clientUserId` / `applName` | the identity/program that was blocked |

> *Channel Blocked* is the **atypical** channel event. The production-normal
> *Channel Started* (2282) / *Stopped* (2283) come from running MCA
> sender/receiver channels, not SVRCONN client connects. See
> [Appendix&nbsp;C.2](#c2-chlev-channel-startedstopped-22822283) for those.

### A.11 Queue Manager Active — Queue Mgr Event (44), reason 2222 — `STRSTPEV`

```json
"eventData" : { "queueMgrName" : "EVTCAP", "hostName" : "nha-rhel-a3", "reasonQualifier" : "Failover Not Permitted" }
```

| key | Meaning |
|---|---|
| `hostName` | the node the QM became active on — **the failover signal** on Native HA |
| `reasonQualifier` | `Failover Not Permitted` on a standalone QM; on a Native HA QM this pairs with a *Not Active* (stop) on the old node |

---

## Appendix B — The `amqsevt -d` key-derivation proof

The §3 rule is directly demonstrable. `amqsevt` renders the **same events** two
ways: `-d` ("print definitions" — the raw MQI constant names, "exactly as they
appear in the header files") and `-o json` (the camelCased keys). Read side by
side, they *are* the mapping — e.g. `MQCA_Q_MGR_NAME` → `queueMgrName`,
`MQCACF_APPL_NAME` → `applName`, `MQIA_APPL_TYPE` → `applType`,
`MQCA_BASE_OBJECT_NAME` → `baseObjectName`. IBM's amqsevt reference page prints a
worked `-d` output and a JSON output of the same events for exactly this purpose
(see [Appendix&nbsp;D](#appendix-d--references)).

Every `eventData` key in the captured events of Appendix A follows this
derivation — the lab's own confirmation of the rule on real data. To reproduce it
first-hand on any lab QM: `amqsevt -m <QM> -d` and `amqsevt -m <QM> -o json`
against the same event class, and diff the key names.

---

## Appendix C — Events not yet captured (how to capture them)

Per epic #110, any event not captured here can be captured experimentally. These
four are the genuine gaps against the 11 enabled classes; the recipe for each:

### C.1 `STRSTPEV` — Queue Manager Not Active (stop)

`endmqm -c <QM>`. The stop event is written to `SYSTEM.ADMIN.QMGR.EVENT` during
controlled shutdown; because the collector stops with the QM, read it on restart
(`strmqm`, then `amqsevt -m <QM> -o json` drains the queued event).

### C.2 `CHLEV` — Channel Started/Stopped (2282/2283)

Needs a **running MCA channel pair** (sender/receiver), not a SVRCONN client. On
the lab, the arm QM's channels to the `SVCQM` counterparty qualify: `STOP CHANNEL(<mca>)`
then `START CHANNEL(<mca>)` emits Stopped then Started.

### C.3 `CHADEV` — Channel Auto-definition

Requires `CHAD(ENABLED)` and an inbound channel with no matching definition, to
trigger an auto-def OK/error event. Not exercised in the lab's normal topology;
stage deliberately.

### C.4 `SSLEV` — Channel SSL Error (2371)

The most setup-heavy: a TLS-configured channel plus a **deliberate handshake
fault** (expired/mismatched/absent certificate). Stage against the lab's TLS
channels.

---

## Appendix D — References

**IBM MQ 9.4 documentation** (fetched + cached via `tools/ibm_doc_cache.py`):

- *Sample program to monitor instrumentation events (amqsevt)* —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=monitoring-sample-program-monitor-instrumentation-events-multiplatforms>
  — the `amqsevt` reference: flags (`-o`, `-d`, `-b`, `-q`, `-m`, …) and the
  worked default/`-d`/JSON sample outputs (the envelope + key-derivation source).
- *Event message descriptions* —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-event-message-descriptions>
  — the per-event catalogue **and** the authoritative "not returned in a defined
  order / optional parameters" caveat (§4).
- *Event message reference* (overview) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-event-message>
- *Event message format* —
  <https://www.ibm.com/docs/en/ibm-mq/9.4?topic=reference-event-message-format>
- *Instrumentation events* (concept) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=monitoring-instrumentation-events>

**Mark Taylor** (author of the `amqsevt` JSON formatter):

- *Formatting MQ Events as JSON* — <https://marketaylor.synology.me/?p=401> —
  the canonical origin source (envelope, camelCase keys, "a simple format … not a
  schema"). *(Verified.)*
- *Event formatter changes with MQ 9.2.4* — `https://marketaylor.synology.me/?p=959`
  (schema-evolution note). *(URL from search; not independently re-verified — check
  before quoting.)*
- The old IBM developerWorks URL for the JSON post is **dead** (developerWorks
  decommissioned); use the `synology.me` blog as canonical.
