# HA Fault Drills Under Load — Findings (Ubuntu Pacemaker/SAN arm, #64)

> **Status:** Complete. All four site-A HA drills pass **RPO 0 under
> continuous load** with the DR validation framework as the oracle. This is
> the "HA is supposed to be perfect" half of the thesis, proven on the
> hand-built Ubuntu arm. The client-side requirements these drills forced are
> captured separately in the
> [MQ Client HA/DR Cooperation Contract](../specs/2026-06-09-mq-client-ha-cooperation-contract.md).

## Contents

- [Method](#method)
- [Results](#results)
- [The two detection paths](#the-two-detection-paths)
- [Per-drill detail](#per-drill-detail)
- [Resolution of the Phase D open anomaly](#resolution-of-the-phase-d-open-anomaly)
- [What this does not yet show](#what-this-does-not-yet-show)

## Method

Each drill runs a **continuous persistent + syncpoint flow** through the
cluster VIP (`10.10.1.200`) while a single fault is injected mid-flight:

- **App flow** (`clients/dr_flow.py`, on `app-client`) — a producer puts
  requests at 20 msg/s and a consumer confirms replies, on separate
  reconnectable connections, writing a SENT/CONFIRMED ledger.
- **God's-eye responder** (`clients/dr_responder.py`) — consumes each request
  and replies, writing a RECEIVED/REPLIED ledger that is the external oracle.
- **Verdict** (`clients/dr_baseline.py`) — reconciles the two ledgers through
  the `mqlab.dr` six-bucket classifier and asserts self-correctness
  (god's-eye == app ledger ⇒ RPO 0).

"RPO 0 under load" therefore means: every message the app sent was confirmed,
with zero stranded / lost / ambiguous / duplicated, across the fault — not a
static before/after check on a handful of messages.

## Results

| # | Drill | Fault injected | Detection / recovery mechanism | Messages | Verdict |
|---|---|---|---|---|---|
| HA-1 | Kill QM process | `kill` the QM on the owner | Pacemaker restarts/migrates `mq_qm`; client reconnects | 279 | **RPO 0** |
| HA-2 | Node crash | `virsh destroy` the owner | `fence_virsh` fences the dead node, `mq_group` migrates to a survivor; client reconnects | 1125 | **RPO 0** |
| HA-3 | Heartbeat sever (controlled) | `domif-setlink` the owner's `net-hb-a` down | quorum loss → controlled `endmqm -w` → **client reconnect loop** rebuilds to the survivor | 1039 | **RPO 0** |
| HA-4 | SAN sever | `domif-setlink` the owner's `net-san-a` down | `mq_fs` `OCF_CHECK_LEVEL=20` probe times out → owner fenced → migrate to a storage-healthy node | 1350 | **RPO 0** |

All four show zero in every loss/ambiguity/duplication bucket and pass the
self-correctness assertion.

## The two detection paths

The drills deliberately exercise **two structurally different** ways the
cluster decides to fail over, because a client that survives one can still fail
the other:

- **Quorum-driven** (HA-1, HA-2, HA-3) — the node or QM is lost/partitioned and
  Pacemaker acts via fencing or a controlled stop.
- **Storage-driven** (HA-4) — the SAN under the owner dies while the node stays
  up and in-quorum; only the hardened `mq_fs` monitor (`OCF_CHECK_LEVEL=20`,
  `on-fail=fence`) catches it. With the default monitor this is a ~12-minute
  blind spot (Phase D drill 4a); the hardened monitor closes it to ~one
  interval, now proven at scale (1350 msgs, RPO 0).

HA-3 is the sharpest client finding: a controlled `endmqm -w` disconnects
clients **non-reconnectably**, so `MQCNO_RECONNECT` alone is not enough — the
client's explicit reconnect loop (contract requirement #9) is what carries the
flow to the survivor. Validated without the `no-quorum-policy=suicide` crutch:
the cooperative client rides a genuine controlled quiesce.

## Per-drill detail

**HA-1 — kill QM (279 msgs, RPO 0).** The lightest fault: Pacemaker's monitor
notices the dead `mq_qm` and restarts it in place (or migrates it). The
reconnect-enabled clients ride the brief outage.

**HA-2 — node crash (1125 msgs, RPO 0).** The hardest classic drill. A hard
`virsh destroy` of the owner is an abrupt break; `fence_virsh` confirms the
node down, Pacemaker migrates `mq_group` to a survivor, the VIP follows, and
auto-reconnect re-establishes the flow. Resource stickiness prevents an
auto-fail-back that would otherwise disconnect reconnected clients on the
controlled move home.

**HA-3 — heartbeat sever, controlled (1039 msgs, RPO 0).** The owner loses
quorum and Pacemaker stops its QM with `endmqm -w`. This is *not* an abrupt
break: the client sees the quiescing family (`2161`/`2202`) and then
`2009 MQRC_CONNECTION_BROKEN`, and auto-reconnect does **not** engage. The
client's reconnect loop rebuilds the connection, the producer resends the
in-flight message under the same business key, and the flow continues on the
survivor — zero loss.

**HA-4 — SAN sever (1350 msgs, RPO 0).** The owner's iSCSI path to `san-a` is
cut while heartbeat/quorum stay up. `/mqshared` I/O hangs; the `mq_fs`
`OCF_CHECK_LEVEL=20` monitor (a real read/write probe, not a mount-table check)
times out; `on-fail=fence` fences the owner; the group relocates to a survivor
that still has its iSCSI session. The keepalive settings (contract #7) let the
clients' blocked calls detect the fenced peer in ~30 s and reconnect.

## Resolution of the Phase D open anomaly

Phase D flagged an open anomaly (drill 4a): during the undetected SAN-failure
window, a persistent put returned success but the message was absent after
recovery. Two things from #64 bear on it:

1. **The hardened monitor removes the window** that produced it — HA-4 reran
   the same storage fault under load at 1350 msgs with zero loss.
2. **The framework would now catch it** rather than leave it a question mark:
   a "success but not on the queue" outcome is exactly the in-doubt /
   lost-unprocessed class the six-bucket classifier and the god's-eye
   reconciliation are built to attribute. It is no longer an unexplained
   anomaly but a named bucket the drills measure.

## What this does not yet show

This report covers **HA** (RPO 0 is the goal and the result). It does **not**
cover **forced DR**, where RPO is expected to be **non-zero**: DRBD protocol-A
(async) replication means messages committed at site A but not yet shipped to
site B are genuinely lost on a forced cutover. Quantifying that gap — the loss
census on an abrupt full-site-A loss with a force-promote of `san-b` — is the
next phase and its own issue. The framework and the cooperative client proven
here are the instruments that phase will use.
