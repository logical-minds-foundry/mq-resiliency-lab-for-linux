# Native HA Log-Lifecycle Spike — Findings (GATING)

**Date:** 2026-08-10
**Issue:** #808 (epic logical-minds-foundry/.github#145 — Native HA log-lifecycle observability)
**Task:** Epic plan Task 1 — the GATING spike. On a live Native HA arm, answer S1–S4 so
Tasks 2–4 build on verified behaviour, not assumptions. Findings are applied forward.

**Status:** DECIDED — executed against a live `nativeha-rhel` arm (QM `NHARAPP`), 2026-08-10.

## Questions this spike must answer

| ID | Question | Gates |
|----|----------|-------|
| S1 | Is `ALTER QMGR LOGGEREV(ENABLED)` **accepted** (`AMQ8005I`) on a replicated-log QM? | Task 2 (declare-and-verify enable) |
| S2 | Which logger events fire under automatic log management, and what extent watermarks do they carry? | Task 3/4 event element |
| S3 | Which log-health signals are pollable **without MQI** vs. only via the event stream? | Task 3 collector scope; Task 4 media-image |
| S4 | Is `LOGGEREV(DISABLED)` **accepted** on a circular QM (explicit negative) or **rejected** (verify-and-omit)? | Task 2 circular branch |

## Decision

**All four load-bearing assumptions are confirmed — the epic's architecture holds, with three
concrete corrections to the plan's guessed mechanics.**

1. **S1 — `LOGGEREV(ENABLED)` is accepted on the Native HA replicated-log QM** (`AMQ8005I`).
   Task 2's enable branch is valid.
2. **S2 — logger events DO fire, one per new-extent write, and ride the existing
   `mq-event-monitor` amqsevt → journald pipeline** (Channel A works). Each event carries
   `currentLogExtentName`, `restartLogExtentName`, `mediaLogExtentName`, `logPath` — but **no
   `archiveLogExtentName`** (automatic log management has no ARCHLOG).
3. **S3 — the filesystem signals are richer than the plan assumed** and diverge active-vs-replica;
   the real log path is `/var/mqm/log/NHARAPP/active/`, extents are `S…`/`R…`.LOG, and
   `AMQ7467I/AMQ7468I/AMQ7490I` in the QM error log carry the same watermarks + created/reused/
   deleted counters.
4. **S4 — on a circular QM, `LOGGEREV(ENABLED)` is rejected (`AMQ8518E`) but `LOGGEREV(DISABLED)`
   is accepted (`AMQ8005I`).** Task 2's circular branch may carry an **explicit**
   `LOGGEREV(DISABLED)` verified negative; it is accepted (a no-op), not an error.

### Corrections to the plan's guessed mechanics (applied forward)

- **`DISPLAY QMGR LOGTYPE` does not exist** (`AMQ8405I` syntax error). Task 2's declare-and-verify
  must read `LogType` from **`qm.ini`** (the plan's named fallback), not `DISPLAY QMGR`.
- **The log path is `/var/mqm/log/<QM>/active/`**, not `/var/mqm/qmgrs/<QM>/active`
  (that path is a small control *file*, not the extent directory).
- **`rcdmqimg -t all` is invalid** — valid `-t` types are `qlocal/qalias/qremote/topic/qmgr/…`;
  there is no `all`. Any playbook step that records a media image must enumerate types.

## Environment

- Stack: `nativeha-rhel` (`short=NHAR`, mechanism `native-ha`, RHEL 9.6, MQ **9.4.5.0**).
- Cluster: 3-node quorum, site A — `nha-rhel-a1/a2/a3`; DR site B — `nha-rhel-b1/b2/b3`.
- QM: **`NHARAPP`**; active instance **`nha-rhel-a2`** (a1/a3 = Replica) at spike time.
- `qm.ini` `Log:` stanza: `LogType=REPLICATED`, `LogManagement=Automatic`,
  `LogPath=/var/mqm/log/NHARAPP/`, `LogPrimaryFiles=10`, `LogSecondaryFiles=10`,
  `LogFilePages=8192` (⇒ 32 MB/extent).

## Evidence

### S1 — `LOGGEREV(ENABLED)` on the replicated-log QM — ACCEPTED

On the active instance (`nha-rhel-a2`), via `runmqsc NHARAPP`:

```
     2 : ALTER QMGR LOGGEREV(ENABLED)
AMQ8005I: IBM MQ queue manager changed.
     3 : DISPLAY QMGR LOGGEREV
AMQ8408I: Display Queue Manager details.
   QMNAME(NHARAPP)                         LOGGEREV(ENABLED)
```

Secondary finding — `DISPLAY QMGR LOGTYPE` is **not** a valid attribute:

```
     1 : DISPLAY QMGR LOGTYPE ...
AMQ8405I: Syntax error detected at or near end of command segment below:-
DISPLAY QMGR LOGTYPE
```

Log type is instead authoritative in `qm.ini` (`LogType=REPLICATED`, `LogManagement=Automatic`).

### S2 — logger events under automatic log management

**The event queue reads `CURDEPTH(0)` — but that is a false negative.** The lab's
`mq-event-monitor` runs `amqsevt -m NHARAPP -o json_compact` (PID under
`mqmonitor@NHARAPP.service`) with the logger event queue open for input
(`SYSTEM.ADMIN.LOGGER.EVENT` shows `IPPROCS(1)`), so it **destructively drains** every event in
real time to journald. Driving persistent-message churn to roll extents produced a logger event
**per new-extent write**, captured in `journalctl -u mqmonitor@NHARAPP.service`:

```json
{ "eventSource": { "objectName": "SYSTEM.ADMIN.LOGGER.EVENT", "objectType": "Queue", "queueMgr": "NHARAPP" },
  "eventType":   { "name": "Logger Event",  "value": 91 },
  "eventReason": { "name": "Logger Status", "value": 2411 },
  "eventCreation": { "timeStamp": "2026-08-10T15:28:46Z", "epoch": 1786375726 },
  "eventData": {
    "queueMgrName": "NHARAPP ... \u0003",
    "currentLogExtentName": "S0000012.LOG",
    "restartLogExtentName": "S0000011.LOG",
    "mediaLogExtentName":   "S0000000.LOG",
    "logPath": "/var/mqm/log/NHARAPP/active/" } }
```

- One event per extent roll; `currentLogExtentName` steps `S0000001 → S0000002 → … → S0000012`,
  `restartLogExtentName` trails it by one.
- **No `archiveLogExtentName`** — automatic log management has no archive workflow. Per IBM's
  *Logger event generation* doc, logger events fire when LOGGEREV is ENABLED and the QM *starts
  writing to a new log extent*, when the QM starts, and when LOGGEREV flips DISABLED→ENABLED; the
  ARCHLOG-driven case does not apply here.
  (<https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=events-logger-event-generation>)
- **Parser gotcha:** `queueMgrName` carries a trailing `\u0003` (ETX) control byte — the collector
  must strip/trim it.

**Parallel signal — the QM error log.** The same watermarks (and lifecycle *counters*) are written
to `AMQERR01.LOG` regardless of the event consumer:

```
AMQ7467I: The oldest log file required to start queue manager NHARAPP is S0000011.LOG.
AMQ7468I: The oldest log file required to perform media recovery of queue manager NHARAPP is S0000000.LOG.
AMQ7490I: Log extent statistics: 1 created. 8 reused. 0 deleted.
```

`AMQ7490I` (created/reused/deleted counters) is a strong, already-ingested log-lifecycle signal.

**Authoritative watermark source — `DISPLAY QMSTATUS`.** All three watermarks are available
directly via `runmqsc` (cleaner than parsing the error log), matching the logger-event fields:

```
CURRLOG(S0000057.LOG)   RECLOG(S0000056.LOG)   MEDIALOG(S0000000.LOG)
```

`CURRLOG` = current extent, `RECLOG` = oldest needed for **restart** recovery, `MEDIALOG` = oldest
needed for **media** recovery. (This is an MQI/`runmqsc` call, not a filesystem poll — the non-MQI
collector uses the filesystem; the watermarks come from Channel A events or, offline, `QMSTATUS`.)

**Media images & reclaim — the headline automation-health finding.** Automatic media imaging is
**on by default** (`IMGSCHED(AUTO)`, `IMGINTVL(60)` min, `IMGLOGLN(3271)` MB, `IMGRCOVO/Q(YES)`).
But **within the spike window `MEDIALOG` never advanced from `S0000000.LOG`** — so automatic log
management **reclaimed nothing**: `AMQ7490I` tallies **55 created, 0 reused, 0 deleted**, and the
`active/` extent count grew monotonically to **66** (`S0000000…S0000065`). `RECLOG` tracked
`CURRLOG` (restart recovery is healthy); only `MEDIALOG` lagged. Manual `rcdmqimg` across all
recoverable types did **not** advance `MEDIALOG`.

- **Data:** at ≈30 min of QM life (`IMGINTVL(60)` not yet reached, so no *automatic* media image
  had fired), media recovery still pinned extent 0 and the log grew without reuse/delete.
- **Judgment (to verify, not asserted as a defect):** this is consistent with expected early-life
  behaviour *before the first automatic media image* rather than a fault — but it is **precisely the
  signal the epic's cockpit must surface**: a rising extent count with `reused=0/deleted=0` and a
  pinned `MEDIALOG` is the "watch the automation" pattern. **Task 5's live-lab playbook should
  assert that reclaim actually occurs** (extent count stabilises / `MEDIALOG` advances) once
  automatic media imaging runs — that assertion is out of this spike's gating scope but is the
  natural next proof.

### S3 — non-MQI log-health signals per instance

Log path `/var/mqm/log/NHARAPP/` contains `active/` (the extents), `amqhlctl.lfh` (log control),
`nativeha.ini`. The `active/` extent files and disk are fully pollable without MQI:

| Instance | Role | `active/` extents (sample) | `du -sm` |
|----------|------|----------------------------|----------|
| nha-rhel-a1 | Replica | `S0000000…S0000009` (10) | 321 MB |
| nha-rhel-a2 | Active  | `S0000000…S0000009` + `R0000009` (11) | 353 MB |
| nha-rhel-a3 | Replica | `R0000001…R0000009` + `S0000000…S0000001` (11) | 353 MB |

- Extent naming: `S…`.LOG (standard) and `R…`.LOG (reserved/recycled); count both.
- **Active vs. replica genuinely diverge** in the S/R mix and count — the collector must tag by
  instance + role (`dspmq -m NHARAPP -o nativeha -x` gives the role).
- Disk: LogPath is on `/` (`/dev/vda3`); `df -P /var/mqm/log/NHARAPP` → used/total,
  `du -sm /var/mqm/log/NHARAPP` → MB.

### S4 — the circular negative

On a throwaway default (circular) QM (`crtmqm` with no `-ll/-lr` ⇒ `LogType=CIRCULAR`):

```
     1 : DISPLAY QMGR LOGGEREV
   QMNAME(SPKCIRC)                         LOGGEREV(DISABLED)
     2 : ALTER QMGR LOGGEREV(ENABLED)
AMQ8518E: LOGGEREV is only valid when using a linear logging queue manager.
     3 : ALTER QMGR LOGGEREV(DISABLED)
AMQ8005I: IBM MQ queue manager changed.
```

`ENABLED` → **rejected** (`AMQ8518E`, confirms #721); `DISABLED` → **accepted** (`AMQ8005I`, no-op).

## What each finding pins down (applied forward)

- **Task 2 (declare-and-verify LOGGEREV).** Enable on replicated arms is valid (S1). Verify
  declared-vs-actual by reading `qm.ini` `LogType` — **not** `DISPLAY QMGR LOGTYPE`, which does not
  exist. The circular else-branch may set an **explicit** `LOGGEREV(DISABLED)` (S4: accepted), or
  equivalently verify-and-omit; either is correct. Update the #721 note to the resolved behaviour.
- **Task 3 (collector).** Poll `/var/mqm/log/<QM>/active/` for `S…`/`R…`.LOG counts (both), `df`/`du`
  on the LogPath filesystem, role via `dspmq -o nativeha -x`; emit per-instance+role (active/replica
  diverge). **The extent count itself is the primary reclaim-health signal** — a monotonic rise with
  no reuse is the "automation not reclaiming" pattern. `AMQ7490I` created/reused/deleted counters are
  an additional non-MQI signal from the error log; `DISPLAY QMSTATUS CURRLOG RECLOG MEDIALOG` is the
  authoritative (MQI) watermark source if ever needed offline.
- **Task 4 (cockpit panel).** Logger events are live on Channel A (amqsevt → journald) with
  `current`/`restart`/`mediaLogExtentName` + `logPath`; strip the `\u0003` on `queueMgrName`. Lead the
  band with **extent-count trend + created/reused/deleted** (the reclaim-health story), not a
  point-in-time tile. The media-image-recency element has a real source (`mediaLogExtentName` /
  `MEDIALOG` / `AMQ7468I` / `IMGSCHED AUTO`) but it is **coarse-grained** — it moves on the
  automatic-image schedule (≥ `IMGINTVL`), so treat it as a slow gauge, and expect a long initial
  pin at the first extent on a young QM.
