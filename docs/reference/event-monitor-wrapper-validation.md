# Validating the resilient event-monitor wrapper

A repeatable procedure to prove the self-healing `amqsevt` event-monitor wrapper
(epic `logical-minds-foundry/.github#122`) behaves correctly on a live lab queue
manager. It pairs with the harness `tools/validate-event-monitor-wrapper.sh`,
which mechanises the checks below and is designed to be handed to an AI lab agent
or run by a human. Engineering background is in
`docs/reports/2026-07-21-mq-event-monitor-wrapper-resilience.md`.

## What it proves (the B-matrix)

| # | Scenario | Expected |
|---|---|---|
| **B1** | Normal start + checkpoint | `run.sh` (under `setsid`) and `amqsevt` are up; the SERVICE `PID` equals the wrapper's PID equals its PGID (`setsid` gave the wrapper its own group); JSON events are reaching journald. |
| **B2** | Clean stop | `STOP SERVICE` reaps **both** the wrapper and `amqsevt` — no orphaned process, service goes away. This is the guarantee the old `exec` model had and the new design must keep. |
| **B3** | Crash recovery | `kill -9` on `amqsevt` is recovered: the wrapper logs the exit, sleeps, and restarts it, retrying through the queue manager's ~29 s exclusive-handle reap window (each retry logs `MQRC_OBJECT_IN_USE [2042]`) until it succeeds. |

**On the 2042 evidence (A1 branch).** B3's 2042 retries appear because `amqsevt`
opens the event queues `AS_Q_DEF` and `SYSTEM.ADMIN.*.EVENT` is `DEFSOPT(EXCL)`,
so a fast restart collides with the dead exclusive handle. That is the expected
configuration (event ordering wants exclusive input). If a site ever made those
queues `DEFSOPT(SHARED)`, 2042 would not arise and B3 would show a clean immediate
restart instead — still a pass.

## Prerequisites

- The resilient wrapper is **deployed** on the target queue manager (`run.sh` in
  `/opt/mq-event-monitor/`, and the SERVICE defined with the `setsid` STARTCMD and the
  inline group-kill STOPCMD — `STOPCMD('/bin/kill')` /
  `STOPARG('-TERM -- -+MQ_SERVER_PID+')`; there is no separate `stop.sh` since `#786`).
  A cold rebuild (`#763`) is the authoritative way to get there; `dspmqver`-style drift
  is not enough.
- The lab is up and the target QM is running. The harness **disrupts** the
  collector (it `STOP`/`START`s the service and `kill -9`s `amqsevt`), then leaves
  it running healthy — safe on a lab QM, not something to run against a QM you
  cannot perturb.

## Step 1 — find the active node

The collector runs on whichever node the QM is **active** on (`CONTROL(QMGR)`
travels with the QM). Resolve it per arm:

| Arm | Find the active node |
|---|---|
| Native HA (rhel/ubuntu) | `dspmq -m <QM> -o nativeha -x` on any node → the `INSTANCE(<node>) ROLE(Active)` line |
| RDQM | `rdqmstatus -m <QM>` → the node reporting `Running` / primary |
| Pacemaker | `pcs status` (or `crm_mon`) → the resource owner |
| Standalone (e.g. `SVCQM`) | the single host |

## Step 2 — run the harness on the active node

From the repo's `ansible/` directory, ship the harness to the active node and run
it (root, so it can drive `runmqsc` as `mqm` and `kill -9`):

```bash
cd ansible
uv run --project .. ansible <active-node> -b -m script \
  -a "../tools/validate-event-monitor-wrapper.sh <QM>"
```

(Or copy `tools/validate-event-monitor-wrapper.sh` onto the node and run
`./validate-event-monitor-wrapper.sh <QM>` as root.) The service name defaults to
`MQ.EVENT.MONITOR`; pass a second argument to override.

The harness prints one `PASS`/`FAIL` line per check, the `run.sh` journald
lifecycle lines (the fail-retry-succeed evidence), a final `== summary: ALL
SCENARIOS PASS ==`, and exits non-zero if any scenario failed.

## Step 3 — interpret and capture

- **All three scenarios must `PASS`** and the summary must read `ALL SCENARIOS
  PASS` (exit 0). Any `FAIL` is a real regression — do not wave it through.
- The B3 journald block should show `MQOPEN of 'SYSTEM.ADMIN.*.EVENT' ended with
  reason code MQRC_OBJECT_IN_USE [2042]` a few times, then a clean run — that
  fail-twice-then-succeed heartbeat is the intended, visible sign the retry path
  is alive.
- **Capture the full stdout** into
  `docs/reports/assets/mq-event-monitor-wrapper/` (e.g. redirect the ansible
  output to a file) and reference it from the report's B-matrix section — that is
  the evidence the internal report is built on.

## Notes

- The harness is **idempotent in effect**: it stops, starts, and crashes the
  collector, then leaves it running. Re-running it is safe.
- Because the harness cycles the service quickly, its own restarts can briefly hit
  the same 2042 window — that is expected and self-heals, exactly as B3 asserts.
