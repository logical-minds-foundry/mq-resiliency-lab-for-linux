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
(HA) and site-to-site (DR).

The loss guarantee differs by event, and that difference is itself a lab
objective:

- **HA is RPO 0.** Zero loss is an absolute requirement we *demonstrate* —
  shared storage, no replication gap (proven under load in #66).
- **DR is RPO ≥ 0.** Async cross-site replication means an unplanned cutover
  loses any un-replicated tail; RPO 0 in an unplanned DR event is luck, not a
  guarantee. A primary purpose of the DR lab is to build tooling that
  **measures and quantifies** the real RPO of a DR event and drives it toward
  minimal through engineering — not to pretend it is zero.

Neither event may ever produce **duplicates or corruption** — that is
non-negotiable in both.

The deliverable of the *implementation* this spec leads to is a runnable
distributed setup: an **app** puts a request that crosses the WAN to a
counterparty **service** and the reply comes home, and that flow keeps working
across a forced failover of our QM.

### 1.1 Non-goals — security is explicitly out of scope

This lab tests **message flow and resiliency only**. Security is a deliberate
non-goal of this version: the inter-QM and app channels run **wide open**
(`MCAUSER('mqm')`, `CHLAUTH(DISABLED)`, no transport encryption), and secrets are
allowed to be exposed in the lab. This is intentional, not an oversight — there
is **no security functionality here**, and nothing in this spec should be read as
providing any.

Security (mutual TLS, `CHLAUTH` peer/cert mapping, non-privileged identities,
secret management) is a substantial, awkward layer best built and reasoned about
on its own — a candidate future minor/major version or a separate (possibly
later-merged) security lab. It is expected to sit **on top of** this architecture:
the object names, channel names, and structure here stay the same; security adds
controls to their configuration rather than reshaping the design. Deferring it
keeps the core architecture unblocked (tracked in §11).

## 2. What this corrects (the gap)

All HA/DR testing to date exercises a **single queue manager with client
reconnect**: an application connects client-mode to one QM via its VIP and rides
`MQCNO_RECONNECT` through failovers (the
[client HA/DR cooperation contract](2026-06-09-mq-client-ha-cooperation-contract.md)).
That validates HA of one QM — real and necessary — but it never models
distributed messaging. The "SVC" role was implemented as another **client of
the same QM**, not as a **remote queue manager**. There are no inter-QM channels,
transmission queues, or remote-queue definitions anywhere in the tree.

Consequence: we proved internal-app HA, not QM-to-QM resilience across a WAN
while our QM moves. This spec closes that.

## 3. The two-business architecture

Two halves separated by a WAN. We instrument **our** side (HA/DR); the
counterparty is a deliberate black box.

```text
  BUSINESS A — US (instrumented HA/DR)              ~ WAN ~        BUSINESS B — SVC (black box)

  ┌──────────┐  client        ┌────────────────────┐                ┌───────────────────────────┐
  │  APP VM  │ ─────────────▶ │      QMPCMK        │ ══ SDR/RCVR ══▶ │         QMSVC            │
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
- **QMSVC** — the counterparty queue manager, **standalone, no HA/DR**, presenting
  a single transparent endpoint (§7, §10).
- **service** (responder) — co-located with QMSVC on one VM, connected by
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
| Counterparty QM | **QMSVC** | Standalone, transparent single endpoint. |
| App → QM channel | `APP.SVRCONN` | SVRCONN on QMPCMK; app connects client-mode. |
| Our → their channel | `QMPCMK.QMSVC` | SENDER on QMPCMK, RECEIVER on QMSVC. Carries requests. |
| Their → our channel | `QMSVC.QMPCMK` | SENDER on QMSVC, RECEIVER on QMPCMK. Carries replies. |
| App request target | `SVC.REQUEST` (`QREMOTE`) | Resolves to `SVC.REQUEST` @ `QMSVC` via xmitq `QMSVC`. |
| Service inbound queue | `SVC.REQUEST` | `QLOCAL` on QMSVC; the service GETs here. |
| App reply queue | `APP.REPLY` | `QLOCAL` on QMPCMK; the app GETs here by `CorrelId`. |
| Outbound xmitq (ours) | `QMSVC` | `QLOCAL USAGE(XMITQ)` on QMPCMK; name = target QM. |
| Reply xmitq (theirs) | `QMPCMK` | `QLOCAL USAGE(XMITQ)` on QMSVC; name = target QM ⇒ auto-resolution. |

Channel-naming convention: a channel's SENDER end on one QM and its RECEIVER end
on the other share the same name (`<source>.<target>`).

## 5. Message flow and MQ objects

Standard remote request/reply: **two one-way channel pairs, one transmission
queue per direction**, a remote-queue definition outbound, automatic xmitq
resolution (xmitq named after the remote QM) on the reply.

### 5.1 The flow

1. **app** (client → QMPCMK) `MQPUT` to `SVC.REQUEST`, setting
   `ReplyToQ=APP.REPLY`, `ReplyToQMgr=QMPCMK`.
2. `QREMOTE SVC.REQUEST` resolves to `{ RNAME=SVC.REQUEST, RQMNAME=QMSVC,
   XMITQ=QMSVC }`; the message lands on xmitq `QMSVC`.
3. SENDER `QMPCMK.QMSVC` (triggered by the xmitq) drains it across the WAN.
4. RECEIVER `QMPCMK.QMSVC` on QMSVC delivers to `SVC.REQUEST`.
5. **service** (bindings) `MQGET`s `SVC.REQUEST`, processes, and `MQPUT`s the
   reply to the message's `ReplyToQ`/`ReplyToQMgr` (`APP.REPLY` @ `QMPCMK`).
6. On QMSVC, `RQMNAME=QMPCMK` resolves to xmitq `QMPCMK` **by name** (no remote
   qdef needed); the reply lands there.
7. SENDER `QMSVC.QMPCMK` drains it back across the WAN; RECEIVER
   `QMSVC.QMPCMK` on QMPCMK delivers to `APP.REPLY`.
8. **app** `MQGET`s `APP.REPLY` by `CorrelId`.

**Correlation contract (load-bearing).** The request/reply match works only if
the service sets the reply's `CorrelId` to the **request's `MsgId`** and puts with
`MQPMO_NEW_MSG_ID` (so the reply gets its own `MsgId`); the app then `MQGET`s
`APP.REPLY` with `MATCH(CORREL_ID)` against the `MsgId` it received from its put.
Get this wrong and every reply is delivered but **un-matchable** — the app blocks
forever and it looks like message loss. Spelled out here because the lab is
glass-box and must be supportable without the AI.

### 5.2 MQSC object set (target)

**On QMPCMK:**

```mqsc
DEFINE QLOCAL(APP.REPLY)              REPLACE
DEFINE QREMOTE(SVC.REQUEST)  RNAME(SVC.REQUEST) RQMNAME(QMSVC) XMITQ(QMSVC) REPLACE
DEFINE QLOCAL(QMSVC)  USAGE(XMITQ)   TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA(QMPCMK.QMSVC) REPLACE
DEFINE CHANNEL(QMPCMK.QMSVC) CHLTYPE(SDR)  CONNAME('10.60.0.50(1414)') XMITQ(QMSVC) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL(QMSVC.QMPCMK) CHLTYPE(RCVR) REPLACE
* APP.SVRCONN already defined by the mq-pcmk-qmgr role (CHLAUTH/ MCAUSER handled there)
```

**On QMSVC:**

```mqsc
DEFINE QLOCAL(SVC.REQUEST)            REPLACE
DEFINE QLOCAL(QMPCMK)  USAGE(XMITQ)   TRIGGER TRIGTYPE(FIRST) INITQ(SYSTEM.CHANNEL.INITQ) TRIGDATA(QMSVC.QMPCMK) REPLACE
DEFINE CHANNEL(QMSVC.QMPCMK) CHLTYPE(SDR)  CONNAME('10.60.0.10(1414),10.60.0.20(1414)') XMITQ(QMPCMK) SHORTRTY(10) SHORTTMR(5) LONGRTY(999999999) LONGTMR(20) REPLACE
DEFINE CHANNEL(QMPCMK.QMSVC) CHLTYPE(RCVR) REPLACE
```

The endpoint addresses are the partner-facing VIPs defined in §7 (SVC at
`10.60.0.50`; our per-site ext VIPs `10.60.0.10`/`10.60.0.20`). Channel security
is **deliberately absent**: the inter-QM channels run wide open, exactly like
`APP.SVRCONN` (`MCAUSER('mqm')`, `CHLAUTH(DISABLED)`), by the explicit non-goal
in §1.1 — not by accident or silent inheritance.

**Retry timers are tuned for lab snappiness, not production.** The SENDER timers
above (`SHORTTMR(5)` then `LONGTMR(20)`, effectively unlimited `LONGRTY`) make a
moved QM re-found within seconds and keep retrying indefinitely. Production uses
long retry intervals so channels self-heal after multi-hour outages with
operators on a bridge; here the outages are *artificial and short*, and the
day-long failover loop (§9) measures recovery — default timers (`LONGTMR` 1200s)
would leave the sender idle for up to 20 minutes and the flow would read as hung
when it is merely waiting. Values to be finalised in the plan. The **channel
initiator** must be running for trigger-driven sender start; it restarts with the
QM on failover, so this holds across HA and DR.

## 6. Connection and resilience model

### 6.1 CONNAME lists are *discovery*, not the safety mechanism

Every connection that must reach **our** QM carries a **CONNAME list of both
site VIPs**, so it follows the QM across HA (the VIP floats within a site) and DR
(a different VIP in the other site):

- **app → QMPCMK**: `CONNAME(VIP-A, VIP-B)` + `MQCNO_RECONNECT`.
- **QMSVC → QMPCMK** (the reply SENDER): `CONNAME(VIP-A, VIP-B)`.
- **QMPCMK → QMSVC**: a **single** static endpoint (SVC is not HA/DR).

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

### 6.4 Loss semantics — RPO by event type

The resilience model carries an explicit, asymmetric loss guarantee:

- **HA failover → RPO 0.** Shared storage means no replication gap; staged and
  in-flight persistent messages survive (demonstrated under load, #66).
- **DR cutover → RPO ≥ 0.** Async cross-site replication means a *graceful,
  quiesced* cutover can drain to RPO 0, but a *disaster* cutover loses the
  un-replicated tail. The DR lab's job is to **measure and quantify** that real
  RPO with tooling and minimise it through engineering — not to assume it away
  (cf. the measured RPO>0 result in #74 and the master design §4.2).
- **No duplication or corruption, ever** — in either event.

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
| **Partner (SVC) VIP — site A** | `10.60.0.10` | `net-ext`, floats with QMPCMK on `pcmk_a` |
| **Partner (SVC) VIP — site B** | `10.60.0.20` | `net-ext`, floats with QMPCMK on `pcmk_b` |
| SVC service VM | `10.60.0.50` | `net-ext` |

So: `QMSVC.QMPCMK` SENDER `CONNAME('10.60.0.10(1414),10.60.0.20(1414)')`;
`QMPCMK.QMSVC` SENDER `CONNAME('10.60.0.50(1414)')`. Pacemaker manages the
partner VIP as a second `IPaddr2` resource colocated and ordered with the QM, per
site. The internal app continues to use the data-plane VIPs unchanged.

**HA/DR automation impact (do not miss).** The current automation is built for a
*single* VIP — the `mq-pcmk-qmgr` role creates the group as `mq_fs → mq_vip →
mq_qm`, and `pcmk-dr-cutover.sh` hardcodes one `TO_VIP` per direction. The
partner VIP is therefore **not** free wiring; it requires changes in **two**
places: (i) the **role** adds `mq_vip_ext` into the group with ordering
`mq_fs → mq_vip → mq_vip_ext → mq_qm`, and (ii) the **cutover script** sets and
creates the per-site `TO_VIP_EXT` alongside `TO_VIP` when it rebuilds the group at
the target site. The silent failure mode if either is missed: a DR cutover brings
up the data VIP and the QM, the internal app works, but the partner VIP never
comes up and the **cross-business flow is dead** — the exact bug this rework
exists to prevent. Acceptance gates this (§13.4).

This mirrors production accurately — internal consumers hit an internal service
address; external partners hit a separate, internet-facing address that differs
per site — and it gives the inter-business hop its own segment to traffic-shape
later (§9). The pcmk nodes gain a `net-ext` NIC (`10.60.0.5{1,2,3}` site A,
`10.60.0.6{1,2,3}` site B); `svc-sim` gains `net-ext` and drops `net-svc`.

### 7.2 Decision: two VIPs (confirmed)

The two-VIP model in §7.1 is adopted. The rejected alternative — a **single
shared VIP** reachable by both the internal app and SVC (one network, one VIP
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
- **SVC service VM** = repurpose the existing `svc-sim` VM into the real
  counterparty: it hosts **QMSVC** (a standalone queue manager) **and** the
  bindings **service** (responder). Re-home it from `net-svc` onto `net-ext`
  (§7) so it can reach our partner VIPs and be reached from our QM.
- **New setup** `distributed` (working name) composes the HA/DR cluster with the
  counterparty and the app: groups `[san_a, san_b, pcmk_a, pcmk_b]` (our QM,
  both sites) + a `svc` service group + the `app` group, provisioned so the
  full cross-WAN request/reply path exists and is exercisable.
- **Retire** the `standalone` setup, the `qm-main` VM, and **QMAIN** — a
  placeholder never used in HA/DR testing.
- **Retire** the vestigial/mismatched FFH client pair
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

v1 assumes **SVC presents a single transparent endpoint** — one queue manager,
one CONNAME, internal resiliency opaque to us. This is a legitimate real-world
presentation (some counterparties genuinely behave this way) and it **isolates
the variable**: when the inter-QM channel re-establishes after a failover, we
know *our* side caused it. The counterparty's own resiliency (transparent single
endpoint vs. two named QMs vs. per-site endpoints) is unknown and not generically
modelable; it is tracked as an open issue (§11) to be modeled once real
requirements are known. The expectation is that the eventual real model is on the
*more complex* end — and building to the complex case subsumes the trivial one.

## 11. Open issues

1. **Remote-side resiliency model.** v1 = single transparent SVC endpoint.
   Model richer counterparty topologies (transparent / two named QMs / per-site
   endpoints) once SVC's real requirements are known. This may change the
   inter-QM design (CONNAME handling, channel set) on *our* side.
2. **WAN traffic-shaping.** Throttle/delay/drop on `net-ext` to simulate WAN
   degradation (§7.3).
3. **Security layer (future version / separate lab).** Mutual TLS (`SSLCIPH`),
   `CHLAUTH` peer/cert mapping, non-privileged identities, secret management —
   explicitly out of scope here (§1.1). Expected to layer on top of this
   architecture without reshaping it (same object/channel names; added controls
   on their configuration). Candidate to merge into this lab or run standalone.

## 12. Diagrams

Stashed as a self-contained, version-controlled artifact alongside this spec:
`docs/specs/diagrams/distributed-mq-architecture.html` — the five brainstorm
diagrams (two-business overview; network topology & trust boundaries; two-site /
two-VIP model; HA vs DR lifecycle; MQ object plumbing / message flow). They are
the glass-box explanation of the architecture and feed the eventual public
docs/site.

## 13. Acceptance criteria

A build is "done" against this spec when:

1. An **app** request `MQPUT` to `SVC.REQUEST` crosses the WAN, is processed by
   the **service**, and the reply returns to `APP.REPLY` — across two distinct
   queue managers connected only by sender/receiver channels.
2. Forcing an **HA** failover of QMPCMK *mid-flow* leaves the request/reply path
   working at **RPO 0**: the inter-QM SENDER re-establishes, staged xmitq
   messages survive and flush, **no loss, no duplication, no corruption** (shared
   storage; proven under load in #66).
3. Forcing a **DR** cutover of QMPCMK leaves the path working — the app and the
   SVC SENDER follow QMPCMK to the site-B VIP via their CONNAME lists, and the
   never-both-live invariant holds throughout — with loss semantics by cutover
   type:
   - **Planned/graceful** DR (quiesce → drain xmitqs → promote): **RPO 0**, no
     loss.
   - **Unplanned/disaster** DR: a **bounded RPO > 0** equal to the
     async-replication tail; assert **no loss beyond that tail, no duplication,
     no corruption**, and that the tail is **measured and quantified** (per the
     DR-validation framework; cf. #74, master design §4.2). RPO 0 here is an
     edge-case windfall, not a pass condition.
4. After a **DR cutover**, **both** VIPs are live at the target site — the data
   VIP *and* the partner (`net-ext`) VIP — and **SVC's SENDER reconnects** and
   resumes flow. (Guards the two-VIP automation gap, §7.1.)
5. The two-business boundary is structurally real: SVC reaches our QM only
   across `net-ext`, never our internal planes.
6. `QMAIN`/`standalone` and the vestigial FFH clients are removed; the tree
   contains no single-QM-masquerading-as-distributed paths.
