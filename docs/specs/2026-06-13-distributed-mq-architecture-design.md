# Distributed MQ Architecture — two businesses, QM-to-QM across a WAN

**Issue:** #145
**Date:** 2026-06-13
**Status:** Design — awaiting review
**Scope:** The end-to-end *distributed-messaging* shape of the lab: our HA/DR
queue manager exchanging messages with a counterparty queue manager over
sender/receiver channels across a WAN, while our QM fails over (HA and DR). This
spec defines the **target architecture, naming, MQ object set, resilience model,
and the topology/VM changes** to get there. The MQSC-role and `mqlab` wiring that
*implements* it is a separate plan; the day-long HA/DR validation loop is a later
spec.

## 1. Goal

Model the actual point of IBM MQ: **two businesses, each operating its own queue
manager, exchanging messages asynchronously over channels.** Make the inter-QM
link a first-class, instrumented part of the lab so we can prove that
QM-to-QM message flow survives our queue manager failing over — node-to-node
(HA) and site-to-site (DR) — with no loss and no duplication.

The deliverable of the *implementation* this spec leads to is a runnable
distributed setup: an **app** puts a request that crosses the WAN to a
counterparty **service** and the reply comes home, and that flow keeps working
across a forced failover of our QM.

## 2. What this corrects (the gap)

All HA/DR testing to date exercises a **single queue manager with client
reconnect**: an application connects client-mode to one QM via its VIP and rides
`MQCNO_RECONNECT` through failovers (the
[client HA/DR cooperation contract](2026-06-09-mq-client-ha-cooperation-contract.md)).
That validates HA of one QM — real and necessary — but it never models
distributed messaging. The "DTCC" role was implemented as another **client of
the same QM**, not as a **remote queue manager**. There are no inter-QM channels,
transmission queues, or remote-queue definitions anywhere in the tree.

Consequence: we proved internal-app HA, not QM-to-QM resilience across a WAN
while our QM moves. This spec closes that.

## 3. The two-business architecture

Two halves separated by a WAN. We instrument **our** side (HA/DR); the
counterparty is a deliberate black box.

```text
  BUSINESS A — US (instrumented HA/DR)              ~ WAN ~        BUSINESS B — DTCC (black box)

  ┌──────────┐  client        ┌────────────────────┐                ┌───────────────────────────┐
  │  APP VM  │ ─────────────▶ │      QMPCMK        │ ══ SDR/RCVR ══▶ │         QMDTCC            │
  │ requester│  CONNAME(      │  our HA/DR QM       │   channels     │  standalone, transparent  │
  │  "app"   │   VIP-A,VIP-B) │  (two sites, one    │ ◀══ pair ══════│  single endpoint          │
  └──────────┘                │   active at a time) │                │   │ bindings                │
                              └────────────────────┘                │   ▼                       │
                                                                    │  responder "service"      │
                                                                    └───────────────────────────┘
```

- **app** (requester) — a remote **client**; it cannot live on our cluster.
- **QMPCMK** — our HA/DR queue manager, present across two sites, **exactly one
  site active at any instant**.
- **QMDTCC** — the counterparty queue manager, **standalone, no HA/DR**, presenting
  a single transparent endpoint (§7, §10).
- **service** (responder) — co-located with QMDTCC on one VM, connected by
  **bindings** (local, no client).

Nouns are **app** / **service** — the request-sender and the response-generator —
chosen because "client"/"server" are wire-protocol roles, not machines, and
recur confusingly at every layer.

The rendered companion diagrams are stashed at
`docs/specs/diagrams/distributed-mq-architecture.html` (overview, network
topology, two-VIP model, HA/DR lifecycle, message-flow plumbing).

## 4. Naming

| Role | Name | Notes |
|---|---|---|
| Our HA/DR QM | **QMPCMK** | Pacemaker/SAN arm. A parallel **QMRDQM** follows on the RDQM arm, identical design, for side-by-side platform comparison. |
| Counterparty QM | **QMDTCC** | Standalone, transparent single endpoint. |
| App → QM channel | `APP.SVRCONN` | SVRCONN on QMPCMK; app connects client-mode. |
| Our → their channel | `QMPCMK.QMDTCC` | SENDER on QMPCMK, RECEIVER on QMDTCC. Carries requests. |
| Their → our channel | `QMDTCC.QMPCMK` | SENDER on QMDTCC, RECEIVER on QMPCMK. Carries replies. |
| App request target | `DTCC.REQUEST` (`QREMOTE`) | Resolves to `SVC.REQUEST` @ `QMDTCC` via xmitq `QMDTCC`. |
| Service inbound queue | `SVC.REQUEST` | `QLOCAL` on QMDTCC; the service GETs here. |
| App reply queue | `APP.REPLY` | `QLOCAL` on QMPCMK; the app GETs here by `CorrelId`. |
| Outbound xmitq (ours) | `QMDTCC` | `QLOCAL USAGE(XMITQ)` on QMPCMK; name = target QM. |
| Reply xmitq (theirs) | `QMPCMK` | `QLOCAL USAGE(XMITQ)` on QMDTCC; name = target QM ⇒ auto-resolution. |

Channel-naming convention: a channel's SENDER end on one QM and its RECEIVER end
on the other share the same name (`<source>.<target>`).

## 5. Message flow and MQ objects

Standard remote request/reply: **two one-way channel pairs, one transmission
queue per direction**, a remote-queue definition outbound, automatic xmitq
resolution (xmitq named after the remote QM) on the reply.

### 5.1 The flow

1. **app** (client → QMPCMK) `MQPUT` to `DTCC.REQUEST`, setting
   `ReplyToQ=APP.REPLY`, `ReplyToQMgr=QMPCMK`.
2. `QREMOTE DTCC.REQUEST` resolves to `{ RNAME=SVC.REQUEST, RQMNAME=QMDTCC,
   XMITQ=QMDTCC }`; the message lands on xmitq `QMDTCC`.
3. SENDER `QMPCMK.QMDTCC` (triggered by the xmitq) drains it across the WAN.
4. RECEIVER `QMPCMK.QMDTCC` on QMDTCC delivers to `SVC.REQUEST`.
5. **service** (bindings) `MQGET`s `SVC.REQUEST`, processes, and `MQPUT`s the
   reply to the message's `ReplyToQ`/`ReplyToQMgr` (`APP.REPLY` @ `QMPCMK`).
6. On QMDTCC, `RQMNAME=QMPCMK` resolves to xmitq `QMPCMK` **by name** (no remote
   qdef needed); the reply lands there.
7. SENDER `QMDTCC.QMPCMK` drains it back across the WAN; RECEIVER
   `QMDTCC.QMPCMK` on QMPCMK delivers to `APP.REPLY`.
8. **app** `MQGET`s `APP.REPLY` by `CorrelId`.

### 5.2 MQSC object set (target)

**On QMPCMK:**

```mqsc
DEFINE QLOCAL(APP.REPLY)              REPLACE
DEFINE QREMOTE(DTCC.REQUEST)  RNAME(SVC.REQUEST) RQMNAME(QMDTCC) XMITQ(QMDTCC) REPLACE
DEFINE QLOCAL(QMDTCC)  USAGE(XMITQ)   TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA(QMPCMK.QMDTCC) REPLACE
DEFINE CHANNEL(QMPCMK.QMDTCC) CHLTYPE(SDR)  CONNAME('10.60.0.50(1414)') XMITQ(QMDTCC) REPLACE
DEFINE CHANNEL(QMDTCC.QMPCMK) CHLTYPE(RCVR) REPLACE
* APP.SVRCONN already defined by the mq-pcmk-qmgr role (CHLAUTH/ MCAUSER handled there)
```

**On QMDTCC:**

```mqsc
DEFINE QLOCAL(SVC.REQUEST)            REPLACE
DEFINE QLOCAL(QMPCMK)  USAGE(XMITQ)   TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA(QMDTCC.QMPCMK) REPLACE
DEFINE CHANNEL(QMDTCC.QMPCMK) CHLTYPE(SDR)  CONNAME('10.60.0.10(1414),10.60.0.20(1414)') XMITQ(QMPCMK) REPLACE
DEFINE CHANNEL(QMPCMK.QMDTCC) CHLTYPE(RCVR) REPLACE
```

The endpoint addresses are the partner-facing VIPs defined in §7 (DTCC at
`10.60.0.50`; our per-site ext VIPs `10.60.0.10`/`10.60.0.20`). Channel security
(`MCAUSER`, `CHLAUTH`) follows the same pattern the `mq-pcmk-qmgr` role already
establishes for `APP.SVRCONN`; the exact mapping is an implementation detail.

## 6. Connection and resilience model

### 6.1 CONNAME lists are *discovery*, not the safety mechanism

Every connection that must reach **our** QM carries a **CONNAME list of both
site VIPs**, so it follows the QM across HA (the VIP floats within a site) and DR
(a different VIP in the other site):

- **app → QMPCMK**: `CONNAME(VIP-A, VIP-B)` + `MQCNO_RECONNECT`.
- **QMDTCC → QMPCMK** (the reply SENDER): `CONNAME(VIP-A, VIP-B)`.
- **QMPCMK → QMDTCC**: a **single** static endpoint (DTCC is not HA/DR).

A CONNAME list probes its entries **in order** (A, then B). This is correct for
active/standby: a client biases back to A, and only uses B when A is unreachable.

### 6.2 HA vs DR are different events

| | HA (node → node) | DR (site → site) |
|---|---|---|
| Trigger | automatic, zero-touch | **manual** — a human "big red button" |
| VIP | **same** VIP floats within the site | a **different** VIP in the other site |
| Client | `MQCNO_RECONNECT` rides it transparently | reconnect, then CONNAME falls through to site-B VIP |
| Operation | respond *after the fact*: confirm the cluster recovered | a managed, heavily-automated **single command**; both sites effectively down during the transition; apps reconnect |
| Failback | n/a | a **separate** managed outage (quiesce B, demote B, promote A, start on A) |

DR is reduced — as far as the technology allows — to a single, idempotent,
bulletproof command (the `lab/scripts/pcmk-dr-cutover.sh` lineage), but the
**trigger is a human decision**. DR is not transparent and we do not pretend it
is.

### 6.3 The invariant: never both sites live

At no instant may apps reach a running QM on **both** sites. This is enforced
**underneath** the CONNAME list, at the storage/cluster layer: **DRBD
single-primary + Pacemaker** mean the QM can only start where the volume is
promoted, and the volume is promoted on exactly one site. A repaired primary
returns as **standby** (DRBD secondary) — Pacemaker physically cannot start the
QM there, so a client probing A-first simply falls through to B. "Don't let apps
prematurely connect to the recovered primary" is therefore enforced by *not
promoting its storage*, not by client configuration.

## 7. Network / WAN modeling

The two-business boundary must be **structurally real**: the counterparty must
not share our internal data/SAN/heartbeat planes, and the inter-QM link must
cross a **distinct, shapeable WAN segment** — separate from the existing
`net-wan` (10.99.0.0/24), which models our *intra-org* cross-site DRBD
replication, not an inter-*business* link.

### 7.1 Recommended model — dedicated inter-business segment + partner-facing VIP

Add a dedicated inter-business network `net-ext` (the modeled internet),
**10.60.0.0/24**, and give our QM a **second floating VIP** on it — a
partner-facing endpoint, distinct from the internal app's data-plane VIP:

| Endpoint | Address | On |
|---|---|---|
| Internal app VIP (existing) | `10.10.1.200` / `10.10.2.200` | `net-data-a` / `net-data-b` |
| **Partner (DTCC) VIP — site A** | `10.60.0.10` | `net-ext`, floats with QMPCMK on `pcmk_a` |
| **Partner (DTCC) VIP — site B** | `10.60.0.20` | `net-ext`, floats with QMPCMK on `pcmk_b` |
| DTCC service VM | `10.60.0.50` | `net-ext` |

So: `QMDTCC.QMPCMK` SENDER `CONNAME('10.60.0.10(1414),10.60.0.20(1414)')`;
`QMPCMK.QMDTCC` SENDER `CONNAME('10.60.0.50(1414)')`. Pacemaker manages the
partner VIP as a second `IPaddr2` resource colocated and ordered with the QM, per
site. The internal app continues to use the data-plane VIPs unchanged.

This mirrors production accurately — internal consumers hit an internal service
address; external partners hit a separate, internet-facing address that differs
per site — and it gives the inter-business hop its own segment to traffic-shape
later (§9). The pcmk nodes gain a `net-ext` NIC (`10.60.0.5{1,2,3}` site A,
`10.60.0.6{1,2,3}` site B); `dtcc-sim` gains `net-ext` and drops `net-dtcc`.

### 7.2 Decision: two VIPs (confirmed)

The two-VIP model in §7.1 is adopted. The rejected alternative — a **single
shared VIP** reachable by both the internal app and DTCC (one network, one VIP
per site) — is simpler (no second `IPaddr2` resource) but **collapses the
two-business boundary** onto one network and erases the internal/partner address
distinction. That distinction is load-bearing: in the real world the internal and
partner VIPs are firewalled differently and may even traverse different physical
interfaces. The two-VIP model is the minimal complexity needed to keep the
boundary structurally accurate without over-modeling.

### 7.3 Future — WAN traffic-shaping

With the inter-business hop on its own segment, a later enhancement can throttle,
delay, or drop traffic on `net-ext` to simulate WAN degradation (link loss,
latency, partial outage) and observe channel retry / xmitq fill behaviour.
Tracked as an open issue (§11), not in this scope.

## 8. Topology and VM changes

Grounded in the current `lab/topology.yaml`:

- **app** = the existing `app-client` VM (role rename to `app`). Already homed on
  `net-data-a`/`net-data-b`, so it already reaches both site VIPs; it needs only
  the CONNAME-list client configuration. No re-homing.
- **DTCC service VM** = repurpose the existing `dtcc-sim` VM into the real
  counterparty: it hosts **QMDTCC** (a standalone queue manager) **and** the
  bindings **service** (responder). Re-home it from `net-dtcc` onto `net-ext`
  (§7) so it can reach our partner VIPs and be reached from our QM.
- **New setup** `distributed` (working name) composes the HA/DR cluster with the
  counterparty and the app: groups `[san_a, san_b, pcmk_a, pcmk_b]` (our QM,
  both sites) + a `dtcc` service group + the `app` group, provisioned so the
  full cross-WAN request/reply path exists and is exercisable.
- **Retire** the `standalone` setup, the `qm-main` VM, and **QMAIN** — a
  placeholder never used in HA/DR testing.
- **Retire** the vestigial/mismatched EPN client pair
  (`clients/epn_requester.py` / `clients/epn_responder.py`) and reconcile the
  `clients/dr_*.py` helpers to the app/service roles defined here (a single
  reconnectable client to QMPCMK for the app; a bindings responder for the
  service). Exact client refactor is implementation-plan detail.

The RDQM arm (`QMRDQM`) reuses this entire design unchanged; only the HA/DR
mechanism underneath differs. It is out of scope for the first build.

## 9. HA/DR operations and the validation north-star

- **HA**: automatic. The operator's job is to *confirm recovery after the fact*,
  not to act during it.
- **DR failover / failback**: each a single, idempotent, heavily-automated
  command (the `pcmk-dr-cutover.sh` lineage), human-triggered, that returns the
  system to a known state.
- **North-star validation (later spec): the day-long HA/DR loop.** Once
  mechanized, run failover/failback cycles continuously for ~24h and count clean
  automated recoveries. This is the ultimate smoke test and the design target
  everything here serves: every cycle must **self-reset to a known state** the
  next iteration can consume (idempotent DR command, no residual split-brain risk,
  the channels and xmitqs converge empty after each flush). The architecture in
  this spec is written to *admit* that loop rather than fight it.

## 10. The counterparty is a black box (v1 assumption)

v1 assumes **DTCC presents a single transparent endpoint** — one queue manager,
one CONNAME, internal resiliency opaque to us. This is a legitimate real-world
presentation (some counterparties genuinely behave this way) and it **isolates
the variable**: when the inter-QM channel re-establishes after a failover, we
know *our* side caused it. The counterparty's own resiliency (transparent single
endpoint vs. two named QMs vs. per-site endpoints) is unknown and not generically
modelable; it is tracked as an open issue (§11) to be modeled once real
requirements are known. The expectation is that the eventual real model is on the
*more complex* end — and building to the complex case subsumes the trivial one.

## 11. Open issues

1. **Remote-side resiliency model.** v1 = single transparent DTCC endpoint.
   Model richer counterparty topologies (transparent / two named QMs / per-site
   endpoints) once DTCC's real requirements are known. This may change the
   inter-QM design (CONNAME handling, channel set) on *our* side.
2. **WAN traffic-shaping.** Throttle/delay/drop on `net-ext` to simulate WAN
   degradation (§7.3).

## 12. Diagrams

Stashed as a self-contained, version-controlled artifact alongside this spec:
`docs/specs/diagrams/distributed-mq-architecture.html` — the five brainstorm
diagrams (two-business overview; network topology & trust boundaries; two-site /
two-VIP model; HA vs DR lifecycle; MQ object plumbing / message flow). They are
the glass-box explanation of the architecture and feed the eventual public
docs/site.

## 13. Acceptance criteria

A build is "done" against this spec when:

1. An **app** request `MQPUT` to `DTCC.REQUEST` crosses the WAN, is processed by
   the **service**, and the reply returns to `APP.REPLY` — across two distinct
   queue managers connected only by sender/receiver channels.
2. Forcing an **HA** failover of QMPCMK *mid-flow* leaves the request/reply path
   working: the inter-QM SENDER re-establishes, staged xmitq messages survive and
   flush, no loss, no duplication.
3. Forcing a **DR** cutover of QMPCMK leaves the path working: the app and the
   DTCC SENDER follow QMPCMK to the site-B VIP via their CONNAME lists; the
   never-both-live invariant holds throughout.
4. The two-business boundary is structurally real: DTCC reaches our QM only
   across `net-ext`, never our internal planes.
5. `QMAIN`/`standalone` and the vestigial EPN clients are removed; the tree
   contains no single-QM-masquerading-as-distributed paths.
