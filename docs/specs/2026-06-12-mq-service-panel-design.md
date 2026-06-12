# MQ Service panel (Layer 2) — design

**Status:** starting-point design (brainstormed). The *structure* below is settled;
the exact metric / channel / queue specifics are **tuned after the exporter spike**
(see Prerequisites). Consolidates #131 (live-node owner flag) and #132 (service
health) into one panel.

## Purpose

Fill the reserved **"MQ Service · Layer 2"** dashboard row with a compact, glass-box
view of the lab's queue managers *as services* — not just where they run. You should
be able to see every QM at once, watch messages move, watch the links between QMs
toggle through retry states, and watch queues fill and drain — the live picture a
human needs to drive and observe HA/DR drills (#119).

## Queue managers (current names)

Keyed by **current** QM name — unique enough today; genericization is deferred to
**#73** (QM/name cleanup) and **#84** (repo rename to "MQ resilience"). The panel
labels normalize for free when those land.

| QM | Role | Setup |
|----|------|-------|
| `QMPCMK` | Service QM (Ubuntu Pacemaker HA/DR), VIP `10.10.1.200` | `pcmk_san_ha` / `_dr` |
| `QMRDQM` | Service QM (RHEL RDQM) — **assert this name in the rdqm role** (currently unset) | `rdqm_ha` / `_dr` |
| `QMAIN` | Service QM (standalone, Phase-B) | `standalone` |
| `QDTCC` | DTCC counterparty (simulated) | `standalone` |

## Structure

**One collapsible row per queue manager** — mirroring the VM-group and
network-section rows (curated, individually collapsible, absent → no-data). All QMs
visible at once; collapse the ones you're not poking at. Small, fixed number — no
need to scale to a full QM's object inventory.

Each QM row shows **three object types**, deliberately picky and minimal:

### 1. The QM
- **Status** (up / running) — fail-loud: a dead exporter shows red, never a stale
  "healthy" tile.
- **One crude "it's alive and moving" stat** — the direct analog of the VM CPU-busy
  tile. Working pick: **message rate** (puts+gets/sec). A glance says "messages are
  flowing."
- **Owner** (HA arm only): which node currently holds the QM (the #131 flag, folded
  in here).

### 2. Channels (compact table)
The **interconnections between QMs** — the load-bearing object for watching the link.
Minimal columns:
- channel name
- **status / state** — RUNNING / RETRYING / INACTIVE / STOPPED (colour-coded)
- *(candidate)* messages or last-activity time

This is where, in a drill, you watch a sender channel flip to **RETRYING** the
instant the link is severed.

### 3. Queues (compact table)
Two kinds, both shown:
- **Transmission queues (xmitq)** — where a channel stages messages bound for a
  remote QM.
- **Application queues** — where messages are delivered / responses sit.

Minimal columns: queue name · type (xmit/app) · **depth** (and % of max) ·
*(candidate)* enqueue/dequeue rate.

## The diagnostic payoff

Channel state + xmitq depth together **localize a stalled flow**: kill the WAN, the
sender channel goes RETRYING and its xmitq depth climbs while the app queue drains —
you *see* exactly where the message path is stuck. That is the live HA/DR story
(#119) made visible, tidy and tabular.

## Design principles
- **Picky and minimal.** Pick only the few critical attributes per object; resist
  showing everything a QM exposes. Tabular and tidy.
- **Glass-box / fail-loud.** Red on a dead probe; never a stale "owned/healthy".
- Consistent with the existing dashboard's curated-rows pattern.

## Data source

IBM's **`mq_prometheus`** exporter (`ibmmq_*` metrics), run in **client mode**
scraping the QM (the VIP for the HA arm). It subscribes to the QM's `$SYS/MQ`
statistics topics and polls the **specific queues/channels we configure** — so *which*
objects is a deliberate config choice.

> **Not the six-month battle.** That was making the *monitoring itself* HA/DR,
> bridging a Prometheus client across data centers to legacy remote QMs. We do none
> of that — a standard client-mode scrape against our own QM is expected to "just
> work," because we're not stretching the exporter on the axes it wasn't built for.

## Prerequisites & dependencies
- **Exporter spike (blocks implementation):** deploy `mq_prometheus` client-mode
  against `QMPCMK`'s VIP, confirm the real `ibmmq_*` metric names, and learn which
  channel/queue attributes are actually exposed. Then pick the macros and finalize
  the columns. *This is its own piece of work.*
- **`QMRDQM` naming:** assert the RDQM QM name (small change in the rdqm role; ties to
  #73).
- **Rename #73 / #84:** cosmetic for this panel — labels follow whatever lands.

## Out of scope
- The QM/repo rename (#73 / #84).
- The exporter deployment itself (separate spike).
- Full-QM metric coverage — we intentionally show only the few objects that matter.

## Open questions (for the spike)
- Exact `ibmmq_*` metric names for QM status, message rate, channel status, queue
  depth.
- Is **channel status** available via the exporter, or does it need a side path
  (e.g. a small `runmqsc DIS CHSTATUS` collector)?
- The concrete list of channels and queues to enumerate per QM (the message-path
  objects between `QMPCMK` ↔ `QDTCC` and the client SVRCONN).
