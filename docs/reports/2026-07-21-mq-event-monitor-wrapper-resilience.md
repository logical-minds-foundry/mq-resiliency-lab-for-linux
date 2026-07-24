# Resilient `amqsevt` event-monitor wrapper — engineering report

- **Epic:** `logical-minds-foundry/.github#122`
- **Research task:** `logical-minds-foundry/mq-resiliency-lab-for-linux#760` (T1, A0–A3)
- **Design:** `.github` `epics/122-event-monitor-wrapper/{spec,plan}.md`
- **Lab arm used:** nativeha-ubuntu, QM `NHAUAPP` (active instance varies per run —
  A0–A3 on `nha-ubuntu-a3`, B1–B3 on `nha-ubuntu-a2`)
- **Date:** 2026-07-21 (research); B-matrix re-validated 2026-07-24
- **Status:** complete — research findings (A0–A3, T1) proven, and the B-matrix (B1–B3)
  validated live against the **simplified** wrapper (no `stop.sh`; inline group-kill) by
  T7 (`#785`). Evidence: `assets/mq-event-monitor-wrapper/b1b3-t7-nativeha-ubuntu.txt`.

## Purpose

Characterise, before building anything, the three things the resilient wrapper's
design turns on: (A0) how MQ spawns the SERVICE `STARTCMD` process — which decides
whether `setsid` can give us a clean process group; (A1) how `amqsevt` opens the
event queues, handles signals, and exits — which decides whether a stale handle /
2042 can arise and whether a clean stop closes handles; and (A3) how long the queue
manager takes to reap a stale exclusive handle — which sizes the restart policy.
Discipline throughout: **read and understand first, then prove in the lab.**

The evidence files referenced below live in
`docs/reports/assets/mq-event-monitor-wrapper/`. The `amqsevt` sample source
(`amqsevta.c`, 3411 lines) was pulled off a lab box into the shared build tree
(`build/cache/amqsevt/amqsevta.c`) for inspection.

## A0 — MQ SERVICE spawn topology → the `setsid` decision

Process table of the running `#114` collector on the active node
(`assets/…/a0-topology.txt`):

```text
  PID  PPID  PGID   SID  USER  ARGS
 5455  5441  5455  5455  mqm   /opt/mqm/bin/amqzmgr0 -m NHAUAPP
 5513  5455  5455  5455  mqm   /opt/mqm/bin/amqpcsea NHAUAPP
 5562  5455  5455  5455  mqm   /opt/mqm/samp/bin/amqsevt -m NHAUAPP -o json_compact
```

**Finding.** `amqsevt` (PID 5562) has `PGID 5455 ≠ its own PID` — it is **not** a
process-group leader. Its parent, `amqzmgr0` (PID 5455, the MQ service/process
manager), is itself the session+group leader (`PGID = SID = PID = 5455`). So MQ
starts the SERVICE `STARTCMD` as a **child inside `amqzmgr0`'s process group**, not
as its own group leader.

**Consequence (the mechanism is viable).** Because the STARTCMD process is *not*
already a group leader, `setsid -w -- run.sh` will call `setsid(2)` **in place
(no fork)** and `exec` `run.sh`, which then becomes a **new session/group leader**
with `PGID == PID == MQ_SERVER_PID`, with `amqsevt` as its child in that group.
`STOPCMD` = `kill -TERM -MQ_SERVER_PID` (negative PID) then targets exactly our
group — the wrapper and `amqsevt` — and nothing else. No Python fallback is
required (to be confirmed end-to-end at B2 with the wrapper deployed).

This also explains why the **current** `#114` design must use *positive*
`kill MQ_SERVER_PID`: today `amqsevt` shares `amqzmgr0`'s group (5455), so a
negative-PID kill of that group would signal `amqzmgr0` and its other children.
The dedicated group `setsid` creates is precisely what makes the group-kill safe.

`setsid` on the lab OS: `util-linux 2.39.3` at `/usr/bin/setsid`; `--` and `-w`
both honoured (`setsid -w -- /bin/echo …` succeeds).

## A1 — `amqsevt` source (`amqsevta.c`) + live corroboration

**Queue open mode — exclusive (via the queue default).** The event queues are
opened with `MQOO_INPUT_AS_Q_DEF | MQOO_FAIL_IF_QUIESCING` (`amqsevta.c:584`), so
the actual share mode is inherited from the queue's `DEFSOPT`. Live check
(`assets/…/a1-openmode.txt`): `SYSTEM.ADMIN.QMGR.EVENT` is
`DEFSOPT(EXCL)` / `DEFTYPE(PREDEFINED)`, and the running handle is held by
`APPLTAG(amqsevt)`. IBM's 2042 doc confirms the rule
(`AS_Q_DEF` + non-`SHARED` default ⇒ exclusive input). **So `amqsevt` holds the
event queue for EXCLUSIVE input** — a second opener (a racing restart) gets 2042.
This is the desired mode: event ordering matters, and exclusive input prevents a
second reader interleaving.

**Signal handling — none.** There are no `signal()` / `sigaction()` calls anywhere
in the source. `amqsevt` runs on default signal dispositions.

**Clean shutdown vs signal death.** The clean exit path (`amqsevta.c:1008` `MOD_EXIT`)
does `MQCLOSE` every handle then `MQDISC` — but it is reached **only** via
`WaitForEnd()`, which returns when the async consumer callback receives an
`MQCBCT_EVENT_CALL` with a terminating reason — `MQRC_Q_MGR_QUIESCING`,
`MQRC_CONNECTION_BROKEN`, `MQRC_Q_MGR_STOPPING`, etc. (`amqsevta.c:517-529`, setting
the `EndProgram` flag). In other words: a **queue-manager quiesce closes handles
cleanly**, but a **signal kills the process before `MOD_EXIT`**, so no `MQCLOSE`
runs and the handle is left for the QM to reap. There is no signal that closes
handles gracefully.

**2042 on open ⇒ exit.** An `MQOPEN` failure that is not `MQRC_UNKNOWN_OBJECT_NAME`
(missing queue, tolerated) prints the reason and `goto MOD_EXIT`
(`amqsevta.c:885-903`); the program returns the MQ reason code as its exit status
(`return((int)Reason)`, `:1086`). So a restart that races a not-yet-reaped handle
gets 2042, and `amqsevt` **exits non-zero** — which the wrapper's loop treats as any
other failed run and retries.

**Non-transactional, and bad-message behaviour.** The consumer uses
`MQGMO_NO_SYNCPOINT` (`amqsevta.c:917`) — destructive get outside syncpoint, so a
message is removed before/while it is processed. A message whose format is not a
recognised event is dumped as raw bytes and processing continues
(`amqsevta.c:487-498`) — so *unrecognised* messages do not crash it. But the PCF
parameter-walking trusts the structure lengths/counts, so a genuinely **corrupt**
PCF can over-read and crash the process, and because the get is non-transactional
that message is then lost. This is precisely the failure the wrapper exists to
recover from, and confirms poison-message handling is correctly out of scope
(there is no transaction to roll back).

## A2 — exit & handle behaviour under signals

Experiment (`assets/…/a2a3-signals-reap.txt`): with the collector running and
holding the handle, send a signal to `amqsevt` and observe the event-queue handle
1s later.

| Signal | `amqsevt` after | Event-queue handle at t=1s |
|---|---|---|
| `SIGKILL (9)` | gone | **left open (stale)** |
| `SIGTERM (15)` | gone | **left open (stale)** |

`SIGTERM` behaves **identically** to `SIGKILL` — both leave the exclusive handle
stale — exactly as the A1 source reading predicts (no handler ⇒ no `MQCLOSE` on any
signal). Practical consequence for our design: the wrapper's `STOPCMD` (`SIGTERM`
to the group) does **not** make `amqsevt` close its handle gracefully; it relies on
the QM reaping the connection. "Clean stop" therefore means *no orphaned process*
(both the wrapper and `amqsevt` reaped), not a graceful `MQCLOSE` — and a brief
stale handle after a stop is normal and self-heals.

## A3 — stale-handle reap timing

**Literature first.** IBM's `MQRC_OBJECT_IN_USE` (2042) doc
(`build/refs/ibm-docs/ibm-mq/9.4.x/codes-2042-07fa-rc2042-mqrc-object-in-use`;
source: <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=codes-2042-07fa-rc2042-mqrc-object-in-use>)
explains 2042 as an open-options conflict (a request for exclusive input when the
object is already open for input), confirms the `AS_Q_DEF`/`DEFSOPT` rule, and — key
— states the programmer response: **"System design should specify whether an
application is to wait and retry, or take other action."** IBM does **not** publish
a reap *time*; that is an implementation detail to measure.

**Measured.** Kill `amqsevt` and poll `DISPLAY QSTATUS(SYSTEM.ADMIN.QMGR.EVENT)
TYPE(HANDLE)` until the stale handle disappears (`assets/…/a2a3-signals-reap.txt`):

| Trial | Signal | Reap time |
|---|---|---|
| 1 | `SIGKILL` | **28 s** |
| 2 | `SIGKILL` | **29 s** |
| 3 | `SIGTERM` | **29 s** |

The reap is strikingly consistent at **~28–29 s**, which suggests a periodic
queue-manager housekeeping sweep (~30 s) rather than immediate detection of the
dead local connection. That means the worst case ≈ one full sweep interval (~30 s)
and the best case (killed just before a sweep) is near-zero — non-deterministic in
timing, bounded around a ~30 s sweep on this build.

## Implications for the wrapper design (T2)

1. **Mechanism confirmed.** `setsid -w -- run.sh` + a negative-PID group stop is sound on
   this platform (A0); build it as the primary, not the fallback. The proven form is
   `STOPCMD('/bin/kill')` + `STOPARG('-TERM -- -+MQ_SERVER_PID+')`: MQ substitutes the
   `+MQ_SERVER_PID+` insert (the group-leader pid, since `setsid` makes `run.sh` the leader)
   **even when embedded** in a larger argument, so it expands to `kill -TERM -- -<pid>` and the
   *negative* pid signals the whole group; `--` is for procps-ng `/bin/kill` (so `-<pgid>` reads
   as a group, not an option). (A T2 diagnostic that dropped the `+` delimiters wrongly concluded
   MQ does not substitute the insert, and briefly led to an over-built `stop.sh`+`pgrep` helper;
   that was corrected and the helper deleted — see T6/`#786`. The inline group-kill does the whole
   job.)
2. **2042 is real, not hypothetical.** Exclusive open (A1) + a ~29 s reap (A3)
   means a restart-after-crash **will** hit 2042 until the QM reaps — the retry is
   load-bearing, exactly as the spec argues.
3. **Revisit the sleep default (decision for T2).** The measured reap (~29 s) is
   **longer than the planned ~10 s sleep**. With `mq_event_sleep_secs = 10` a crash
   restart retries roughly three times (logging 2042 each time) before the ~29 s
   window clears — functionally correct but noisy. Options for T2: (a) keep 10 s and
   accept ~3 logged 2042 retries per crash; or (b) raise the sleep toward ~20–30 s so
   a single retry usually clears the window. Recommend deciding this in T2 with the
   ~29 s figure in hand; the bounded open-retry (`mq_event_open_retry_secs`) is
   independent and still wanted for a genuinely-held queue.
4. **"Clean stop" ≠ graceful `MQCLOSE`.** B2 should assert *no orphan* + SERVICE
   `STOPPED`, and expect a brief stale handle to be reaped by the QM — it must not
   assert an immediate handle close, which `amqsevt` never does on a signal (A2).

## Validation matrix (B1–B3)

The B-matrix is mechanised by `tools/validate-event-monitor-wrapper.sh` and
documented in `docs/reference/event-monitor-wrapper-validation.md`. It is run on
the active node of the target QM and captures the evidence below. The authoritative
run is **T7 (`#785`)**: the B-matrix re-run against the *simplified* wrapper — after
T6/`#786` deleted `stop.sh` and moved the stop inline — on the stable nativeha-ubuntu
stack (NHAUAPP, active `nha-ubuntu-a2`, 2026-07-24). All three scenarios PASS; the full
capture is `assets/mq-event-monitor-wrapper/b1b3-t7-nativeha-ubuntu.txt`.

### B1 — normal start + checkpoint

**PASS.** Wrapper (`run.sh` under `setsid`) and `amqsevt` both up; the SERVICE `PID`
equals the wrapper's PID equals its PGID — one process group led by `run.sh`:

```text
  PID   PPID  PGID  ARGS
39872   5957 39872  /bin/bash /opt/mq-event-monitor/run.sh NHAUAPP
39942  39872 39872  /opt/mqm/samp/bin/amqsevt -m NHAUAPP -o json_compact
service_PID=39872  wrapper_PID=39872  wrapper_PGID=39872  amqsevt=39942
```

`amqsevt` (39942) is a child inside the wrapper's group (PGID 39872), and 20 JSON
events were already in journald. **Active-only:** a clean `ps` across the raft trio
shows the wrapper + `amqsevt` **only** on the active instance (`nha-ubuntu-a2`);
replicas `a1`/`a3` carry neither — the SERVICE (`CONTROL(QMGR)`) starts on the active
QM instance alone, as Native HA requires. (A bare `pgrep -f 'run.sh NHAUAPP'` *appears*
to match on the replicas, but that is the shell probe self-matching its own argv; the
clean `ps` that excludes the `sh -c` wrapper is authoritative.)

### B2 — clean stop (no orphan)

**PASS.** `STOP SERVICE` (`AMQ8732I`) reaped **both** the wrapper and `amqsevt` — no
orphan, service gone — via the inline `STOPARG('-TERM -- -+MQ_SERVER_PID+')` group-kill
alone (no `stop.sh`). This is the same no-orphan guarantee the old `exec` model gave,
now preserved by the group stop. Note (per A2) that "clean" here means *no orphan*, not
a graceful `MQCLOSE` (`amqsevt` has no signal handler, so the QM reaps its connection);
a brief stale handle after the stop is normal and self-heals.

### B3 — crash recovery through the 2042 window

**PASS.** `kill -9` on `amqsevt` (66238) → the wrapper logged the exit, slept ~10 s, and
relaunched it (→ 66473), retrying through the exclusive-handle reap window — each racing
restart logging `MQRC_OBJECT_IN_USE [2042]` (per A1's exclusive-open finding) until the
handle cleared and the collector stuck. The journald trail is the deliberate
fail-retry-succeed heartbeat:

```text
run.sh[66167]: amqsevt exited rc=0 after 0s [... MQRC_OBJECT_IN_USE [2042]]; restarting in 10s
run.sh[66167]: amqsevt exited rc=137 after 3s (fast); restarting in 10s
run.sh[66167]: amqsevt exited rc=0 after 0s [... MQRC_OBJECT_IN_USE [2042]]; restarting in 10s
```

The lab was left healthy: service running only on the active node, raft
`INSYNC`/`QUORUM(3/3)`. Full capture (with the active-only `ps` and final state):
`assets/mq-event-monitor-wrapper/b1b3-t7-nativeha-ubuntu.txt`. The earlier T2 smoke
(`assets/mq-event-monitor-wrapper/b2b3-smoke.txt`) is superseded by this T7 run.
