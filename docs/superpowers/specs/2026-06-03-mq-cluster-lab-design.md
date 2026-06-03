# MQ Cluster Tooling — Lab & Tooling Design

> **Status:** living document, expanded iteratively during brainstorming.
> **Date:** 2026-06-03
> **Author:** Phillip Moore (with Claude)
> **Context:** Pre-engagement R&D for a client — a large financial services
> firm bringing IBM MQ (DTCC clearing connectivity) in-house off a third-party
> provider. The client standardizes on Ubuntu Linux and wants to own the
> service. The workload is **back-office post-trade clearing/settlement** with
> DTCC — *not* real-time trade execution — so uptime/latency/throughput
> requirements differ from front-office systems and are **TBD** pending
> client/DTCC input. This repo is a personal home-lab harness on an Apple M5
> Max (128 GB, arm64) used to develop and validate portable MQ HA/DR tooling
> before go-live (2026-06-15).
>
> **Anonymization note:** this is deliberately generic, non-proprietary
> industry work. A large enterprise needing to clear post-trade with DTCC over
> IBM MQ on a Linux cluster is a common, well-understood use case — none of it
> is client-specific. The specific client is intentionally **not named** so
> this R&D can be shared. Keep it that way: refer to "the client" / "the
> enterprise," never the firm.

---

## 0. Framing (non-negotiable)

**The product is the scripting, configuration approach, and operational
understanding for standing up and managing a *redundant* IBM MQ queue
manager on Linux clusters — with both intra-site high availability *and*
cross-site disaster recovery.** The virtualization lab is a disposable
development/validation harness — it is *not* the deliverable. No one rebuilds
this VM topology at the client; it exists so the tooling can be developed and proven
locally, then carried into the client (and ideally published as a reusable package).

**Deliverable #1 (the foundation):** demonstrate that we can stand up a
*solid, redundant, basic* queue manager and document *exactly* how to set it
up and operate it — before any message-pumping, configuration breadth, or
performance testing. Get this right first; everything else builds on it.

**This is not a cheap-it-out exercise.** the client is a tier-one firm. We engineer
this with the most modern, strategic stack that meets the requirements — not
the lowest-cost option that technically works.

---

## 1. North Star & Solution Scope

- Portable HA **and DR** tooling/config/standards is the product; the lab is
  the harness.
- A complete solution has **two inseparable halves**: (a) intra-site HA — the
  queue manager survives node/component failure within a data center; and
  (b) cross-site DR — the service is recoverable when an entire data center
  is lost. **Bulletproof HA without a demonstrable DR story is not a
  solution.** (See §4.)
- First proof (Deliverable #1): a solid, redundant basic queue manager with a
  documented, repeatable setup-and-operate procedure, measured against §3.
- **Design for the full architecture (HA *and* DR, 3+3) up front.** Even though
  Deliverable #1 is one solid QM, we architect from day one for the complete
  HA-within-site + DR-across-site shape (three nodes per site, see §4 and
  Appendix A) so nothing has to be retrofitted later.
- **Vendor-supportability gap is a first-class, heavily-weighted criterion.**
  MQ is a closed-source black box; for a tier-one firm we *must* be able to get
  IBM at the table for a SEV-1. Every architecture is judged partly on how far
  it deviates from what IBM will support (see §3). A self-managed Ubuntu cluster
  that IBM disclaims is a serious mark against it, however elegant.
- **Workload is back-office post-trade clearing/settlement with DTCC** — not
  front-office trade execution. Performance/throughput/latency are *not* the
  priority; correctness, recoverability, and failover behavior are. Exact
  uptime/RPO/RTO targets are **TBD** pending DTCC/the client requirements, and the
  final recommendation is explicitly deferred until those are known.

## 2. The Central Thesis & Research Agenda

### 2.1 Thesis

> **Can a production-grade, redundant IBM MQ queue manager — with both
> intra-site HA and credible cross-site DR — be built and reliably operated
> on Ubuntu using only the native Linux stack (i.e. *without* RDQM), and how
> does that compare to RDQM running on RHEL x86-64?**

The deliverable of this thread is a *defensible, evidence-backed
recommendation* the client can act on. "RDQM on RHEL x86-64 is the only way to meet
all the requirements" is an acceptable and valuable conclusion if the
evidence supports it — and current expectation is that it may well be the
front-runner, precisely because of the DR/replication argument in §4.

### 2.2 Why this is the core risk

The client wants to standardize on Ubuntu. IBM's flagship, turnkey HA/DR technology
(RDQM) is hard-locked to RHEL x86-64 by a DRBD kernel module shipped as RHEL
RPMs — confirmed across IBM's System Requirements, the Installing-RDQM docs,
and the RDQM Kernel Modules support page. The gap between *what the client wants*
(Ubuntu) and *what IBM blesses for turnkey HA/DR* (RHEL/RDQM) is the single
biggest unknown to de-risk before go-live. Everything else is downstream.

### 2.3 Candidate architectures (the experimental arms)

Each arm must be evaluated on **both** its HA story and its DR story (§4).

1. **External Pacemaker/Corosync** managing the QM as an OCF resource, over
   shared block storage — **SAN/iSCSI**, attached/detached by the failover
   resource agents (explicitly *not* NFS; see §2.4) — with **STONITH**
   fencing and a **qdevice/witness** for 2-node quorum. Base MQ + IBM's
   sample resource agents. Fully self-managed: we own the cluster, storage,
   and fencing. **DR:** host- or array-based replication cross-site (e.g.
   DRBD async + Booth/geo-clustering, or SAN array replication). The shared
   storage tier is the acknowledged weak link to engineer around.
2. **RDQM** (RHEL x86-64 only) — the comparison baseline and likely
   front-runner. **HA:** 3-node group, **synchronous** DRBD block
   replication, shared-nothing, automatic failover, built-in quorum, turnkey
   `rdqmadm` / `crtmqm -sx` tooling. **DR:** RDQM DR — **asynchronous** DRBD
   replication to a remote node/group; combined HA+DR supported. The key
   strategic property: **replication is owned by the data-management layer**
   (like a modern database), not offloaded to an async infrastructure tier.
3. **Native HA** (parked) — three-node log-replication quorum, but
   **containers/Kubernetes/OpenShift only**. Revisit only if the client will accept
   containers on Ubuntu hosts; out of scope for the bare-VM premise. (Note:
   its container-native replication model is conceptually closest to RDQM's
   "data layer owns replication" virtue.)

### 2.4 Rejected: multi-instance queue manager over NFS

**Decision: eliminated. We will not recommend, build, or support it.** This
is based on direct production experience, and the rationale is itself a
deliverable (decision-makers will ask "why not the cheap option?").

- **Known data-integrity failure modes** — NFS locking edge cases can produce
  message loss and queue-file corruption; we suffered serious outages with
  this exact technology at a prior employer.
- **Poor performance** — NFS round-trips on the message path.
- **Fatal for DR** — making the NFS tier redundant relies on filer-level
  replication, which typically runs on **5–10 minute cycles**. That cycle
  time *is* the cross-site message-loss window — unacceptable for cleared
  trades. It offloads data management to an asynchronous infrastructure layer
  with a minutes-scale RPO, the opposite of the data-layer-owns-replication
  model we want.
- **Verdict:** 20th-century solution to this problem. Off the list.

### 2.5 Open questions to resolve by experiment

- **Q1 — Pacemaker HA parity:** Can a self-managed Pacemaker cluster on Ubuntu
  achieve RDQM-equivalent automatic failover — proper fencing, no
  split-brain, RPO 0 within site — using shared SAN/iSCSI block storage? What
  is the Day-2 operational burden versus RDQM's turnkey tooling?
- **Q2 — Quantify the RDQM gap:** What exactly does RDQM give that the Ubuntu
  Pacemaker option does not? (Shared-nothing synchronous replication → no
  shared-storage SPOF; integrated HA+DR lifecycle tooling; full IBM support.)
  Put specifics on the gap, not adjectives.
- **Q3 — RDQM/RHEL lock (confirm, don't assume):** Treat RDQM as RHEL-x86-64
  only. If time permits, *attempt* it on Ubuntu to document precisely how/why
  it fails (kmod load failure), turning the assumption into evidence.
- **Q4 — The storage-SPOF trap:** The Ubuntu/Pacemaker arm needs HA *and*
  cross-site-replicable shared storage. Can we build that on Ubuntu (SAN
  array replication, or DRBD+Pacemaker for the storage tier) without it
  becoming as complex and fragile as the thing RDQM solves in one box? This
  is likely where the recommendation is decided.
- **Q5 — DR replication mode & window:** For each arm, what is the realistic
  cross-site replication mode (sync vs async) and resulting RPO window? How
  does RDQM DR's continuous async (seconds) compare to the alternatives?
- **Q6 — App/infra DR interface:** What must the application (and the DTCC
  protocol) do to tolerate the DR message-loss window? (See §4.3.) This is a
  cross-team requirement, not pure infra.

### 2.6 Output of this thread

A written tradeoff analysis (phase E) scoring each arm against the §3 + §4
criteria, ending in a recommendation with explicit conditions ("choose X if
the client values A over B").

## 3. Reliability & Redundancy Criteria (the yardstick)

These define what "solid" means and give a consistent scorecard to run every
arm through. Each approach in §2.3 is evaluated against all of them, for both
the HA case (this section) and the DR case (§4).

- **RTO (recovery time objective)** — wall-clock from node failure to QM
  available again. Measured empirically, not estimated.
- **RPO (recovery point objective)** — data loss on failover. For cleared
  trades this must be **zero for persistent messages** within site. RDQM/DRBD
  achieve it via synchronous block replication; Pacemaker via shared storage.
  Assess the distinct failure modes of each path.
- **Split-brain protection / fencing** — does the design prevent two active
  instances corrupting the queue files? STONITH + quorum. Highest-stakes
  correctness property.
- **Quorum model** — 2-node-plus-witness vs. 3-node; tie-breaker behavior
  under partition.
- **Single points of failure** — enumerate explicitly per approach (storage
  array/LUN, heartbeat network, witness, replication link).
- **Data integrity under fault** — behavior under `kill -9`, power loss,
  network partition, and storage loss; persistent-message guarantees in each.
- **Operational complexity / Day-2** — install effort, failover *and*
  failback procedures, patching (esp. RDQM's kernel-module/kernel coupling),
  observability, and the skill level required to run it at 3 a.m.
- **Vendor-supportability gap** *(weighted heavily)* — how far does the
  architecture deviate from a configuration IBM will support on a SEV-1? RDQM
  is fully IBM-supported end to end; an external Pacemaker/SAN cluster is "here
  are sample resource agents, you own the cluster, storage, and fencing." For a
  tier-one firm running a black-box product, a wide gap is a serious liability —
  when (not if) we hit an outage that needs IBM, we must be inside, or close to,
  their supported envelope. Measured concretely: which components IBM supports,
  which they disclaim, and what we'd have to prove/rebuild before they'll engage.
- **Recoverability & diagnostics** — when it breaks, can we (a) recover service
  and (b) capture the diagnostics IBM needs to drive a SEV-1? Each arm is
  assessed on how cleanly it produces `runmqras` archives, FFST/FDC records,
  and cluster/replication state for hand-off to IBM support. Tooling to gather
  this is part of the product (see §8).
- **Performance overhead** *(out of scope for the comparison)* — this is
  back-office clearing, not front-office execution; throughput/latency are not
  decision criteria. Note synchronous-replication latency (DRBD) qualitatively
  only. Failover *speed* (RTO) matters and is captured above; raw message
  throughput is not benchmarked.

### 3.1 Test methodology

A standardized fault-injection suite, run **identically** against each arm so
results are comparable:

1. Kill the QM process (`kill -9` the QM processes) — expect automatic restart
   or failover per design.
2. Hard-kill the active node (power off) — measure RTO, confirm RPO 0.
3. Sever the heartbeat/replication network — confirm no split-brain; correct
   quorum decision.
4. Sever access to shared storage — confirm graceful behavior, no corruption.
5. Graceful planned failover and **failback** — the routine ops case.
6. Rolling patch / node maintenance — update one node at a time without an
   outage? (RDQM kernel-module coupling is the interesting case.)
7. **Full site loss** (DR) — kill the entire primary site; measure cross-site
   RTO and RPO, and validate the app-level reconciliation path (§4.3).
8. **Diagnostics capture under fault** — after each fault, exercise the
   diagnostic-gathering path (`runmqras`, FFST/FDC collection, cluster and
   replication state) and confirm we'd have a clean package to hand IBM for a
   SEV-1. This is itself a tested deliverable, not an afterthought.

Each scenario records: RTO, message loss (if any), whether intervention was
required, any data-integrity anomaly, and whether usable IBM-grade diagnostics
were captured.

## 4. Disaster Recovery & Business Continuity (first-class requirement)

### 4.1 Why DR is inseparable from the design

You cannot design this without DR. A solution that survives any single-node
or single-component failure but cannot demonstrate reliable failover when an
entire data center is lost does not meet the client's needs. DR is the *harder*
problem and it constrains the HA design choices (e.g. it is what eliminates
NFS in §2.4). Every candidate arm carries an explicit DR mechanism in §2.3.

### 4.2 The synchronous-vs-asynchronous problem (the message-loss window)

- **Within a data center / metro** (low latency), **synchronous** replication
  is feasible → **RPO 0**. This is the HA case.
- **Across geographically separated data centers**, synchronous replication
  is generally impractical — WAN latency throttles throughput and distance
  imposes hard limits. So cross-site replication is almost always
  **asynchronous** → **non-zero RPO** → an unavoidable **message-loss
  window** equal to the replication lag.
- The whole game is **minimizing that window**. RDQM DR uses continuous
  asynchronous DRBD (seconds-scale lag). NFS filer replication (rejected)
  runs minutes-scale cycles. Orders of magnitude separate them.

### 4.3 The app/infrastructure interface (the promise that breaks)

Under normal operation the MQ infrastructure promises the application it will
**never lose a message**. In a true site disaster with asynchronous
cross-site replication, **that promise does not hold** — messages inside the
replication window can be lost or duplicated on cutover. This is the ugly,
essential part of the design:

- The infrastructure's guarantee degrades during DR; the **application and
  the DTCC protocol must cooperate** to close the gap — sequence numbering,
  acknowledgements, idempotent processing, **replay / re-request of
  unconfirmed trades**, and end-of-day reconciliation.
- This is a **cross-team conversation** (infra ↔ application ↔ possibly DTCC),
  not something infrastructure can solve alone. Any recommendation must state
  the residual DR risk explicitly and the app-side obligations to mitigate it.

### 4.4 Per-approach DR mechanisms (validated against IBM docs)

- **RDQM** — three documented configurations, confirmed against IBM MQ 9.4
  docs (trust-but-verify; cited below for re-checking against the version the client
  licenses):
  - **HA group:** exactly **3 nodes, synchronous DRBD, single site**, one
    quorum domain, automatic Pacemaker failover. RPO 0 within site.
  - **DR:** a **DR pair** — one primary instance replicating **asynchronously**
    to one recovery instance, created with `crtmqm -rr p` / `-rr s`. Cutover is
    **manual** (`rdqmdr`), seconds-scale async window. No node ever spans the
    WAN.
  - **HA/DR combined (3+3):** a synchronous 3-node HA group at the primary
    site replicating **asynchronously** to a 3-node HA group at the recovery
    site. This is the shape we design toward (see §1, Appendix A).
  - **Strategic win — shared-nothing:** RDQM owns replication in the
    data-management layer (DRBD block replication between local disks). There is
    **no shared storage** — no SAN, no LUN, no iSCSI target to be the SPOF and
    weak link. This simplicity is the single biggest argument for RDQM over the
    Pacemaker/SAN arm, and the thing most likely to "sell" RHEL despite the client's
    Ubuntu preference.
  - **Correction recorded:** the earlier "2+1 stretch" idea (one HA member in
    the other DC) is an **anti-pattern** — an HA group is a single synchronous
    quorum domain, so stretching it ties every commit to inter-site latency and
    turns a DC-to-DC partition into a cluster collapse rather than clean DR.
    Cross-site is *always* a separate asynchronous DR relationship.
- **Pacemaker/SAN (Ubuntu):** cross-site via SAN array replication or
  host-based DRBD async between sites, coordinated by a multi-site cluster
  manager (e.g. **Booth** ticket arbitration). More moving parts; we own all
  of it — and it reintroduces the shared-storage SPOF that RDQM avoids.
  Validate that it can match RDQM DR's window and correctness.

**IBM references (re-verify against the licensed version):**

- RDQM HA — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-rdqm-high-availability>
- RDQM DR — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-rdqm-disaster-recovery>
- RDQM HA/DR combined — <https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=configurations-rdqm-disaster-recovery-high-availability>
- Creating a DR RDQM pair — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=recovery-creating-disaster-rdqm>
- `rdqmdr` (manage DR instances) — <https://www.ibm.com/docs/en/ibm-mq/9.2?topic=reference-rdqmdr-manage-dr-rdqm-instances>
- Pacemaker cluster / quorum — <https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=availability-defining-pacemaker-cluster-ha-group>

### 4.5 DR open questions

- What cross-site RPO is *actually achievable* per arm, and what is the client's
  tolerance (TBD — needs DTCC/vendor requirements)?
- What is the cross-site RTO and the cutover procedure (manual vs automated)?
- Failback to primary after a DR event without data loss or split-brain.
- What are DTCC's absolute requirements for resilience and message integrity?
  (Unknown — a key thing to learn from the vendor/DTCC documentation.)

## 5. Lab Topology

The lab simulates **two data centers** so DR is exercisable from day one,
matching the "six Linux servers" hint: **three nodes per DC = 3+3**.

- **DC-A (primary):** 3-node RDQM HA group (`node-a1/a2/a3`), synchronous,
  automatic failover. Holds the QM's virtual IP that clients and DTCC channels
  attach to.
- **DC-B (recovery):** 3-node HA group (`node-b1/b2/b3`) as the async DR
  target — full 3+3, production-grade (not a single recovery node).
- **Inter-site WAN:** simulated `net-wan` link between the DCs, with optional
  injected latency, carrying the asynchronous DR replication.
- **Networks (per the validated topology diagram):** DTCC-facing net; per-DC
  data/VIP net; per-DC private heartbeat/replication net; client/app net.
- **Fixtures:** `dtcc-sim` QM (server side, sender/receiver channels back to
  the in-house clearing QM); `app-client` (requester puts trades → in-house
  QM; responder replies to DTCC traffic).

**Sizing:** this is a *functional* lab — failover correctness and behavior,
not throughput. Each VM is small (~**1 GB RAM**); the full 3+3 plus fixtures
fits comfortably on the M5 Max (128 GB). The Pacemaker/SAN (Ubuntu) arm adds a
qdevice/witness and an iSCSI target VM in the same network shape.

See the validated topology diagram committed alongside this spec —
[`diagrams/topology-rdqm-ha-dr.html`](diagrams/topology-rdqm-ha-dr.html) —
which shows both the single-QM HA+DR building block (panel ①) and the
active/active two-block composition (panel ②).

## 6. OS / Arch Build Matrix

*(to expand: same skeleton, swappable OS image; where Ubuntu and RHEL diverge
at the system level.)*

| OS    | arch   | HA/DR exercisable                                  | Host method on M5 Max |
|-------|--------|----------------------------------------------------|-----------------------|
| Ubuntu| arm64  | Pacemaker/Corosync + SAN/iSCSI; DR via DRBD/Booth  | native, fast          |
| Ubuntu| x86-64 | Pacemaker/Corosync + SAN/iSCSI; DR via DRBD/Booth  | emulation or cloud    |
| RHEL  | arm64  | Pacemaker/Corosync (no RDQM)                       | native, fast          |
| RHEL  | x86-64 | Pacemaker/Corosync, **RDQM (HA + DR)**             | emulation or cloud    |

Consequence: faithfully testing **RDQM forces RHEL-x86-64**, which on this
Mac means emulation or a cheap cloud x86 box. At this stage RDQM validation
is *functional* (correct failover/DR cutover), not performance.

## 7. Virtualization & Provisioning Harness

*(to expand: options + tradeoffs — Vagrant + provider, Lima/Multipass, Tart,
UTM — parameterized by {os, arch} and capable of multi-network, multi-site
topologies; decision deferred until weighed. In-VM config via Ansible so the
real tooling stays provider/host portable.)*

**Sizing budget:** the full 3+3 topology plus DTCC sim, client, and (for the
Pacemaker arm) witness + iSCSI target is ~8–10 VMs at ~1 GB each — well within
the M5 Max's 128 GB. Native arm64 VMs run fast; x86-64 (required for RDQM)
comes via emulation or a cheap cloud box, accepted as slower since RDQM
validation is functional, not performance.

## 8. The Tooling (the actual product)

*(to expand: QM install/config automation; HA-setup automation per approach;
DR setup + cutover/failback automation; operational standards — runbooks,
health checks, backup; eventual `.deb` / `.rpm` packaging.)*

## 9. DTCC Simulation & Validation

*(to expand: DTCC QM channels; requester/responder apps; fault-injection
failover tests and DR cutover tests — prove message integrity, measure RTO/RPO.)*

## 10. Phasing

Sub-projects, each its own spec → plan → build. **DR is baked into each arm
from the start** — we do not build HA and then bolt DR on; an arm is only
"done" when it has a demonstrated cross-site DR story (per §1, design for the
full 3+3 architecture up front).

- **A.** Virtualization harness (multi-site, multi-network, {os,arch}-parameterized)
  — start here.
- **B.** Single standalone QM (Ubuntu arm64) + DTCC sim + client — prove the
  end-to-end message path before any clustering.
- **C.** **RDQM arm, full HA+DR on RHEL x86-64** — the comparison baseline:
  3-node synchronous HA group at the primary site + async DR to a 3-node group
  at the recovery site (3+3), with `rdqmdr` cutover/failback and the §3.1 fault
  suite incl. full-site-loss.
- **D.** **Ubuntu Pacemaker/SAN arm, full HA+DR** — external Pacemaker/Corosync
  + SAN/iSCSI + STONITH + qdevice for intra-site HA, plus cross-site DR (DRBD
  async + Booth), run through the *identical* §3.1 suite for an apples-to-apples
  comparison.
- **E.** Comparison analysis & recommendation for the client — scores both arms on
  §3 + §4, **weighting the vendor-supportability gap heavily**, and states the
  conditions under which each wins (decision deferred to DTCC/the client requirements).
- **F.** Packaging & operational standards — `.deb`/`.rpm`, runbooks, health
  checks, and the **recovery & diagnostics tooling** (`runmqras`/FFST capture)
  proven in §3.1 step 8.
- **G.** *(forward-looking, post-requirements)* Dual-path / multi-QM
  active-active across two DCs — see Appendix A. Out of scope for Deliverable
  #1; gated on DTCC/app requirements.

## 11. Risks & Open Questions

*(to expand: RHEL developer licensing; x86 emulation speed; RDQM under the
developer license; SAN/shared-storage as the weak link; cross-site sync
impossibility & residual DR message-loss risk; unknown DTCC/vendor resilience
requirements; ARM caveats — no MQTT/AMQP.)*

---

## Appendix A. Likely Final Recommendation: Dual-Path, Multi-QM Active/Active (forward-looking)

> **Scope note.** The body of this design is about building **one** solid
> HA/DR queue manager — that is Deliverable #1 and the foundation. This
> appendix records the *expected shape of the eventual production
> recommendation*, which is almost certainly built from **multiple** such
> queue managers. It is deliberately not part of Deliverable #1; it gets
> fleshed out once we understand DTCC's and the application's requirements.

### A.1 The pattern: parallel A/B flows across two live data centers

Drawing on prior production experience building these systems: the resilient
shape is **two redundant, parallel processing paths** — Stack A (a complete
MQ stack in data center A) and Stack B (a complete stack in data center B).
Trades can flow through either path. In the modern norm there is **no idle,
dedicated DR site**: both data centers run **live, at capacity**, each sized
to absorb the other's load. If a data center fails, the surviving stack takes
over; the only traffic impacted is what was in flight through the failed path
at the moment it died.

This is **redundancy above the single-QM layer** — defense in depth:

- **HA** handles node/component failure *within* a data center.
- **DR** handles loss of an *entire* data center for a single stack.
- **Dual-path / multi-QM** handles whole-stack failure and (if permitted)
  enables active/active throughput across both data centers.

### A.2 Mapping to the "six Linux servers" hint (validated model)

The engagement's starting constraint — "six Linux servers and a license" —
maps cleanly to **three nodes per data center**. The architecture is built
entirely from documented RDQM pieces — **no stretched groups**:

- **Each DC = one primary HA group + the other QM's DR copy.** DC-A hosts the
  3-node synchronous HA group for **QM-A** (live) *and* the async DR target for
  **QM-B**; DC-B hosts the 3-node HA group for **QM-B** (live) *and* the async
  DR target for **QM-A**. The same three nodes in a DC run both roles (the
  primary QM plus the peer's DR copy). Mutual async DR, A→B and B→A, each with
  manual cutover.
- **Both DCs live at capacity.** Trades dual-path through QM-A (DC-A) or QM-B
  (DC-B). Lose DC-A → QM-A cuts over to its DR copy in DC-B, where QM-B is
  already live; only in-flight QM-A traffic is impacted.

**Correction (was "2+1 stretch").** The earlier recollection — two HA nodes in
DC-A with a third stretched to DC-B — is **not how RDQM works** and is an
anti-pattern. An RDQM HA group is a single **synchronous** quorum domain;
putting a member across the WAN ties every commit to inter-site latency and
turns a DC-to-DC partition into a cluster collapse instead of clean DR.
Cross-site resilience is *always* a separate asynchronous DR relationship.

**Hypothesis to validate in the lab:** the symmetric "each DC = one primary
group + one DR copy" composition is assembled from documented RDQM building
blocks (3+3 HA/DR), but running it *mutually for two QMs* is our design — the
kind of thing we prove experimentally rather than trust the vendor on. See the
committed active/active topology diagram
([`diagrams/topology-rdqm-ha-dr.html`](diagrams/topology-rdqm-ha-dr.html),
panel ②). Citations in §4.4.

### A.3 The big unknowns (gate this work)

- **Does DTCC permit active/active?** Two simultaneously-live endpoints may or
  may not be allowed by DTCC's connection model and sequencing assumptions.
  Unknown until we study the DTCC application and its requirements.
- **Can both paths run live at once**, or is it active/standby at the
  path level? Depends on DTCC assumptions.
- **App-side routing & reconciliation:** the application must detect a dead
  path, reroute to the live stack, and reconcile in-flight/duplicated trades
  across paths — reinforcing the §4.3 app/infra interface point at a larger
  scale.

---

## Appendix B. Multi-Environment Replication & Cross-Environment Change Propagation (forward-looking)

> **Scope note.** Like Appendix A, this is *not* part of Deliverable #1. It
> records a second long-term purpose this lab unlocks, so the building block is
> designed with it in mind. Vendor- and provider-agnostic by intent.

### B.1 Why the building block is more than an HA/DR choice

Choosing the right HA/DR architecture (§2–§4) is only the first payoff. The
deeper value is that a **reproducible, parameterized building block** becomes
the substrate for the operational problem every real deployment hits:
**managing controlled change across multiple environments**. The lab harness
already replicates one stack from templates — replicating it *N* times with
distinct names is the same machinery.

### B.2 The dev → test → prod model

Once one stack works, stand it up **three times** as independent environments —
**dev, test, prod** (names/labels vary by organization; we use the generic
three). Each is a complete instance of the app + infrastructure with full
functionality. This gives us a faithful place to build and exercise the
**cross-environment change-propagation tooling** — the real Day-2 product:

- **Deploy** new MQ objects — typically a new set of queues/channels for a new
  counterparty, client, or entity the business must transact with.
- **Promote** those changes up the chain (dev → test → prod) in a controlled,
  repeatable, auditable fashion — the same change applied identically at each
  tier, not hand-edited per environment.
- **Decommission** objects cleanly when a relationship ends, propagating the
  removal the same controlled way.

### B.3 What this implies for the design

- **Configuration must be declarative and templated**, not snowflake-edited per
  box — so the same definition renders into dev, test, and prod with only
  environment-specific substitutions (names, hosts, endpoints).
- We will develop **minimal change-management tooling** for this lifecycle:
  apply / promote / decommission, with diffing and verification between tiers.
- This reinforces the §8 tooling direction: the install/config automation is
  the foundation the cross-environment promotion layer is built on top of.
