# Native HA log lifecycle — the operator's runbook

- **MQ version:** 9.4
- **Status:** Active
- **Last validated in lab:** 2026-08-10 — verified against a live `native-ha`
  arm (queue manager `NHARAPP`, MQ 9.4.5, RHEL 9.6) during the log-lifecycle
  spike (#808). The verified specifics — log type, extent naming, watermarks,
  logger-event fields, and the reclaim-health behaviour — are recorded in
  `docs/reports/2026-08-10-nativeha-log-lifecycle-spike.md`.
- **Related guides:**
  [Instrumentation event monitoring](mq-event-monitoring-guide.md) — the
  `amqsevt` → JSON pipeline the logger-event feed rides on;
  [JSON diagnostic logging](mq-json-logging-guide.md) — the `AMQ7467I` /
  `AMQ7468I` / `AMQ7490I` error-log lines this runbook reads;
  [Metrics & monitoring configuration](mq-metrics-config-guide.md) — the
  monitoring pipeline the log-health metrics feed into.

---

## 1. Purpose & audience

This runbook teaches an operator to read a **Native HA queue manager's recovery
log as a living system**: what the queue manager is doing to its log extents on
its own, which signals report that the automation is healthy, and — the part
that trips people up — why the **active instance and its replicas legitimately
show different numbers**. It is for whoever watches a Native HA queue manager in
production and needs to tell "the automation is working" apart from "the log is
about to fill".

It is a *reading* guide, not a *configuration* guide: a Native HA queue manager
arrives with all of this switched on and correct. The job is to interpret it, not
to turn it on.

## 2. Scope & version floor

In scope: recovery-log logging types; why Native HA mandates **replicated**
logging; what **automatic log management** and **automatic media images** do to
log extents; how to read the log-health signals (extent counts, disk fill,
recovery watermarks, media-image recency, the logger-event feed); and the one
real constraint that automatic log management imposes.

Out of scope: enabling the logger-event feed itself (that is the
[event-monitoring guide](mq-event-monitoring-guide.md)); the metrics-collector
and dashboard mechanics (the lab wiring is noted where relevant but is not the
subject); and disaster-recovery topology beyond the single Native HA group.

Version floor: replicated logging and Native HA are MQ **9.4** on MQ for
Multiplatforms. Automatic log management and automatic media images predate 9.4
but are what a Native HA queue manager uses by default.

## 3. The three log types — and why Native HA uses replicated

IBM MQ writes a **recovery log** so it can rebuild persistent state after a
failure. There are three logging types, and the choice is made once, at queue-manager
creation:

| Log type | What it does | Recovery reach |
|---|---|---|
| **Circular** | A fixed ring of extents, overwritten in place | Restart recovery only — no media recovery, no replication |
| **Linear** | An ever-advancing sequence of extents; old extents are retained until no longer needed | Restart **and** media recovery; supports archiving and backup queue managers |
| **Replicated** | Linear logging whose extents are **synchronously replicated** to the other instances in the Native HA group | Restart and media recovery, made redundant across the group |

**Native HA is replicated logging, and replicated logging is linear logging with
two defaults turned on.** A Native HA group keeps three instances of one queue
manager in lock-step by replicating the recovery log itself: every log write on
the active instance is streamed to the replicas before it is acknowledged. That
only works on a linear-family log (circular's in-place overwrite cannot be
replayed on a replica), so a Native HA queue manager is created with:

```ini
Log:
   LogType          = REPLICATED
   LogManagement    = Automatic
   LogPath          = /var/mqm/log/<QM>/
   LogFilePages     = 8192          # 32 MB per extent (8192 × 4 KB pages)
   LogPrimaryFiles  = 10
   LogSecondaryFiles = 10
```

- **`LogType=REPLICATED`** — the log is linear *and* replicated across the group.
- **`LogManagement=Automatic`** — MQ owns the extent lifecycle (section 4): it
  creates, reuses, and deletes extents itself, with no archiving step and no
  operator action.
- **Automatic media images on by default** — `IMGSCHED(AUTO)` schedules media
  images so old extents can be released (section 4).

You can confirm the type: `LogType` and `LogManagement` are authoritative in
**`qm.ini`** (the `Log:` stanza). Note that `DISPLAY QMGR LOGTYPE` does **not**
exist — asking for it is a syntax error (`AMQ8405I`); read `qm.ini` instead.

Because replicated logging is linear-family, the recovery-log **logger events**
(`LOGGEREV`) that this runbook reads are valid and accepted on a Native HA queue
manager — on a circular queue manager the same `ALTER QMGR LOGGEREV(ENABLED)` is
rejected with `AMQ8518E`. See the [event-monitoring guide](mq-event-monitoring-guide.md).

## 4. What the automation does

With `LogManagement=Automatic`, the queue manager runs two independent
housekeeping loops. Reading the log health is really reading the interplay
between them.

### 4a. Extent lifecycle — create, reuse, delete

Log extents are fixed-size files under `LogPath/<QM>/active/`, named
`S<nnnnnnn>.LOG` (standard, in use) and `R<nnnnnnn>.LOG` (reserved/recycled).
As the queue manager writes, it rolls forward into a new extent; each roll is one
step of `S0000001 → S0000002 → …`. When an extent is **no longer needed for any
kind of recovery**, automatic log management either **reuses** it (renames it into
the reserved pool for a future write) or **deletes** it. The queue manager reports
each housekeeping pass in its error log:

```text
AMQ7490I: Log extent statistics: 1 created. 8 reused. 0 deleted.
```

Those three counters — **created / reused / deleted** — are the pulse of the
extent lifecycle. A healthy queue manager under steady load shows extents being
**reused or deleted** at roughly the rate they are created, so the active extent
count holds a band rather than climbing forever.

### 4b. Media images — what lets extents be released

An extent cannot be reused or deleted while it is still needed to **media-recover**
an object. Automatic media images are what move that boundary forward: MQ
periodically records a media image of recoverable objects, and once an image
exists past a given extent, the extents before it are releasable. On a Native HA
queue manager these are scheduled automatically:

```mqsc
ALTER QMGR IMGSCHED(AUTO) IMGINTVL(60) IMGLOGLN(3271) IMGRCOVO(YES) IMGRCOVQ(YES)
```

- **`IMGSCHED(AUTO)`** — MQ schedules media images itself; no `rcdmqimg` cron.
- **`IMGINTVL(60)`** — target interval, in minutes, between automatic images.
- **`IMGLOGLN(3271)`** — also trigger an image after this many MB of log written,
  whichever comes first.

The key operational consequence: **media-image recency is a slow gauge.** The
media-recovery boundary only advances when an automatic image fires, so on a
**young** queue manager — before the first `IMGINTVL` has elapsed — no image has
been taken yet, the media boundary is still pinned at the first extent, and no
extent can be released. That is expected early-life behaviour, not a fault (see
section 5).

## 5. Reading the log-health band

The lab surfaces all of this as a **log-health band** on the per-queue-manager
Grafana board, present only on the Native HA arms. It is built to be read
**top to bottom as a story**, and to be read **per instance** — the active and its
replicas are drawn as separate lines because they genuinely diverge.

!!! note "How this lab implements it"
    A stdlib-only collector (`src/mqlab/loglifecycle.py`, deployed as
    `lab-loglifecycle-state`) polls each instance's filesystem — no MQI — for
    disk usage and the `active/` extent split, and tags every sample by
    `instance` and `role` (`dspmq -m <QM> -o nativeha -x`). It emits
    `mqlab_log_disk_used_bytes`, `mqlab_log_disk_total_bytes`,
    `mqlab_log_extents_active` (the `S…` count), `mqlab_log_extents_inactive`
    (the `R…` count), and `mqlab_log_sample_stale`. The band is assembled in
    `src/mqlab/qmboard.py` (`_log_health_band`). The logger-event feed rides the
    shared `amqsevt` → journald → Loki pipeline (epic
    `logical-minds-foundry/.github#145`).

### 5a. The reclaim-health trend (lead panel)

The lead panel plots the **standard (`S`, in-use)** and **reserved (`R`,
recycled)** extent counts over time, per instance. This is the headline signal.

- **Healthy:** the `S` count holds a band — it rises as extents are created, then
  levels or falls as reuse/delete keeps pace (cross-check the `AMQ7490I`
  created/reused/deleted counters, section 4a). `R` reflects the recycled pool.
- **Watch the automation:** the `S` count climbs **monotonically** with
  `reused=0` / `deleted=0` in `AMQ7490I` and the media boundary pinned (section
  5c). On a young queue manager this is the **expected pre-first-media-image**
  pattern — the log grows because nothing has been released yet; it should
  stabilise once the first automatic image fires. If it *keeps* climbing well
  past `IMGINTVL` with the media boundary still pinned, that is the "automation is
  not reclaiming" pattern worth investigating.

### 5b. Log disk fill

Two panels — **log disk %** (the `LogPath` filesystem, per instance+role) and the
raw **bytes-used** trend. Read these as the safety net under the extent trend: a
reclaim stall shows up here as a steady climb toward full. Disk % is the number
to alert on; the bytes trend shows the slope.

### 5c. Media-image recency (slow gauge)

A single stat for the age of the most recent media image. Treat it as a **coarse,
slow gauge**: it only moves on the automatic-image schedule (≥ `IMGINTVL`), so
expect a **long initial "No image / no data"** on a young queue manager, and
expect it to update in steps, not continuously. It is the forward-looking
companion to the reclaim trend — when it advances, extents become releasable.

The live media watermark is also visible in the logger-event feed
(`mediaLogExtentName`, section 5e) and, offline, via `DISPLAY QMSTATUS MEDIALOG`.

### 5d. Collector sample freshness

A stat that reads **✓ fresh**, **⚠ STALE**, or **No data**. This is the honesty
check on the band itself: STALE means a filesystem sample timed out (the extent
and disk numbers may be stale), and No data means the collector is not reporting
at all. Trust the other panels only while this reads fresh.

### 5e. The logger-event log

The bottom panel is the recovery-log **logger events** themselves — one per extent
roll — scoped to `SYSTEM.ADMIN.LOGGER.EVENT`. Each event carries
`currentLogExtentName`, `restartLogExtentName`, `mediaLogExtentName`, and
`logPath`. There is **no** `archiveLogExtentName` — automatic log management has
no archive workflow, so that field simply does not exist here. This panel is the
per-event ground truth behind the trends above: the extent watermarks stepping
forward, and the media watermark (not) advancing.

### 5f. Healthy vs. per-instance divergence

The single most important reading skill: **the active instance and its replicas
do not match, and that is normal.** In the spike, at the same moment, the three
instances of one queue manager showed different `S`/`R` mixes and different extent
counts (e.g. 10 vs 11 extents, with different `S`/`R` splits) and different disk
totals. Each line is tagged by `instance` and `role`, so the active leads and each
replica's divergence is visible as its own line.

- **Healthy divergence:** the lines differ in count and `S`/`R` mix but all trend
  together — each holds its band, each releases extents, each stays well under
  disk. Divergence in *level* is expected; divergence in *behaviour* is the tell.
- **Real trouble:** one instance's `S` count climbs while the others hold; or one
  instance's disk % pulls away toward full; or an instance drops to role
  `unknown` or its sample goes STALE. That is a single-instance problem, not the
  group-wide reclaim story of section 5a.

## 6. The one constraint, and why Native HA makes it moot

Automatic log management buys the hands-off extent lifecycle above, and it has
exactly **one** real cost worth internalising:

> **Automatic log management does not archive log extents, so a queue manager
> using it cannot have a backup queue manager.**

A backup queue manager (`strmqm -r`) is kept current by **replaying archived log
extents** into a standby copy — which requires **manual** log management with an
archiving step to hand those extents over. Automatic log management deliberately
has no archive workflow (confirmed by the absence of any `archiveLogExtentName` in
the logger events, section 5e), so the backup-queue-manager technique is simply
not available on a Native HA queue manager.

**Native HA makes that a non-loss, not a compromise.** The backup queue manager
exists to give you a second, log-shipped copy to recover onto. Native HA already
provides that — and provides it *better* — through **synchronous log replication**
to the replica instances: the redundant copies are live, in quorum, and promote
automatically on failover, rather than a lagging copy you replay by hand. You give
up a technique whose entire purpose Native HA fulfils more strongly. The one thing
to remember is the negative: **do not plan around a backup queue manager for a
Native HA arm** — the replicas are the redundancy.

Other caveats to keep in mind:

- **Media recency is coarse by design** (section 5c) — a long initial pin and
  step-wise updates are the automation working, not a stall.
- **Per-instance numbers differ by design** (section 5f) — never alert on the
  active and a replica being unequal; alert on a single instance *diverging in
  behaviour*.
- **The band is only as fresh as the collector** (section 5d) — a STALE or
  No-data sample invalidates the extent/disk numbers above it until it recovers.

---

## Appendix A: Extent naming, watermarks, and signals reference

**Extent files** (under `LogPath/<QM>/active/`):

| Name | Meaning |
|---|---|
| `S<nnnnnnn>.LOG` | Standard extent, in use — the primary reclaim-health count |
| `R<nnnnnnn>.LOG` | Reserved / recycled extent, available for a future write |
| `amqhlctl.lfh` | Log control file (not an extent) |
| `nativeha.ini` | Native HA config (not an extent) |

**Recovery watermarks** (`DISPLAY QMSTATUS`, and the logger-event fields):

| `QMSTATUS` | Logger-event field | Meaning |
|---|---|---|
| `CURRLOG` | `currentLogExtentName` | The extent currently being written |
| `RECLOG` | `restartLogExtentName` | Oldest extent needed for **restart** recovery |
| `MEDIALOG` | `mediaLogExtentName` | Oldest extent needed for **media** recovery |
| — | `logPath` | The active log directory |
| — | *(no `archiveLogExtentName`)* | Automatic log management has no archive step |

**Error-log signals** (also visible via [JSON diagnostic logging](mq-json-logging-guide.md)):

| Message | Meaning |
|---|---|
| `AMQ7467I` | Oldest log extent required to **start** the queue manager (= `RECLOG`) |
| `AMQ7468I` | Oldest log extent required for **media recovery** (= `MEDIALOG`) |
| `AMQ7490I` | Log extent statistics — **N created. N reused. N deleted.** |

**Band metrics** (emitted by the lab collector, all labelled `{qm,instance,role}`):

| Metric | Reads |
|---|---|
| `mqlab_log_extents_active` | Count of `S…` extents (in use) |
| `mqlab_log_extents_inactive` | Count of `R…` extents (reserved/recycled) |
| `mqlab_log_disk_used_bytes` / `mqlab_log_disk_total_bytes` | `LogPath` filesystem fill |
| `mqlab_log_sample_stale` | `1` when a filesystem sample timed out |

## Appendix D: Troubleshooting

Read the band top to bottom; the first row that is wrong is your fault domain.

| Symptom | Likely cause | Reading |
|---|---|---|
| `S` extent count rises, `reused=0`/`deleted=0`, `MEDIALOG` pinned, queue manager young | No automatic media image has fired yet (`IMGINTVL` not reached) | Expected pre-first-image behaviour — watch for it to stabilise after the first image |
| `S` count keeps climbing long past `IMGINTVL`, media boundary still pinned | Reclaim genuinely stalled (media images not advancing the boundary) | The "automation not reclaiming" pattern — investigate media imaging and disk headroom |
| Log disk % climbing toward full | Downstream of a reclaim stall, or genuine write volume | Cross-check the extent trend (5a) and media recency (5c) |
| Media-image recency shows "No image / no data" for a long time | Young queue manager, or the recency series is not being fed | Coarse gauge — corroborate with `mediaLogExtentName` in the logger-event log |
| One instance diverges in *behaviour* (its `S` climbs while others hold) | A single-instance problem, not the group reclaim story | Scope to that instance+role; check its disk and role |
| A line reads role `unknown`, or the sample stat is `⚠ STALE` / `No data` | Role probe or filesystem sample timed out; collector down | Distrust that instance's numbers until fresh; check `lab-loglifecycle-state` |
| `ALTER QMGR LOGGEREV(ENABLED)` rejected with `AMQ8518E` | Queue manager is circular, not linear/replicated | Expected — `LOGGEREV` is linear-family only; a Native HA arm accepts it |
| `DISPLAY QMGR LOGTYPE` returns `AMQ8405I` | That attribute does not exist | Read `LogType` from `qm.ini`, not `DISPLAY QMGR` |

## Appendix E: References

- **Lab spike (verified specifics):**
  `docs/reports/2026-08-10-nativeha-log-lifecycle-spike.md` (#808) — the live-arm
  evidence for every claim here: log type, extent naming, watermarks, logger-event
  fields, and the reclaim-health behaviour.
- **Epic:** `logical-minds-foundry/.github#145` — Native HA log-lifecycle
  observability (the collector, the cockpit band, and this runbook).
- **IBM MQ 9.4 documentation** — search these topic titles at
  <https://www.ibm.com/docs/en/ibm-mq/9.4>: *Types of logging*, *Managing logs*,
  *Automatic media images*, *Recording media images*, *Logger event generation*,
  *Backing up and restoring IBM MQ queue manager data* (backup queue managers),
  and *Native HA*.
