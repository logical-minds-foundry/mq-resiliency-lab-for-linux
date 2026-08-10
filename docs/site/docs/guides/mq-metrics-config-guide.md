# IBM MQ metrics &amp; monitoring configuration — enable data for a collector

- **MQ version:** 9.4 (Multiplatforms)
- **Status:** Active
- **Last validated in lab:** 2026-07-02
- **Related guides:** [JSON diagnostic logging](mq-json-logging-guide.md);
  [Native HA log lifecycle (runbook)](nativeha-log-lifecycle-guide.md) — the
  log-health band that reads this lab's out-of-band `mqlab_log_*` collector metrics
  alongside the exporter's queue-manager metrics

---

## 1. Purpose & audience

A metrics collector for IBM MQ — for example the IBM MQ Prometheus exporter
(`mq-metric-samples`) — reads the queue manager's **resource-monitoring** and
**statistics** publications. By default those publications are sparse: unless
monitoring and statistics are enabled on the queue manager, a collector sees
little beyond queue-manager-level basics, and per-queue and per-channel metrics
are missing. This guide enables the right data at the source and keeps the
per-object settings coherent so it actually flows. It is for anyone standing up
metrics for IBM MQ.

## 2. Scope & version floor

In scope: the queue-manager attributes that turn on the data a collector consumes
— online monitoring (`MONQ`, `MONCHL`), statistics (`STATMQI`, `STATQ`,
`STATCHL`, `STATINT`), the per-object coherence that lets objects inherit those
settings, and `MAXHANDS` headroom for a collector that opens many queue handles.

Out of scope: deploying or configuring the collector itself (the exporter, the
Prometheus scrape job). MQ's job is only to *emit* the data; the collector
attaches its own scrape-side labels (queue-manager name, object name) — MQ does
not. Attributes here are pinned to **MQ 9.4 Multiplatforms**; z/OS has different
class-switch semantics and ignores granularity.

## 3. Recommendation

Enable a **coherent baseline at the queue manager and let objects inherit it**:

- **Monitoring** `MONQ` and `MONCHL` at **`MEDIUM`** — IBM describes MEDIUM as a
  moderate rate of data collection with limited effect on system performance. It
  is the right default: `LOW` gives coarse, less-current data; `HIGH` adds
  overhead that rarely pays off for time-series scraping.
- **Statistics** `STATMQI(ON)`, `STATQ(ON)`, and `STATCHL(MEDIUM)`.
- **Leave queues and channels at `QMGR`** (the default) so they inherit the
  queue-manager setting — one place to reason about, coherent across objects.
- **Raise `MAXHANDS`** if your collector opens a handle per queue; the default can
  be too low for a collector plus normal application load.

The strongest recommendation is this single coherent set rather than per-object
tuning: uniform inheritance is easier to operate and to reason about, and it
matches what a scraping collector expects. Per-object overrides are a scalpel for
the rare queue you want at a different granularity (section 4).

Statistics (`STAT*`) and monitoring (`MON*`) are **complementary**, not
alternatives — enable both. Accounting (`ACCT*`) is a **separate, heavier** lever
for per-application data and is **not** required for the exporter path; see
[Appendix B](#appendix-b-alternatives-and-tradeoffs).

## 4. How to configure it

1. **Queue manager — enable monitoring and statistics.** Set the monitoring and
   statistics attributes on the queue manager. Optionally set `STATINT` (seconds)
   to tune how often statistics are written, and raise `MAXHANDS` for collector
   headroom:

   ```mqsc
   ALTER QMGR MONQ(MEDIUM) MONCHL(MEDIUM) STATMQI(ON) STATQ(ON) STATCHL(MEDIUM) MAXHANDS(512)
   ```

2. **Objects — keep them coherent by inheriting.** Queues and channels default to
   `QMGR` for their monitoring and statistics attributes, which means "use the
   queue-manager setting." Leave them there and they inherit the baseline above.
   Override an individual object only when you deliberately want a different
   granularity — for example a hot queue at `HIGH`:

   ```mqsc
   ALTER QLOCAL(PAYMENTS.IN) MONQ(HIGH) STATQ(ON)
   ```

3. **Mind the `NONE` trap.** At the queue-manager level, `OFF` disables collection
   for objects that inherit (`QMGR`) but still lets an object override to on.
   `NONE` disables collection for **all** objects **regardless** of their own
   setting — an object-level override is ignored. Use `OFF`, not `NONE`, unless
   you intend a hard off. See [Appendix A](#appendix-a-full-parameter-reference)
   for the exact value semantics.

## 5. Verify it worked

- **Confirm the attributes:** display the queue manager's monitoring and
  statistics attributes and check they hold the values you set (`MONQ`, `MONCHL`,
  `STATMQI`, `STATQ`, `STATCHL`).
- **Confirm the data flows:** after enabling, the queue manager's system
  monitoring publications become populated where they were sparse before, and the
  collector begins reporting per-queue and per-channel metrics (queue depth,
  message rates, channel status) rather than only queue-manager-level values.
- **Confirm objects inherit:** a queue left at `MONQ(QMGR)`/`STATQ(QMGR)` should
  show data at the queue-manager granularity; one you overrode should reflect its
  own value.

## 6. What stays / caveats

- **Granularity is a cost knob.** `HIGH` monitoring increases collection overhead;
  prefer `MEDIUM` for scraping. `STATINT` trades freshness against volume — a
  shorter interval means more frequent, larger statistics writes.
- **`NONE` overrides objects; `OFF` does not.** A queue-manager `NONE` silently
  defeats per-object overrides (section 4, step 3).
- **`MAXHANDS` can throttle a collector.** A collector that opens a handle per
  queue, on top of application handles, can exhaust the per-connection handle
  limit; raise `MAXHANDS`. General queue-manager limit tuning is its own topic —
  this guide raises `MAXHANDS` only for the collector's sake.
- **MQ emits; the collector labels.** Scrape-side identity (queue-manager name,
  object name, cluster) is applied by the collector, not MQ. Keeping
  queue-manager and object naming consistent across a fleet keeps those labels
  coherent.
- **z/OS differs.** On z/OS, statistics granularity is ignored and class switches
  apply; this guide covers Multiplatforms.

!!! note "How this lab implements it"
    The exporter path above (the IBM MQ Prometheus exporter reading the monitoring
    and statistics publications this guide enables) is only one of the metric
    sources on the boards. For the **Native HA** arms the lab also runs a
    stdlib-only, **out-of-band** collector — `src/mqlab/loglifecycle.py`, deployed
    as `lab-loglifecycle-state` — that polls each instance's log filesystem (no
    MQI, so it is independent of MQ monitoring) and emits
    `mqlab_log_disk_used_bytes`, `mqlab_log_disk_total_bytes`,
    `mqlab_log_extents_active`, `mqlab_log_extents_inactive`, and
    `mqlab_log_sample_stale`, each labelled `{qm,instance,role}`. Prometheus scrapes
    these alongside the exporter metrics, and `src/mqlab/qmboard.py`
    (`_log_health_band`) assembles them into the per-queue-manager **log-health
    band**. How to read that band is the
    [Native HA log-lifecycle runbook](nativeha-log-lifecycle-guide.md)
    (`logical-minds-foundry/.github#145`).

---

## Appendix A: Full parameter reference

Queue-manager attributes (set with `ALTER QMGR`). Objects (`ALTER QLOCAL`,
`ALTER CHANNEL`) take the same value plus `QMGR` to inherit, which is their
default.

| Attribute | Values (queue manager) | Meaning |
|---|---|---|
| `MONQ` | `OFF` / `NONE` / `LOW` / `MEDIUM` / `HIGH` | Online monitoring data collection for queues. `OFF`: off for `QMGR`-inheriting queues (override allowed). `NONE`: off for all queues regardless of their setting. `LOW`/`MEDIUM`/`HIGH`: on at that rate for `QMGR`-inheriting queues. |
| `MONCHL` | `OFF` / `NONE` / `LOW` / `MEDIUM` / `HIGH` | Online monitoring data collection for channels; same `OFF`/`NONE`/level semantics as `MONQ`. |
| `STATMQI` | `OFF` / `ON` | Collect MQI statistics for the queue manager. |
| `STATQ` | `OFF` / `ON` / `NONE` | Statistics for queues. `ON`/`OFF`: on/off for queues set to `QMGR`. `NONE`: off for all queues regardless of their setting. |
| `STATCHL` | `NONE` / `OFF` / `LOW` / `MEDIUM` / `HIGH` | Statistics for channels; required to collect channel accounting records. |
| `STATINT` | `integer` (seconds) | Interval at which statistics data is written to the monitoring queue. |
| `MAXHANDS` | `integer` | Maximum open handles any one connection may hold at once. Raise for a collector that opens many queue handles. |

Object-level values: a queue's `MONQ` and `STATQ`, and a channel's `MONCHL` and
`STATCHL`, each accept `QMGR` (inherit — the default) plus their own explicit
values. Leaving objects at `QMGR` is what makes the queue-manager baseline
coherent across them.

## Appendix B: Alternatives and tradeoffs

**Monitoring vs statistics — enable both, they answer different questions.**
Online *monitoring* (`MON*`) exposes current activity (e.g. current queue depth,
last-get time, channel status). *Statistics* (`STAT*`) accumulate interval
aggregates (message counts, byte counts) written every `STATINT`. A collector
uses both; neither substitutes for the other.

**Granularity — `LOW` vs `MEDIUM` vs `HIGH`.** Higher granularity means
more-current, finer data at higher collection cost. For periodic scraping,
`MEDIUM` is the sweet spot; `HIGH` rarely improves a dashboard enough to justify
the overhead; `LOW` can lag.

**Accounting (`ACCT*`) — a heavier, separate lever, not needed here.** Accounting
produces per-connection and per-queue *accounting* records (`ACCTMQI`, `ACCTQ`,
with `ACCTINT` interval and `ACCTCONO` to allow application override). It answers
"which application did what," for chargeback or forensic per-app analysis — at
materially higher cost than monitoring/statistics. The typical Prometheus exporter
path does **not** consume accounting, so enable `ACCT*` only when you specifically
need per-application data, independent of this guide's collector baseline.

## Appendix C: Complete configuration examples

**Queue-manager baseline** (the coherent set a collector needs):

```mqsc
ALTER QMGR MONQ(MEDIUM) MONCHL(MEDIUM) STATMQI(ON) STATQ(ON) STATCHL(MEDIUM) MAXHANDS(512)
```

**Optional — tune the statistics write interval** (default is queue-manager
defined; shorten for fresher data at higher volume):

```mqsc
ALTER QMGR STATINT(30)
```

**Objects inherit by default** — no per-object change is needed. To *override* a
single hot queue to a higher granularity:

```mqsc
ALTER QLOCAL(PAYMENTS.IN) MONQ(HIGH) STATQ(ON)
```

**Verify the queue-manager settings:**

```mqsc
DISPLAY QMGR MONQ MONCHL STATMQI STATQ STATCHL STATINT MAXHANDS
```

## Appendix D: Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Collector shows only queue-manager-level metrics, no per-queue/channel data | monitoring/statistics not enabled | set `MONQ`/`MONCHL`/`STATMQI`/`STATQ`/`STATCHL` on the queue manager |
| A specific queue has no metrics despite the queue-manager baseline | queue overridden to `OFF`, or queue-manager set to `NONE` (which overrides objects) | set the queue back to `QMGR`, or change the queue-manager value from `NONE` to a level/`ON` |
| Metrics feel stale or too voluminous | `STATINT` too long or too short | tune `STATINT` for the freshness/volume balance you want |
| Collector fails opening queues / hits a handle limit | `MAXHANDS` too low for collector + application handles | raise `MAXHANDS` |
| Queue-manager CPU rises after enabling | granularity set to `HIGH` | lower `MONQ`/`MONCHL`/`STATCHL` to `MEDIUM` |

## Appendix E: References

IBM MQ 9.4 documentation:

- *ALTER QMGR (alter queue manager settings)* —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=reference-alter-qmgr-alter-queue-manager-settings>
  (authoritative value domains for `MONQ`, `MONCHL`, `STATMQI`, `STATQ`,
  `STATCHL`, `STATINT`, `MAXHANDS`, and the `ACCT*` attributes).
- Search these topic titles at <https://www.ibm.com/docs/en/ibm-mq/9.4>:
  *Monitoring your queue manager network*, *Structure of statistics and accounting
  messages*, and *Real-time monitoring*.
