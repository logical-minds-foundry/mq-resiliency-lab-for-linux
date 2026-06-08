# DR/HA Continuous-Flow Validation & Loss-Quantification Framework — Design

- **Date:** 2026-06-08
- **Status:** Design (approved in brainstorming; pending spec review)
- **Issue:** #44
- **Defers to:** Layer C reconciliation (future, gated on client protocol); gray-failure class (#43)
- **Relationship:** refines and extends §3.1 (Test methodology) of
  `2026-06-03-mq-cluster-lab-design.md`; **gates Phase E** (§10) — the comparison
  is only honest once both arms run this suite.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. The honesty thesis: HA vs DR](#3-the-honesty-thesis-ha-vs-dr)
- [4. Architecture — five components](#4-architecture--five-components)
  - [4.1 Flow generator](#41-flow-generator)
  - [4.2 Ledger oracle (three tiers)](#42-ledger-oracle-three-tiers)
  - [4.3 Scenario / fault engine](#43-scenario--fault-engine)
  - [4.4 Exposure gauge](#44-exposure-gauge)
  - [4.5 Analyzer / reporter](#45-analyzer--reporter)
- [5. Message lifecycle & classification model](#5-message-lifecycle--classification-model)
- [6. Scenario catalog](#6-scenario-catalog)
- [7. Reporting & outputs](#7-reporting--outputs)
- [8. Relationship to the lab design & phases](#8-relationship-to-the-lab-design--phases)
- [9. Open questions](#9-open-questions)
- [10. Implementation surface](#10-implementation-surface)
- [11. Success criteria](#11-success-criteria)

---

## 1. Problem & motivation

The Phase C and Phase D builds each demonstrated **RPO 0** — but only for the
*messages-at-rest* case. The recorded method was, verbatim from the findings:

- Phase C (RDQM): *"persistent messages seeded pre-fault, retrieved"*; controlled
  cutover recorded as *"all 3 pre-cutover persistent messages retrieved via B's VIP."*
- Phase D (Pacemaker/SAN): *"3 persistent messages put at site A … all 3 retrieved at
  site B — RPO 0."*

Both prove that **durable messages survive a failover**. That is real and necessary,
but it is the static case: nothing was *in flight* when the fault landed. The current
client harness cannot do better — `clients/epn_requester.py` puts N messages in a loop
and *then* gets N replies; nothing flows concurrently, and nothing is mid-replication
when a node dies. No tool in the tree deliberately induces loss or counts a gap.

The lab design already promised more than the builds delivered: §4.2 names the
asynchronous **message-loss window**, §4.3 names *"the promise that breaks"* and the
reconciliation obligation, and §3.1 step 7 explicitly requires validating *"the
app-level reconciliation path."* This spec closes that gap.

**The stakes.** DR is the linchpin. A botched DR event in this domain does not cost a
restart — it costs reconciliation. Real-world experience: a major outage can leave tens
of thousands of trades to reconcile, consuming a large team for weeks at enormous cost,
precisely because no one invested in the tooling described here. The deliverable is not
"we proved DR works." It is **"we can detect a loss the instant it happens, quantify it
exactly, and report it"** — the prerequisite skill for ever automating reconciliation.

This framework is **Layer B**: detect, quantify, report. It is built so **Layer C**
(reconciliation) slots in once the client's message protocol and application behaviour
are known (next-week-onward client input). Layer C is deliberately out of scope:
reconciliation requires message-content and business semantics we do not yet have.

## 2. Goals & non-goals

**Goals**

1. A **continuous, steady-state flow** through the full path (firm → QMAIN → DTCC sim →
   reply) that keeps running *through* every fault and DR event.
2. **HA:** prove **RPO 0 under load** across *every* fault in the suite. When a fault
   does **not** hold zero, that is not a failure of the exercise — it opens an
   investigation into the constraint required to build for zero.
3. **DR:** deterministically **reproduce RPO ≠ 0**, and **detect / quantify / report**
   the exposure — classified as lost vs duplicated vs stranded, with exact message
   identities and counts.
4. A **live exposure gauge**: at any instant, "if we failed right now, here is the
   exposure."
5. A **confidence envelope**: the precise conditions under which an RPO-0 claim is
   honest — and the conditions under which it provably is not.
6. A **cross-arm comparison** (C vs D) on the *same* suite — the honest input Phase E
   currently lacks.

**Non-goals**

- **Layer C reconciliation mechanism** — sequence/ack-ledger-driven replay, re-request
  of unconfirmed trades, end-of-day reconciliation. Gated on client protocol/app
  semantics. This spec only ensures the seams exist for it.
- **Gray-failure / degraded-but-not-failed** class (replication that slows rather than
  stops) — tracked in #43, a separate future iteration.
- **The app↔infra coordination layer** — systematically studying how the *application's*
  MQI connection and configuration choices (fail-if-quiescing, VIP-vs-node connection,
  auto-reconnect, syncpoint discipline, expiry) change HA/DR behaviour. The app stays a
  **dummy** here; this build sets sane defaults so the *infrastructure* story is clean.
  The parameter study is a follow-on, tracked in #45.
- **Security** and **performance benchmarking** beyond what loss measurement requires
  (per §0 of the lab design, security is out of scope; throughput tuning is not the
  point here).

## 3. The honesty thesis: HA vs DR

The framework treats the two halves differently *by design*, because their physics
differ:

- **HA is synchronous** (intra-site, low latency) → genuine RPO 0 is achievable. HA is
  *supposed to be perfect*. The honest test is to beat it relentlessly under load and
  show it never loses a message — and where it does, learn why.
- **DR is asynchronous** (cross-site, WAN latency) → there is an **unavoidable loss
  window equal to the replication lag** (§4.2). An unqualified "RPO 0" for DR is a lie.
  RPO 0 across sites is reachable **only** under a controlled quiesce; in any forced
  event it is non-zero, and the deliverable is to *measure* it.

This yields **two DR regimes**:

- **Controlled (far-side fault, our side quiescable).** The fault is on the sender/peer
  side; our primary is healthy. Stop the app, let in-flight traffic drain, confirm
  replication has caught up, *then* cut over → RPO 0 is honest. We already do a version
  of this; its new contribution is **documenting the envelope**, not new building. Note
  the clean drain itself **depends on app cooperation** (fail-if-quiescing; see §9) — an
  app↔infra contract studied as a follow-on (#45), not here.
- **Forced (primary unrecoverable, cannot quiesce).** Corrupted network, dead storage,
  whole-site loss. Flow continues straight through the cutover; replication may already
  be broken. Loss is guaranteed → reconciliation is required. This is the new build.

We are **not finished** until we have reproduced, deterministically, at least one
scenario where RPO ≠ 0 and explained exactly why.

## 4. Architecture — five components

### 4.1 Flow generator

Replaces the batch requester with a **sustained, concurrent** producer + responder
driving a real request/reply round-trip at a configurable rate, with optional per-message
**expiry**. Every message is **self-identifying**: a monotonic sequence number, a UUID,
and `created` / `sent` / `reply` timestamps. Because the synthetic format is *ours*,
detection needs nothing from DTCC's real protocol — that constraint only blocks Layer C,
not Layer B.

The generator and responder both write the app-side ledger (§4.2) as a side effect of
every put/get, so the ledger is a faithful record of what the *application* believes.

### 4.2 Ledger oracle (three tiers)

1. **App-side, both ends** — firm (requester) and DTCC sim (responder). **Authoritative
   and production-realistic.** Persisted append-only so it survives the fault for
   analysis. Encodes the per-message tri-state: **never-sent** / **in-pipeline-unresolved**
   (local QM ACKed, no reply yet) / **confirmed** (reply received).
2. **DTCC god's-eye** — a lab-only instrument recording what DTCC *actually* received and
   replied to. We would never have this in production; in the lab it lets us **prove our
   app-side detection is correct** against absolute truth, and **size the Ambiguous
   bucket** (§5).
3. **MQ-side recorder** — diagnostic visibility, especially for **duplication** and
   edge-case forensics. Flagged honestly as **possibly not production-scalable**
   (per-message MQ logging is expensive); this is a lab-grade visibility tool first, and
   its production viability is a separate question. The back-office settlement profile
   (where correctness may outrank raw throughput, unlike HFT) may change that calculus —
   unknown until client input.

### 4.3 Scenario / fault engine

Orchestrates the catalog (§6) under live flow. Reuses existing primitives
(`lab/scripts/net-down.sh` / `net-up.sh`, the DRBD/replication controls exercised in
Phase D, process/node kill) and adds a **controlled-quiesce path** (stop app → drain →
confirm replication caught up → cutover). Scenarios **compose/chain** — e.g. "replication
degrades → *then* failover" — so causal dependencies are expressible, not just isolated
single faults.

### 4.4 Exposure gauge

Derives, at any instant T, the current at-risk set:

```
exposure(T) = count(in-pipeline / unresolved)
            + count(sent-but-not-yet-replicated-to-secondary)
```

The second term requires the **replication position** (DRBD / the replication tier)
versus what has been written locally. Sampled continuously during a run, this is the
"if we die this instant, here is what evaporates" number. At steady state, a
well-architected QM at low-to-medium rate never actually queues (per-message processing
beats inter-arrival time), so exposure can sit near zero — and **the goal is to keep that
number as small as possible**, because the less there is to reconcile, the better.

### 4.5 Analyzer / reporter

Post-event, diffs the three ledgers against the surviving queue + replication state to
produce the per-message **bucket census** (§5), the **loss window**, **RTO**, peak/at-fault
**exposure**, and a verdict. Emits a structured, machine-readable report plus a human
summary (§7).

## 5. Message lifecycle & classification model

**Firm (requester) state machine — one record per message:**

```
CREATED ──MQPUT+commit OK──▶ IN-PIPELINE ──reply matched──▶ CONFIRMED
 (seq, uuid,            (local QM durably        (ts_reply)
  ts_created)           has it; ts_sent)
```

**IN-PIPELINE / unresolved** is the dangerous state: the local QM ACKed (the message is
durable *somewhere*), but no reply has returned — so the firm cannot know whether DTCC
received it, processed it, or the reply died in transit. **That ambiguity is the entire
DR problem**, and the ledger makes it explicit rather than invisible.

**DTCC sim** independently records `RECEIVED` + `REPLIED` per message (god's-eye, lab-only).

**After a forced cutover, every message sorts into one bucket** by diffing the firm ledger
× the DTCC god's-eye ledger × what is actually present on the secondary:

| Bucket | Meaning | Reconciliation implication |
|---|---|---|
| **Confirmed** | reply received before cutover | done, safe |
| **Continued** | replicated + cleanly reprocessed on secondary | good — the system worked |
| **Stranded** | SENT, never replicated, sitting on the dead primary | lost *now*; **replay hazard on failback** |
| **Lost–unprocessed** | SENT, DTCC never received, absent on secondary | safe to resend |
| **Ambiguous** | DTCC *did* process it, reply lost in cutover | **resend = duplicate** — the reconciliation set |
| **Duplicated** | DTCC received it twice | already a dup — must be caught |

**The headline.** In production you have only the firm ledger, so **Stranded +
Lost–unprocessed + Ambiguous all look identical — they are just "no reply."** The
god's-eye DTCC ledger is what lets the *lab* prove the true split and **size the Ambiguous
bucket** — the set that, in the real world, forces human reconciliation because you
genuinely cannot tell "never arrived" from "arrived, processed, reply lost." Demonstrating
that we can measure that boundary is the pitch.

**Duplication has two sources:**

- **At cutover:** a message replicated to the secondary *and* processed on the primary
  before death, with the reply lost → reprocessed on the secondary → DTCC sees it twice.
- **At failback:** the "comes back to haunt you" hazard. Messages stranded on a dead
  primary, reconciled and resent during the DR event, are *still on the revived
  primary's disk* when it is reintroduced days/weeks later. Depending on how replication
  is re-established, **they can replay as duplicate trades.** Failback/reintroduction is
  therefore a **first-class detection target**, not just failover.

**Expiration as a DR-safety lever.** A request whose reply you stop waiting for after
*t* seconds has no business carrying an expiry longer than *t*. Persistent, no-expiry
messages are the ones that linger and haunt. The framework can set per-message expiry and
**show its effect** on the failback-replay bucket.

## 6. Scenario catalog

Every drill runs **under continuous flow**, identically on both arms (C and D). The
"buckets" column is what each drill is engineered to light up; a drill that does *not*
produce its target buckets is itself a finding.

| ID | Injected under load | Expected RPO | Designed to light up | Proves |
|---|---|---|---|---|
| **HA-1** | `kill -9` the QM process | **0** | Continued only | local restart loses nothing under flow |
| **HA-2** | power-off active node | **0** | Continued only | intra-site failover transparent under flow |
| **HA-3** | sever heartbeat/replication net | **0** | Continued only | no split-brain; correct quorum, no loss |
| **HA-4** | sever shared storage | **0** | Continued only | graceful, no corruption (esp. D/SAN arm) |
| **HA-5** | rolling patch one node at a time | **0** | Continued only | maintenance under flow (RDQM kernel-module case) |
| **DR-CTRL** | quiesce → drain → confirm replication caught up → cutover | **0** | Confirmed / Continued | RPO 0 *is* reachable — and the exact preconditions that make the claim honest |
| **DR-FORCE-1** | primary unrecoverable, flow continues through cutover | **≠ 0** | Stranded, Ambiguous, maybe Duplicated | the baseline forced-DR loss, quantified |
| **DR-FORCE-2** *(marquee)* | primary isolated from **both** DTCC and secondary, app keeps producing | **≠ 0** | large Stranded | the linchpin — un-replicated messages stranded, gap sized |
| **DR-FORCE-3** | replication lagged/broken **then** failover (chained) | **≠ 0, scales with lag** | Stranded grows with window | loss window = replication lag; ties to the exposure gauge |
| **FB-REPLAY** | reintroduce formerly-dead primary holding stranded persistent msgs | duplication risk | Duplicated | the "comes back to haunt you" replay; expiry shown as mitigation |

Any HA row returning **≠ 0** does not fail the suite — it opens an investigation into the
constraint needed to reach zero. That is the lab doing its job.

## 7. Reporting & outputs

Each run emits a structured (machine-readable) report plus a human summary, carrying:

- **Identity:** scenario ID, arm (C/D), flow rate, run duration.
- **Timeline:** when the fault landed, **RTO** (service restored), when flow resumed.
- **Bucket census:** exact counts for Confirmed / Continued / Stranded / Lost–unprocessed /
  Ambiguous / Duplicated, with drill-down to individual seq/UUIDs.
- **Loss window:** as both a *time span* and a *message-count span* (e.g. "messages
  8,801–8,842, a 1.7 s window").
- **Exposure:** peak exposure during the run, and exposure at the instant of the fault.
- **Honesty fields:** intervention required? data-integrity anomaly? diagnostics captured
  (ties to §3.1 step 8 — `runmqras` / FFST under fault)?
- **Verdict:** RPO 0 achieved (yes/no); if no, the explained *why* and the governing
  constraint.

Two roll-ups sit on top:

1. **Confidence envelope** — a single document stating the conditions under which an
   RPO-0 claim is honest, and the conditions under which it provably is not. This replaces
   today's unqualified "RPO 0."
2. **Cross-arm comparison** — the same scenario's bucket census, C vs D side by side. The
   honest input Phase E was missing.

**Self-correctness (fail loud).** In a **no-fault baseline run**, the god's-eye DTCC
ledger must agree with the app-side ledger exactly — zero Ambiguous, zero Lost. If they
disagree with no fault injected, the *instrument* is broken, and we fix that before
trusting any drill. (This satisfies the "no silent failures" rule: the oracle surfaces its
own breakage rather than hiding it.)

## 8. Relationship to the lab design & phases

- **Extends §3.1.** The §3.1 fault suite becomes the *under-load, loss-quantified* suite
  defined here. The original at-rest checks remain valid but are now the floor, not the
  ceiling.
- **Gates Phase E.** The §10 comparison & recommendation depends on apples-to-apples data;
  that data is only honest once **both arms** have run this suite. E does not start until
  then.
- **Runs identically on arms C and D** — the same generator, ledgers, scenarios, and
  reports, so the comparison is fair.
- **Layer C deferred** (client-gated); **gray-failure deferred** (#43).

## 9. Open questions

1. **Can *this* app guarantee a clean quiesce?** For a small, single-app deployment a
   controlled drain may be feasible (making DR-CTRL's RPO 0 realistic); for a 24/7
   multi-tenant estate with many apps it is not. This is a **finding to produce**, not a
   design input — the framework must handle the flowing/forced case regardless.
   Critically, the controlled quiesce depends on **app cooperation**: without
   `MQGMO_/MQOO_FAIL_IF_QUIESCING`, a controlled `endmqm -c` will not return the app's
   in-flight MQI calls, so the app keeps the connection busy and **blocks a clean
   shutdown** — the wrong behaviour when a forced DR needs the primary stabilised fast.
   Likewise the app must connect to the **VIP, not a node address** (a prior outage was
   caused by a client pinned to a node IP, which HA failover then broke). The dummy app
   here sets these correctly so the infra story is clean; the systematic study of varying
   them is the #45 follow-on.
2. **Flow-rate parameters are unknown** until client input (back-office settlement
   profile). The generator must be rate-parameterized; the *chosen* rates for the headline
   runs are TBD.
3. **MQ-side recording production-scalability** — lab-grade now; production viability
   depends on the real throughput envelope (§4.2 tier 3).
4. **Reading the replication position per arm** for the exposure gauge — both arms use
   DRBD-style block replication (RDQM internally; the Pacemaker arm via DRBD per the
   Phase D build), so the "bytes/ops behind" signal should be obtainable on both; exact
   mechanism to be confirmed in the plan.

## 10. Implementation surface

Prototype, generically written (per §0 of the lab design — proof of concept, not the
client's production code). Expected landing zones:

- **`clients/`** — evolve `epn_requester.py` / `epn_responder.py` into the concurrent
  flow generator + responder, each emitting its app-side ledger.
- **`src/mqlab/`** — new analyzer/classifier module (ledger × god's-eye × queue/replication
  state → bucket census + report); exposure-gauge sampler.
- **`lab/scripts/`** — scenario/fault-engine orchestration on top of the existing
  `net-*.sh`, DRBD controls, kill, and the new quiesce path.
- **Reports** — written under `build/` for runs; representative results summarized into
  `docs/reports/` per the existing phase-report convention.

Exact module boundaries and interfaces are the job of the implementation plan
(writing-plans), not this spec.

## 11. Success criteria

1. A continuous flow runs through the full path and survives every HA fault with **RPO 0
   under load** — or, where it does not, the framework reports the exact loss and the
   investigation has a home.
2. At least one **forced-DR scenario deterministically reproduces RPO ≠ 0**, with the loss
   classified into the §5 buckets, counted exactly, and bounded by a reported window.
3. The **exposure gauge** reports a live at-risk number throughout a run.
4. The **failback-replay** hazard is detected as a Duplicated bucket.
5. The **confidence envelope** and **cross-arm comparison** documents exist and are
   honest.
6. The **self-correctness baseline** passes (god's-eye == app-ledger at zero fault).
7. Both arms (C and D) have run the identical suite, unblocking Phase E.
