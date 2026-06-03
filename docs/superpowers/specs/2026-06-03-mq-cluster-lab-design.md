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
> **before the contract engagement begins (2026-06-15)**. The aim is to arrive
> with the knowledge, hands-on understanding, and ideally some working tooling
> to hit the ground running on day one — *not* a production go-live. Any
> production rollout happens later, on the client's own timeline.
>
> **Anonymization note:** this is deliberately generic, non-proprietary
> industry work. A large enterprise needing to clear post-trade with DTCC over
> IBM MQ on a Linux cluster is a common, well-understood use case — none of it
> is client-specific. The specific client is intentionally **not named** so
> this R&D can be shared. Keep it that way: refer to "the client" / "the
> enterprise," never the firm.

---

## Contents

- [0. Framing (non-negotiable)](#0-framing-non-negotiable)
- [1. North Star & Solution Scope](#1-north-star--solution-scope)
- [2. The Central Thesis & Research Agenda](#2-the-central-thesis--research-agenda)
  - [2.1 Thesis](#21-thesis)
  - [2.2 Why this is the core risk](#22-why-this-is-the-core-risk)
  - [2.3 Candidate architectures (the experimental arms)](#23-candidate-architectures-the-experimental-arms)
  - [2.4 Rejected: multi-instance queue manager over NFS](#24-rejected-multi-instance-queue-manager-over-nfs)
  - [2.5 Open questions to resolve by experiment](#25-open-questions-to-resolve-by-experiment)
  - [2.6 Output of this thread](#26-output-of-this-thread)
  - [2.7 RDQM-on-RHEL vs native HA/DR on Ubuntu — objective tradeoffs](#27-rdqm-on-rhel-vs-native-hadr-on-ubuntu--objective-tradeoffs-no-decision-yet)
- [3. Reliability & Redundancy Criteria (the yardstick)](#3-reliability--redundancy-criteria-the-yardstick)
  - [3.1 Test methodology](#31-test-methodology)
- [4. Disaster Recovery & Business Continuity (first-class requirement)](#4-disaster-recovery--business-continuity-first-class-requirement)
  - [4.1 Why DR is inseparable from the design](#41-why-dr-is-inseparable-from-the-design)
  - [4.2 The synchronous-vs-asynchronous problem (the message-loss window)](#42-the-synchronous-vs-asynchronous-problem-the-message-loss-window)
  - [4.3 The app/infrastructure interface (the promise that breaks)](#43-the-appinfrastructure-interface-the-promise-that-breaks)
  - [4.4 Per-approach DR mechanisms (validated against IBM docs)](#44-per-approach-dr-mechanisms-validated-against-ibm-docs)
  - [4.5 DR open questions](#45-dr-open-questions)
  - [4.6 Public & regulatory basis for the two-site DR requirement (researched)](#46-public--regulatory-basis-for-the-two-site-dr-requirement-researched)
  - [4.7 Symmetric peer sites & periodic role rotation (recommended design constraint)](#47-symmetric-peer-sites--periodic-role-rotation-recommended-design-constraint)
- [5. Lab Topology](#5-lab-topology)
- [6. OS / Arch Build Matrix](#6-os--arch-build-matrix)
  - [6.1 Bare metal vs virtualization (confirmed: VMs are fine)](#61-bare-metal-vs-virtualization-confirmed-vms-are-fine)
- [7. Virtualization & Provisioning Harness](#7-virtualization--provisioning-harness)
  - [7.1 Decision: Vagrant orchestrates the lab](#71-decision-vagrant-orchestrates-the-lab)
  - [7.2 The Apple-Silicon provider bind (open decision — Phase-A spike)](#72-the-apple-silicon-provider-bind-open-decision--phase-a-spike)
  - [7.3 Configuration via Ansible](#73-configuration-via-ansible)
  - [7.4 Relationship to Vergil and the host](#74-relationship-to-vergil-and-the-host)
  - [7.5 Sizing budget](#75-sizing-budget)
- [8. The Tooling (the actual product)](#8-the-tooling-the-actual-product)
- [9. DTCC Simulation & Validation](#9-dtcc-simulation--validation)
  - [9.1 Connectivity model to mirror (from the public FICC EPN MQ guide)](#91-connectivity-model-to-mirror-from-the-public-ficc-epn-mq-guide)
  - [9.2 Transport & security context (real-world, for fidelity notes)](#92-transport--security-context-real-world-for-fidelity-notes)
  - [9.3 Validation](#93-validation)
- [10. Phasing](#10-phasing)
- [11. Risks & Open Questions](#11-risks--open-questions)
- [Appendix A. Dual-Path, Multi-QM Active/Active (forward-looking)](#appendix-a-likely-final-recommendation-dual-path-multi-qm-activeactive-forward-looking)
  - [A.1 The pattern: parallel A/B flows across two live data centers](#a1-the-pattern-parallel-ab-flows-across-two-live-data-centers)
  - [A.2 Mapping to the "six Linux servers" hint (validated model)](#a2-mapping-to-the-six-linux-servers-hint-validated-model)
  - [A.3 The big unknowns (gate this work)](#a3-the-big-unknowns-gate-this-work)
- [Appendix B. Multi-Environment Replication & Change Propagation (forward-looking)](#appendix-b-multi-environment-replication--cross-environment-change-propagation-forward-looking)
  - [B.1 Why the building block is more than an HA/DR choice](#b1-why-the-building-block-is-more-than-an-hadr-choice)
  - [B.2 The dev → test → prod model](#b2-the-dev--test--prod-model)
  - [B.3 What this implies for the design](#b3-what-this-implies-for-the-design)
- [Appendix C. IBM MQ Version Strategy: 9.4 baseline, 9→10 gap analysis (forward-looking)](#appendix-c-ibm-mq-version-strategy-94-baseline-910-gap-analysis-long-term-upgrade-plan-forward-looking)
  - [C.1 Baseline assumption](#c1-baseline-assumption)
  - [C.2 The 10.0 timing wrinkle (important)](#c2-the-100-timing-wrinkle-important)
  - [C.3 EOS boundary conditions (the upgrade window)](#c3-eos-boundary-conditions-the-upgrade-window)
  - [C.4 The gap-analysis task (with a specific question to answer)](#c4-the-gap-analysis-task-with-a-specific-question-to-answer)
  - [C.5 Strategy](#c5-strategy)

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
- **Symmetric peer sites (recommended design constraint).** The two sites are
  built as **identical peers** — full 3-node HA at *both* — not a rich primary
  and a thin recovery node. The recovery site must be able to run the live
  business, with the same HA guarantees, for **as long as the primary did**.
  Roles ("which site is live") are swappable at any time with **no major
  hardware, software, or configuration change**. This is a recommendation we
  push unless the client deliberately lowers the bar. (Full rationale in §4.7.)
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
- **Scale boundary condition (drives nearly every decision below).** This is a
  **deliberately small, caged, bespoke service** — *not* a general-purpose MQ
  platform. Scale is measured by **(a) number of queue managers, (b) number of
  external sites/counterparties connected to, and (c) number of applications** —
  and on all three axes it is **small** (a handful, not hundreds). Message and
  queue *volume* within that footprint may be non-trivial, but the topology stays
  small. We explicitly **cage the design to the required use cases** and do not
  build for hypothetical future breadth or throughput. This constraint is the
  single biggest simplifier: because scale is small, **"keep it simple" wins
  ties** — the operational and supportability cost of a complex self-managed
  stack is hard to justify when the thing it would buy you (horizontal scale,
  performance headroom) is explicitly out of scope. If scale or performance *were*
  in scope, the calculus would shift and a heavier Ubuntu investment could be
  warranted — but it is **not** part of this client's expressed design. State this
  boundary plainly in any recommendation; it is *why* simplicity is weighted as
  heavily as it is.

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
biggest unknown to de-risk before the engagement begins. Everything else is
downstream.

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

### 2.7 RDQM-on-RHEL vs native HA/DR on Ubuntu — objective tradeoffs (no decision yet)

This subsection consolidates the head-to-head pros/cons as a neutral ledger to
validate by experiment — **it is deliberately not a decision.** The gut
expectation is that RDQM-on-RHEL will win (see below), but the entire point of
the Ubuntu arm (phase D) is to test that honestly: if Ubuntu demonstrates the
same functionality, we get a true apples-to-apples POC that *proves why* one is
better rather than asserting it. We are perfectly willing to be surprised and
find Ubuntu-native fully viable.

**Frame everything against the §1 scale boundary condition.** Because the
service is small and caged, the criteria that reward simplicity and
supportability dominate; the criteria that would reward a heavier self-managed
investment (scale, performance, platform independence) are out of scope. That
asymmetry is what tilts the ledger.

**Arguments *for* RDQM-on-RHEL:**

- **Far smaller vendor-supportability gap (the heaviest factor).** The operator
  can build, configure, monitor, operate, and scale MQ — but **cannot
  self-service IBM's internals**: crash dumps, FFST/FDC records, and the strange
  failure modes of a closed black box ultimately have to go back to IBM. That
  support relationship is *non-trivial* and is the kind of thing that decides a
  3 a.m. SEV-1. RDQM is supported end-to-end by IBM; a bespoke Ubuntu stack makes
  us solve a long tail of problems IBM will disclaim, for a product whose internals
  knowledge is rare. (Expanded in §3 "Vendor-supportability gap" and §6.1.)
- **No external/shared storage — the biggest single simplification.** RDQM keeps
  storage **local to each node** (DRBD block replication, shared-nothing). That
  **removes an entire layer of infrastructure** — no SAN, no LUN, no iSCSI
  target, no array-replication tier — and with it removes a critical stability
  dependency ("we are only as stable as our storage"). The Ubuntu/Pacemaker arm
  *reintroduces* exactly this shared-storage SPOF. (Expanded in §4.4 and Q4 in
  §2.5.)
- **Simplicity is the right default at this scale.** Given the small, caged
  footprint (§1), the turnkey single-vendor box is proportionate to the problem;
  a bespoke cluster is not.

**Arguments *for* / mitigations *toward* Ubuntu-native (the case to test, not dismiss):**

- **It is the client's chosen standard.** The client runs Ubuntu and has Ubuntu
  operational muscle; staying on it avoids introducing a second OS.
- **No new-OS integration risk.** The client's internal infrastructure services
  (authentication/identity, configuration management, monitoring, patching,
  logging) are very likely **designed and optimized for Ubuntu**. RHEL may
  integrate poorly or require bespoke work against those services — a real,
  **OS-specific** cost that is plausibly *why the client's own people steered
  away from Red Hat.*
- **Platform independence / no RHEL-x86-64 lock** — only matters if the out-of-scope
  scale/portability concerns ever come into scope.

**Arguments *against* RDQM-on-RHEL (the costs to weigh):**

- **The client lacks RHEL experience and chose Ubuntu.** Introducing RHEL means a
  second OS to learn, patch, secure, and integrate — against an Ubuntu-shaped
  internal-services estate (see above). The *depth* of integration actually needed
  for a small bespoke service is itself **a conversation to have with the client**
  — it may be far less than a general-purpose platform would require.
- **RHEL-x86-64 hard lock** (DRBD kmod) — the central §2.2 constraint; forces an
  x86-64 footprint and couples patching to kernel versions (§3 Day-2).

**Arguments *against* Ubuntu-native (the costs to weigh):**

- **Wide vendor-supportability gap** (the inverse of RDQM's biggest pro) — we own
  the cluster, storage, and fencing; IBM ships sample resource agents and
  disclaims the rest.
- **Reintroduces the shared-storage SPOF** and a multi-site replication tier we
  must build and prove (§2.3 arm 1, Q4).
- **Higher Day-2 burden** for a service whose small scale doesn't reward the
  investment.

**Net (still to be proven, not decided):** at this scale the ledger leans toward
**RDQM-on-RHEL** — smaller support gap, no shared-storage layer, simplicity
proportionate to a caged service — with the **client's Ubuntu standard and
RHEL-integration cost** as the genuine counterweights. The Ubuntu arm is built
specifically to convert this lean into evidence (phase D/E), and to surface any
surprise that would change it.

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
9. **Planned, reversible site role rotation** (§4.7) — a *controlled* (not
   disaster) cutover of the live role A→B, run the business live from B,
   validate full HA at B, then rotate back B→A. Confirms the DR runbook works
   on demand, that the peer can genuinely run live, and that the swap requires
   no hardware/software/config change. This is the "DR for a long stay" and
   disruptive-upgrade-via-rotation case, distinct from step 7's disaster.

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
  (The client's contractual specifics are TBD, but the public regulatory and
  DTCC-disclosed floor is now documented — see §4.6.)

### 4.6 Public & regulatory basis for the two-site DR requirement (researched)

The requirement to run **two geographically separated data centers** — which
is what *forces* a DR architecture rather than HA alone — is **not just client
preference**. It traces to public regulatory mandates and DTCC's own disclosed
posture. These are the citable floor; the client's actual contractual numbers
(TBD) will sit on top and we iterate when we have them. *(Citations gathered
from public sources 2026-06-03; verify currency against the version in force at
onboarding.)*

- **Interagency Paper on Sound Practices to Strengthen the Resilience of the
  U.S. Financial System** (FRB / SEC / OCC, **April 2003**) — the post-9/11
  origin of the mandate. Core clearing & settlement organizations target
  recovery/resumption **within ~2 hours**; firms in "significant" market roles
  should strive for a **4-hour** capability; backup sites must be
  **out-of-region** — "as far away from the primary site as necessary to avoid
  being subject to the same set of risks," not sharing the same labor
  pool/infrastructure (i.e. beyond synchronous-replication range for the most
  critical systems). *Confidence: HIGH.*
  - <https://www.sec.gov/news/press/2003-45.htm> ·
    <https://www.federalreserve.gov/boarddocs/srletters/2003/sr0309.htm> ·
    <https://www.occ.treas.gov/news-issuances/bulletins/2003/bulletin-2003-14.html>
- **SEC Regulation SCI** — 17 CFR §242.1001 & §242.1004 (adopted 2014). DTC,
  NSCC, and FICC are registered clearing agencies = **"SCI entities"**, so this
  binds DTCC directly: BC/DR must be **"sufficiently resilient and
  geographically diverse"** with next-business-day / **two-hour** resumption of
  critical systems. **§1004 cascades to members:** designated participants must
  take part in BC/DR functional testing **at least annually** — the legal basis
  for DTCC mandating member DR-test participation. *Confidence: HIGH.*
  - <https://www.law.cornell.edu/cfr/text/17/242.1001> ·
    <https://www.law.cornell.edu/cfr/text/17/242.1004>
- **FINRA Rule 4370** — the broker-dealer's *own* business-continuity duty
  (written BCP, data backup/recovery, mission-critical systems, annual review).
  No prescribed distance/RTO — deliberately flexible. Relevant as the client's
  obligation, not a gateway distance spec. *Confidence: HIGH.*
  - <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4370>
- **DTCC's own disclosed posture** — NSCC/FICC PFMI Disclosure Frameworks
  (CPMI-IOSCO Principle 17) and the public Quantitative Disclosures state a
  ~**two-hour RTO** and geographically dispersed data centers, matching the
  above. *Confidence: MEDIUM on exact wording (PDFs hard to quote cleanly —
  verify directly).* DTCC's 2025 "Data Center Rotation Test Plan" shows
  movement toward active-active operation.
  - <https://www.dtcc.com/legal/policy-and-compliance> ·
    <https://www.dtcc.com/operational-resilience>
- **Historical confirmation** — Computerworld (June 2004) reported DTCC built
  data centers **>1,000 miles apart** using EMC SRDF multihop mirroring,
  achieving **~3-hour DR with 0–30 min data loss**, explicitly citing the 2003
  Interagency Paper. Period-accurate; DTCC has since tightened toward the
  ~2-hour / near-zero-loss posture above. *Confidence: HIGH (historical).*
  - <https://www.computerworld.com/article/1702090/>

**Design consequence:** DR is non-negotiable and the target is a **~2-hour
RTO** with an **out-of-region** second site — which is precisely why
cross-site replication is **asynchronous** (§4.2) and why the
app/infrastructure reconciliation interface (§4.3) matters. Our async DR window
(seconds-scale for RDQM DR) sits comfortably inside this envelope; the residual
message-loss window is the thing the application must reconcile.

### 4.7 Symmetric peer sites & periodic role rotation (recommended design constraint)

This is *why* the lab is **fully symmetric 3+3** rather than a 3-node primary
plus a single thin recovery node, and it is a constraint to recommend to the
client unless they consciously choose to lower the bar.

**The core principle: design DR for a long stay, not a brief excursion.** Build
the recovery site on the assumption that once you fail over to it, **you may be
running there for a long time** — weeks, not minutes. That means the secondary
must carry **the same high-availability guarantees the primary had**: full
intra-site HA (3-node group), same capacity, same operability. A thin
single-node recovery target violates this — it can accept the business but
cannot *safely run* it, because the moment you're in DR you've lost your HA.

**Treat the sites as peers, not primary/secondary.** Architect for **Site A and
Site B as identical equals** whose live/standby roles are **swappable at any
time** with **no major hardware, software, or configuration change**. Even when
operationally labelled primary/secondary, the design assumption is full symmetry
and reversible role assignment for as long as required.

**Why this is worth enforcing — three concrete payoffs:**

- **Validated DR procedures.** A controlled, scheduled failover (an off-weekend,
  reversible exercise) proves the DR runbook actually works *before* a real
  disaster forces it — not a paper plan, a rehearsed one.
- **Both sites stay genuinely live-capable.** Periodically running the business
  *from* the secondary — for as long as you'd run from the primary — keeps it
  from rotting into a never-exercised cold standby. The strongest form is
  **scheduled role rotation**: run live in A until a planned cutover to B, then
  run live in B for a comparable span, and back.
- **A clean path for non-rolling, disruptive upgrades.** When a change is too
  disruptive to apply live or reversibly against an active cluster (e.g. a major
  OS/kernel/RDQM-kmod jump that can't be done in a rolling fashion), **rotate the
  live role to the peer site, upgrade the now-idle site safely, then rotate
  back.** Symmetric peers turn "scary irreversible upgrade" into a routine,
  reversible site swap.

**Relationship to RDQM mechanics.** RDQM's documented HA/DR combined model is
**3+3** (a synchronous HA group at each site, async DR between them — §4.4), so
symmetric peers are built from supported building blocks. Rotation is a *manual,
controlled* `rdqmdr` cutover (async window applies, §4.2–4.3), not automatic
WAN-spanning failover. **Note** the DR replication relationship still has a
direction at any instant; "swappable roles" means we can re-establish it the
other way (B→A) as a planned operation, not that both directions are live
simultaneously — that fully mutual case is the active/active vision in
Appendix A.

**Caveat — role rotation is DTCC-constrained, and that's a separate axis from
3+3.** The clean "run live in A for six months, planned swap, run live in B"
cadence is straightforward when **you own the whole stack end to end**. This is
not that situation. Each data center will likely have its **own physical
connectivity to DTCC** — historically leased lines, possibly secure
internet/SMART circuits today; *how it's implemented now is unknown to us* and
needs to be established. Because that connectivity terminates at DTCC, a site
swap is **not unilaterally ours to schedule** — it may require coordination with
DTCC and put us on **their** test calendar, not ours. DTCC may even mandate the
operating posture outright: e.g. *stay primary at all times, use the secondary
only on a genuine primary failure, and fail back as soon as the primary is
healthy* — i.e. classic active/standby with no elective rotation. Which model we
can actually run is **dictated by the DTCC relationship and contract**, and we
adapt to it.

**This does not weaken the 3+3 requirement — it's orthogonal.** Whether we may
*electively* run live from the secondary is an **operational** question
constrained by DTCC. Whether the secondary must be a **full 3-node HA peer** is
a **design** question, and the answer is yes regardless: if you have failed over
to the secondary, *something bad has happened at the primary, and you cannot
assume it will be repaired quickly* — you must design for a long stay with full
HA at the recovery site. DTCC constraints only tweak *how we operate* the two
sites at a given moment; they do not change the fundamental requirement that
**both sites are full-HA peers (3+3)**.

**Lab consequence:** the 3+3 topology (§5) exists precisely so we can exercise
this — scheduled cutover A→B, run live in B, validate, and cut back — as a
first-class tested procedure in the §3.1 fault/operations suite (extends step 7,
full-site DR, into a *planned, reversible* rotation, not only a disaster).

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

### 6.1 Bare metal vs virtualization (confirmed: VMs are fine)

A natural worry — RDQM ships a DRBD **kernel module**, so does it require bare
metal? **No.** Confirmed against IBM material (researched 2026-06-03):

- RDQM's only hard constraints are **RHEL + x86-64**. The kernel-module
  requirement is about the kmod matching the **running RHEL kernel version**,
  not about physical hardware — a VM runs the same kernel, so the module loads
  and behaves identically. There is **no documented bare-metal requirement**.
- IBM's stated position is **hypervisor-agnostic**: MQ "has not been
  specifically tested in virtualization environments" and IBM doesn't certify
  particular hypervisors, but MQ (and RDQM) is supported in a VM to the extent
  the underlying RHEL release is supported. (IBM historically even shipped a
  "WebSphere MQ Hypervisor Edition" for RHEL.)
- **Supportability asterisk (feeds the §3 vendor-gap criterion):** IBM's defect
  support won't help with problems "directly related to the virtualization
  environment," and for a hard case may ask you to **reproduce on a tested,
  non-virtualized configuration** before engaging. So virtualization is fully
  supported for *running* RDQM; the small gap is at the *support boundary* for
  a virtualization-layer-specific defect. Worth noting in the client's
  production decision — not a lab blocker. *Confidence: HIGH that VMs are
  permitted/workable; the support caveat is the nuance.*

This positively confirms the whole lab premise: **everything here, including
the RDQM arm, runs on VMs** — no bare-metal node is required.

**References (re-verify; some IBM pages gated at research time):**

- IBM MQ support position on virtualization & HA —
  <https://www.ibm.com/support/pages/ibm-mqs-support-position-virtualization-low-level-hardware-file-systems-networks-and-high-availability>
- RDQM kernel modules (kernel-version coupling) —
  <https://www.ibm.com/support/pages/ibm-mq-replicated-data-queue-manager-kernel-modules>
- System Requirements for IBM MQ 9.4 —
  <https://www.ibm.com/support/pages/system-requirements-ibm-mq-94>

## 7. Virtualization & Provisioning Harness

### 7.1 Decision: Vagrant orchestrates the lab

The harness is **Vagrant**. The lab's hard requirement is not VM *lifecycle* —
it is **multi-node, multi-network topology**: isolated subnets for the
client/app net, the DTCC-facing net, the per-DC data/VIP net, and crucially the
**private heartbeat and replication networks** the HA stack depends on.
Vagrant's multi-machine + network DSL models exactly this and is a mature,
widely-used, open-source tool that has solved this problem for over a decade.
Hand-rolling subnet wiring around a single-VM tool *to avoid a dependency* would
be reinventing a solved problem — the wrong kind of simplicity.

This is a deliberate divergence from Vergil's Lima choice, and the reasoning is
clean:

- **Lima is optimal for a single, special-purpose VM** — exactly the Vergil
  agent sandbox (one VM, one `/projects` mount, stripped down to contain Claude
  Code). Lima keeps that job; nothing here changes it.
- **The MQ lab is the opposite shape** — many peer nodes across simulated sites
  with real, separately-addressable networks. That is Vagrant's home turf, not
  Lima's.
- The two **coexist on the host** (see §7.4).

**Why the heartbeat/replication nets must be real, not faked:** the §3.1 fault
suite deliberately **severs the heartbeat network** and **severs replication**
to confirm correct quorum/fencing behavior and no split-brain. You cannot
credibly test "what happens when the heartbeat link dies" against one flat
simulated network — you need genuinely separate, individually-severable
interfaces. The fidelity of the entire HA/DR validation rests on getting the
network definition right, which is *precisely* why Vagrant earns its place here.

### 7.2 The Apple-Silicon provider bind (open decision — Phase-A spike)

Vagrant delegates the actual VM to a **provider**, and on Apple Silicon the
provider choice is genuinely constrained — load-bearing enough to be the first
thing **Phase A settles experimentally**. The bind: *no single provider cleanly
gives us both rich multi-NIC networking and x86-64 emulation*, and we need both
(multi-NIC for the topology; x86-64 because RDQM forces RHEL-x86-64, §2.2/§6).
*(Provider landscape researched 2026-06-03; re-verify — these plugins move fast.)*

- **`vagrant-libvirt` inside a nested Linux VM** *(leading hypothesis — see
  below)* — on Linux, libvirt is the **most mature** Vagrant provider with the
  **strongest multi-network model by far**, and it defines proper multi-NIC
  topologies for **x86-64 guests too**. Its only catch was "needs a Linux host"
  — which on this machine we simply *give* it.
- **`vagrant-qemu`** (open source) — runs arm64 guests natively (fast, HVF)
  *and* can emulate x86-64 (slow, TCG). The one host-level tool that spans both
  arches. **But** its **multiple-network-interface support is limited** — exactly
  our core need — so the topology we care about most is where it is weakest.
- **`vagrant-parallels`** — robust host-only/private-network support (good for
  the topology), arm64-native; **but Parallels on Apple Silicon does not emulate
  x86-64**, so it cannot run the RDQM arm. Commercial/paid.
- **`vagrant-vmware-desktop` + VMware Fusion** — Fusion is now free; solid
  networking; also **arm64-guest-only on Apple Silicon (no x86 emulation)**.
  ARM64 Vagrant support has historically been rough — verify current state.
- **VirtualBox** — on Apple Silicon (7.1+) it is host-capable but **arm64-guest
  only, no x86 emulation** (Oracle pulled it for poor performance), and its
  Apple-Silicon support is the **newest/roughest** of the set. No advantage over
  Parallels/Fusion here; not a contender.

**Leading hypothesis: one nested `vagrant-libvirt` harness.** Carve out a single
well-resourced Linux VM (Lima/vz, ~32–48 GB given 128 GB to spend) and run the
*entire* lab inside it under `vagrant-libvirt`. This is the **only** option that
delivers **rich, severable multi-NIC networking *and* x86-64 in one coherent,
mature provider**: arm64 arms are **KVM-accelerated via nested virtualization**,
and the RDQM x86 arm is TCG-emulated **but still gets real, individually-severable
heartbeat/replication NICs** — the exact combination nothing at the host level
offers. Bonuses: the libvirt + Ansible harness **lifts cleanly onto any real
Linux KVM host or cloud box later** (far more production-representative than a
Mac-specific provider), and it **mirrors Vergil's own two-layer model**
(agent-in-VM → containers; here Vagrant-in-Linux-VM → libvirt guests), so it is
consistent with the ecosystem rather than a one-off.

**Why nested virt makes this viable now (and its precise limit):** macOS 15
(Sequoia)+ exposes **nested virtualization on M3-and-later** hosts via
Virtualization.framework — **the M5 Max qualifies** — so a Linux VM can run KVM
and hardware-accelerate **arm64** guests. The precise limit: nested virt only
accelerates **same-architecture** (arm64-on-arm64). It does **not** speed up
x86 — x86-64 guests are QEMU/TCG **software emulation** regardless of layering.
That is fine (TCG runs inside a VM without issue); it just means the RDQM arm is
CPU-slow. *(Researched 2026-06-03; confirm macOS 15+ and that Lima passes nested
virt through — Phase-A spike.)*

**Honest caveats (record these):**

- **Nesting does not accelerate x86** — the RDQM arm is CPU-slow under TCG. Fine
  for *functional* failover/DR validation, which is all we need (§3, perf is out
  of scope).
- **Absolute RTO wall-clock from the emulated x86 arm is not representative** —
  failover *correctness* is valid, failover *timing* is not. Report emulated-arm
  RTO qualitatively; trust arm64-native timings for real numbers. (Refines §3.)
- **Two layers complicate network debugging** — when a net misbehaves, isolate
  whether it is the host→Linux-VM boundary or the Linux-VM→guest boundary.
- **Prerequisites** — macOS 15+ on M3+ (have it) and Lima nested-virt pass-through
  (confirm in Phase A).

**Fallback (if emulated x86 is too slow even for functional runs, or nested virt
won't pass through): the split harness.** Run the arm64 arms on a strong-networking
arm64-native provider locally, and put the **RDQM RHEL-x86-64 arm on a cheap x86
cloud box**, using cloud VPC subnets in place of the private nets — which
sidesteps both the local emulation-speed penalty and the host-qemu multi-NIC gap.

**Service-surface minimization (worth a day — applies either way).** Strip the
guest OSes hard: mask the default junk a full distro runs that a lab node never
needs — USB/device discovery, `multipathd`, unused iSCSI initiator, ModemManager,
telemetry/`apt-daily` timers (**RHEL especially ships a lot**). Direct precedent
exists in `vergil-vm`'s service-minimization pass (it already masks `open-iscsi`,
`multipathd`, `ModemManager`, et al.); we reuse that approach on both the outer
Linux VM and the guest nodes. Leaner guests matter doubly under emulation.

### 7.3 Configuration via Ansible

Vagrant only stands up and networks the bare VMs. The **real MQ HA/DR install,
configuration, and operational tooling — the actual product (§8) — is done in
Ansible** (via Vagrant's Ansible provisioner) over SSH. This keeps the
deliverable **independent of the harness**: the same playbooks that configure a
Vagrant VM here run against real client hardware later, with no
Vagrant/Lima/provider assumptions baked in. The harness is disposable (§0); the
playbooks are not.

### 7.4 Relationship to Vergil and the host

This repo is the deliberate **Vergil sandbox exception**: it runs **directly on
the MacBook host**, *not* inside the Vergil agent VM, because it must itself
create and manage VMs (you cannot usefully nest the lab VMs inside the
single-purpose Vergil VM). So on the host, two virtualization tools coexist by
design: **Lima** runs the Vergil agent VM (where Claude Code is sandboxed for
*other* repos), and **Vagrant** runs the MQ lab VMs as host-level siblings. We
still reuse Vergil's conventions where they transfer — Ubuntu LTS base,
provisioning patterns, and the `make docs` documentation-site layout — once this
repo is Vergil-adopted.

### 7.5 Sizing budget

The full 3+3 topology plus DTCC sim, client, and (for the Pacemaker arm) witness
+ iSCSI target is ~8–10 VMs at ~1 GB each. Under the leading nested-libvirt model
(§7.2) those live *inside* one Linux VM, so size that outer VM generously —
~**32–48 GB** of the M5 Max's 128 GB leaves comfortable headroom. Native arm64
guests run KVM-accelerated; x86-64 (required for RDQM) is TCG-emulated, accepted
as slower since RDQM validation is functional, not performance.

## 8. The Tooling (the actual product)

*(to expand: QM install/config automation; HA-setup automation per approach;
DR setup + cutover/failback automation; operational standards — runbooks,
health checks, backup; eventual `.deb` / `.rpm` packaging.)*

## 9. DTCC Simulation & Validation

The `dtcc-sim` fixture mimics DTCC's server side so we can validate the message
path and DR behavior end to end. The public record gives us enough to make the
simulation **realistic in shape** (the exact per-service formats and endpoints
are delivered per-client at onboarding and are not public — so we simulate the
*pattern*, not a real DTCC interface). *(Grounded in public DTCC material
researched 2026-06-03; see §9.3 references.)*

### 9.1 Connectivity model to mirror (from the public FICC EPN MQ guide)

- **Distributed queuing**, not client/server: the firm's queue manager and the
  DTCC queue manager exchange messages via **sender/receiver channels** with
  **local queues, remote-queue definitions, and transmission queues** on each
  side. The sim therefore runs its own QM with reciprocal channel definitions
  back to the in-house QM.
- **Application-level fixed-format header inside the message body** (distinct
  from the MQMD): blank-padded, left-justified fields — e.g. Password, Sender
  (the firm's DTCC account ID), Receiver (a fixed service mnemonic), and
  business date — followed by service-specific **ACK / reject codes** (e.g.
  header-validation failure, stale business date). The responder app validates
  and ACKs this header so we exercise realistic reject/replay handling.
- **Per-client password auth carried in the header**; **a single connection
  ID** per client (multiple IDs cause duplicate delivery on the same channel);
  legacy TCP/CTCI and MQ must **not** be active simultaneously for one account.
- **DTCC-side resiliency feature worth modeling:** DTCC can deliver a client's
  inbound messages into **multiple queues** to support the client's
  resiliency/DR — a useful pattern to reflect in the DR tests (§3.1 step 7).

### 9.2 Transport & security context (real-world, for fidelity notes)

In production, MQ to DTCC runs over a **dedicated SMART circuit** (new-circuit
lead times ~12–14 weeks — a *schedule* risk, not a lab one), and DTCC enforces
**channel security/encryption standards (TLS)** per Important Notice GOV1683-24
(mandatory since 2024-12-31; members register their MQ channel name;
non-compliant connections are disconnected). The lab need not replicate SMART,
but the tooling and standards **must** produce a TLS-secured channel
configuration so what we build is onboarding-ready.

### 9.3 Validation

Fault-injection failover tests and DR cutover tests (per §3.1) run trades
through `app-client → in-house QM → dtcc-sim → responder → back`, proving
message integrity and measuring RTO/RPO across both HA failover and full-site
DR cutover.

**References (public; verify per-service at onboarding):**

- FICC EPN MQ Implementation Guide (DTCC, "Public/White") —
  <https://www.dtcc.com/-/media/Files/Downloads/Clearing-Services/FICC/MBSD/EPN-MQ-Implementation-Guide.pdf>
- Important Notice GOV1683-24 (connectivity security standards, incl. MQ) —
  <https://www.dtcc.com/-/media/Files/pdf/2024/4/19/GOV1683-24.pdf>
- DTCC Settlement Service Guide (MQ used on the DTC settlement side) —
  <https://www.dtcc.com/globals/pdfs/2018/february/27/service-guide-settlement>

**Caveat:** message header layouts and ACK codes are **per-service** (FICC EPN
/ MBSD vs DTC settlement vs NSCC/UTC). QM names, channel names, ports, and IP
endpoints are **not public** and arrive per-client during onboarding — the sim
must not hardcode any assumed real values.

## 10. Phasing

Sub-projects, each its own spec → plan → build. **DR is baked into each arm
from the start** — we do not build HA and then bolt DR on; an arm is only
"done" when it has a demonstrated cross-site DR story (per §1, design for the
full 3+3 architecture up front).

- **A.** Virtualization harness (multi-site, multi-network, {os,arch}-parameterized)
  — **Vagrant**; start here. First task is the **provider spike** (§7.2): confirm
  the leading **nested `vagrant-libvirt`** model — Lima nested-virt pass-through
  on this M5/macOS, severable heartbeat/replication nets, and acceptable
  TCG-emulated x86 for the RDQM arm — with the cloud-x86 split as the fallback.
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

**Platform / lab:**

- RHEL developer licensing and whether **RDQM** is usable under it.
- x86-64 emulation speed on the M5 Max (RDQM forces RHEL-x86-64).
- **Vagrant provider bind on Apple Silicon** (§7.2): the strong-networking
  providers (Parallels, VMware Fusion, VirtualBox) don't emulate x86-64, and the
  x86-capable host tool (`vagrant-qemu`) has limited multi-NIC support. Leading
  resolution is a **nested `vagrant-libvirt`** harness (arm64 KVM-accelerated via
  M3+ nested virt; x86 TCG-emulated but properly networked); fallback is a
  cloud-x86 split. Depends on macOS 15+ nested-virt pass-through via Lima —
  confirm in the Phase-A spike.
- SAN / shared storage as the weak link in the Pacemaker arm (the SPOF RDQM
  avoids).
- ARM caveats — no MQTT/AMQP on the ARM64 MQ build.

**DR / message integrity:**

- Cross-site synchronous replication is impractical → residual DR message-loss
  window; the app/DTCC reconciliation path (§4.3) must close it.
- Target envelope is now grounded (§4.6): **~2-hour RTO, out-of-region** —
  the client's exact contractual numbers remain TBD.

**DTCC-specific (grounded in §9, but with real gaps):**

- **Which DTCC service** the client clears/settles through (FICC EPN, DTC
  settlement, NSCC/UTC, …) determines message header formats and ACK codes —
  **unknown** until the client tells us. The sim models the *pattern*, not a
  specific service's wire format.
- **Channel security:** DTCC mandates TLS on the MQ channel (GOV1683-24) — the
  tooling must emit an onboarding-ready, TLS-secured channel config.
- **Schedule risk:** a new dedicated SMART circuit has a ~12–14 week lead time;
  irrelevant to the lab but material to the client's production rollout plan.
- **Open gap — no public mandate for dual/diverse member MQ circuits.** That
  specific requirement (if it exists) lives in DTCC's **gated, internally
  classified DR Guide** and per-client onboarding packets, not public material.
  We treat member-side dual-site connectivity as best practice and a **question
  to confirm with the client/DTCC**, *not* a citable public requirement.
- QM names, channel names, ports, and endpoints are delivered per-client at
  onboarding — never hardcode assumed values.

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

---

## Appendix C. IBM MQ Version Strategy: 9.4 baseline, 9→10 gap analysis, long-term upgrade plan (forward-looking)

> **Scope note.** A third-order, *strategic* concern — not part of Deliverable
> #1, but it must be captured now because the version we build on has a finite
> support life and DTCC will not retire a connection just because IBM end-of-lifes
> a release. We design on a proven baseline and plan the upgrade around hard
> vendor boundary conditions. *(Version/date facts researched 2026-06-03; treat
> dates as approximate and re-verify at IBM's lifecycle pages before acting.)*

### C.1 Baseline assumption

The design targets **IBM MQ 9.4 LTS** (GA 2024-06-18; latest CD update in the
9.4 stream as of research, ~9.4.5). Rationale: it is the **most proven, stable,
widely-deployed** current LTS — the right thing to go live on, not the
bleeding edge. RDQM ships in MQ Advanced and remains, to our knowledge,
**RHEL-x86-64-only** in 9.4 (the central constraint behind §2).

### C.2 The 10.0 timing wrinkle (important)

**IBM MQ 10.0 (LTS) was announced 2026-04-21 and goes GA 2026-06-16** (z/OS
2026-06-19) — fittingly, **the day after the contract engagement begins
(2026-06-15)**. (Nothing of ours "goes live" on that date; it is simply the
contract start. The coincidence is just that 10.0 ships into the world the day
after.) The version question is still a deliberate decision point:

- **Build on 9.4, not 10.0.** A tier-one clearing connection should not be
  founded on a release that shipped *days* earlier — that maximizes risk for
  zero upside. 9.4 LTS has years of runway (C.3) and a deep field-proven track
  record, so it is the right baseline to develop tooling against and the right
  thing for the client to eventually run in production. Treat 10.0 as a
  **planned, tested upgrade**, not a foundational bet.
- But 10.0's existence changes the long-game, so part of arriving prepared is
  bringing a written 9→10 gap analysis and upgrade plan.

### C.3 EOS boundary conditions (the upgrade window)

- **MQ 9.3 LTS** — End of Service **2027-09-30**.
- **MQ 9.4 LTS** — ~5-year support from 2024-06 GA → EOS **~2029-06**; optional
  paid Extended Support up to ~4 more years (~2033).
- **MQ 10.0 LTS** — fresh 5-year clock from 2026-06 GA.

So a go-live on 9.4 gives a comfortable runway to **~2029** (longer with paid
extended support). The 9→10 upgrade must land **inside** that window — early
enough to be unhurried, before 9.4 support lapses.

### C.4 The gap-analysis task (with a specific question to answer)

A documented **9.4 → 10.0 gap analysis** is an explicit deliverable. The
headline question, directly relevant to the §2 thesis:

> **Did 10.0 broaden RDQM platform support beyond RHEL x86-64** (e.g. to Ubuntu
> or other Linuxes), or relax the DRBD-kernel-module coupling?

**Research so far (2026-06-03): no evidence it did — and the signal points the
other way.** IBM's full 10.0 system-requirements page was not retrievable at
research time (HTTP 403), but every available 10.0 summary leads with **Native
HA and CRR (Cross-Region Replication) for cloud-native, zero-downtime**
deployments as the modern HA/DR direction — *not* a broadening of bare-metal
RDQM. This is consistent with the structural incentive: **IBM owns both Red Hat
and MQ**, so keeping turnkey RDQM RHEL-only nudges customers toward RHEL. Our
working assumption: **RDQM stays RHEL-x86-64 in 10.0; the cloud-native HA story
advances via Native HA/CRR (containers/Kubernetes), which remains out of scope
for the bare-VM premise (§2.3).** *Confidence: MEDIUM — confirm against the
official 10.0 System Requirements and the RDQM kernel-modules page before
relying on it.* If a future version ever did extend RDQM to Ubuntu, it would
**resolve the core tension in §2** — so re-ask this question at every major
version.

### C.5 Strategy

- Build and go live on **9.4 LTS**.
- Deliver the **9→10 gap analysis** early: what 10.0 buys us, especially any
  **HA/DR** or **vendor-supportability** gains (§3), and whether Native HA/CRR
  changes the recommendation if the client ever accepts containers.
- Produce a **documented, tested 9.4→10.0 upgrade runbook** as part of the
  operational standards (§8), scheduled to complete inside the C.3 window and
  ahead of any DTCC- or IBM-driven requirement to move.
- Re-verify all dates and the RDQM-platform question against IBM's lifecycle
  and System Requirements pages — vendor claims get the same trust-but-verify
  treatment as everything else.

**References (re-verify; some IBM pages were gated at research time):**

- Introducing IBM MQ v10.0 — <https://www.ibm.com/new/announcements/introducing-ibm-mq-v10-0>
- IBM MQ lifecycle / EOS dates — <https://www.ibm.com/support/pages/lifecycle/details/?q45=mQ>
- RDQM kernel modules (platform coupling) — <https://www.ibm.com/support/pages/ibm-mq-replicated-data-queue-manager-kernel-modules>
