# MQ Cluster Tooling — Lab & Tooling Design

> **Status:** living document, expanded iteratively during brainstorming.
> **Date:** 2026-06-03
> **Author:** Phillip Moore (with Claude)
> **Context:** Pre-engagement R&D for a client — a large financial services
> app bringing IBM MQ (SVC messaging connectivity) app off a third-party
> provider. The client standardizes on Ubuntu Linux and wants to own the
> service. The workload is **back-office post-trade messaging/settlement** with
> SVC — *not* real-time trade execution — so uptime/latency/throughput
> requirements differ from front-office systems and are **TBD** pending
> client/SVC input. This repo is a personal home-lab harness on an Apple M5
> Max (128 GB, arm64) used to develop and validate portable MQ HA/DR tooling
> **before the contract engagement begins (2026-06-15)**. The aim is to arrive
> with the knowledge, hands-on understanding, and ideally some working tooling
> to hit the ground running on day one — *not* a production go-live. Any
> production rollout happens later, on the client's own timeline.
>
> **Anonymization note:** this is deliberately generic, non-proprietary
> industry work. A large enterprise needing to clear post-trade with SVC over
> IBM MQ on a Linux cluster is a common, well-understood use case — none of it
> is client-specific. The specific client is intentionally **not named** so
> this R&D can be shared. Keep it that way: refer to "the client" / "the
> enterprise," never the app.

---

## Decision & Pivot (2026-06-15) — supersedes the two-arm comparison framing below

> **Status:** authoritative as of 2026-06-15. Where this section conflicts with
> the "undecided comparison" framing in §0–§2 and §10, this section wins. The
> body below is preserved as the R&D record that led here — read it as
> *evidence*, not as an open question.

**The platform decision is made: RHEL + RDQM.** On engagement day one the app
confirmed it standardizes on RHEL and will run IBM MQ HA/DR on **RDQM**, driven
by **IBM-supportability concerns** — precisely the criterion this design already
weighted most heavily (§2.7; §3 "vendor-supportability gap"). The decision
*validates* the R&D lean rather than reversing it: the comparison thread did its
job and independently pointed at RDQM for the same reason the app did.

**Consequences for this document:**

1. **RDQM/RHEL is the priority arm and the first deliverable** — no longer "the
   likely front-runner, to be proven," but the chosen architecture to build out.
2. **The two-arm comparison is not retired — it is elevated to a standing
   property.** Ubuntu/Pacemaker remains a **first-class, co-maintained,
   co-tested** secondary arm. Phase E (§10) stops being a one-shot recommendation
   writeup and becomes a **continuously re-assertable parity result** produced by
   a cross-arm parity harness. We maintain parity, not a frozen snapshot.
3. **"Arm" becomes a first-class, open abstraction — N arms, not two.** The lab
   is being refactored so the HA/DR mechanism, the OS host-prep platform, and the
   substrate (VM vs container/K8s) are independent, pluggable axes — an **arm
   registry** — rather than a hardcoded `rdqm | pcmk` choice. Four arms are
   anticipated:

   | Arm | Mechanism | OS | Substrate | Status |
   |---|---|---|---|---|
   | `pcmk-ubuntu` | Pacemaker/SAN | Ubuntu | VM | built |
   | `rdqm-rhel` | RDQM | RHEL | VM | **priority — this pivot** |
   | `nativeha-rhel` | IBM MQ Native HA | RHEL/Linux | container/K8s | slot only |
   | `pcmk-debian` | Pacemaker/SAN | Debian (Trixie / 13) | VM | slot only |

   This **un-parks the Native HA arm** (§2.3 arm 3): the app already runs IBM MQ
   Native HA, so it is a real future arm, not a hypothetical. **Debian (Trixie)**
   is added because the app's actual base OS is Debian, not Ubuntu; the Pacemaker
   backend ports to it with near-trivial L0 changes (Ubuntu is Debian-derived).
   **Guardrail:** no build work on the Native HA or Debian slots until the
   framework is proven on the `rdqm-rhel` + `pcmk-ubuntu` pair.

**Transition design.** The full pivot — wrap-up of in-flight work, the
arm-backend abstraction, the cross-arm parity harness, and the phased path to
RDQM-at-parity — is specified in
[`2026-06-15-rdqm-parity-pivot-design.md`](2026-06-15-rdqm-parity-pivot-design.md).

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
  - [2.7 RDQM-on-RHEL vs native HA/DR on Ubuntu — objective tradeoffs (no decision yet)](#27-rdqm-on-rhel-vs-native-hadr-on-ubuntu--objective-tradeoffs-no-decision-yet)
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
  - [7.4 Development model — one large, persistent VM for dev and lab](#74-development-model--one-large-persistent-vm-for-dev-and-lab)
  - [7.5 Sizing budget](#75-sizing-budget)
- [8. The Tooling (prototype that proves the design)](#8-the-tooling-prototype-that-proves-the-design)
  - [8.1 What the tooling is — and is not](#81-what-the-tooling-is--and-is-not)
  - [8.2 Layered structure](#82-layered-structure)
  - [8.3 Queue-manager install, REST enablement & content](#83-queue-manager-install-rest-enablement--content)
  - [8.4 HA setup automation (per arm)](#84-ha-setup-automation-per-arm)
  - [8.5 DR setup, cutover & failback](#85-dr-setup-cutover--failback)
  - [8.6 Operational standards](#86-operational-standards)
  - [8.7 Recovery & diagnostics](#87-recovery--diagnostics)
  - [8.8 Packaging — optional, only if it earns its keep (Phase F)](#88-packaging--optional-only-if-it-earns-its-keep-phase-f)
  - [8.9 Design principles (the through-line)](#89-design-principles-the-through-line)
- [9. SVC Simulation & Validation](#9-svc-simulation--validation)
  - [9.1 Connectivity model to mirror (from the public SVC FFH MQ guide)](#91-connectivity-model-to-mirror-from-the-public-svc-ffh-mq-guide)
  - [9.2 Transport & security context (real-world, for fidelity notes)](#92-transport--security-context-real-world-for-fidelity-notes)
  - [9.3 Validation](#93-validation)
- [10. Phasing](#10-phasing)
- [11. Risks & Open Questions](#11-risks--open-questions)
- [Appendix A. Likely Final Recommendation: Dual-Path, Multi-QM Active/Active (forward-looking)](#appendix-a-likely-final-recommendation-dual-path-multi-qm-activeactive-forward-looking)
  - [A.1 The pattern: parallel A/B flows across two live data centers](#a1-the-pattern-parallel-ab-flows-across-two-live-data-centers)
  - [A.2 Mapping to the "six Linux servers" hint (validated model)](#a2-mapping-to-the-six-linux-servers-hint-validated-model)
  - [A.3 The big unknowns (gate this work)](#a3-the-big-unknowns-gate-this-work)
- [Appendix B. Multi-Environment Replication & Cross-Environment Change Propagation (forward-looking)](#appendix-b-multi-environment-replication--cross-environment-change-propagation-forward-looking)
  - [B.1 Why the building block is more than an HA/DR choice](#b1-why-the-building-block-is-more-than-an-hadr-choice)
  - [B.2 The dev → test → prod model](#b2-the-dev--test--prod-model)
  - [B.3 What this implies for the design](#b3-what-this-implies-for-the-design)
- [Appendix C. IBM MQ Version Strategy: 9.4 baseline, 9→10 gap analysis, long-term upgrade plan (forward-looking)](#appendix-c-ibm-mq-version-strategy-94-baseline-910-gap-analysis-long-term-upgrade-plan-forward-looking)
  - [C.1 Baseline assumption](#c1-baseline-assumption)
  - [C.2 The 10.0 timing wrinkle (important)](#c2-the-100-timing-wrinkle-important)
  - [C.3 EOS boundary conditions (the upgrade window)](#c3-eos-boundary-conditions-the-upgrade-window)
  - [C.4 The gap-analysis task (with a specific question to answer)](#c4-the-gap-analysis-task-with-a-specific-question-to-answer)
  - [C.5 Strategy](#c5-strategy)

---

## 0. Framing (non-negotiable)

**The primary deliverable is the *design* — and the evidence to back it.** The
real value carried into the client is a validated HA/DR design for running a
*redundant* IBM MQ queue manager on Linux clusters (intra-site high availability
*and* cross-site disaster recovery), together with **demonstrated, evidence-based
answers** to what the strategy should be and what the tradeoffs are. We arrive
not with "here is an idea, might it work" but with proven concepts and
demonstrated feasibility — so client-side work becomes *porting working code to
their infrastructure*, not starting cold.

**The tooling is a generically-written prototype, not the client's production
code.** It exists to prove the concepts. It is **not expected to be reused
as-is** — the client's environment is tightly coupled to their own internal
infrastructure and tooling (they may not use Ansible; we don't assume they will,
and we don't mind), so we expect to re-implement to fit it. If some piece turns
out usable as-is, that is a bonus, not the plan.

**The lab is durable, reusable R&D infrastructure — and *that* outlasts the
engagement.** The virtualization lab is a disposable *per-run* harness, but the
**model** is a long-lived, abstract test bed: any engineer can replicate the
deployment architecture on their own laptop and experiment freely with **zero
real-hardware constraints** (real hardware comes later; we will try to keep the
tooling comparable). The model is meant to be extended — other miniature
queue-manager networks and structures, and future concerns like monitoring and
integration with the client's systems (deferred until that environment is
known).

**Deliverable #1 (the foundation):** demonstrate that we can stand up a
*solid, redundant, basic* queue manager and document *exactly* how to set it
up and operate it — before any message-pumping, configuration breadth, or
performance testing. Get this right first; everything else builds on it.

**This is not a cheap-it-out exercise.** the client is a tier-one app. We engineer
this with the most modern, strategic stack that meets the requirements — not
the lowest-cost option that technically works.

---

## 1. North Star & Solution Scope

- **The validated HA/DR design — proven by prototype tooling — is the product
  (§0).** The portable config/standards/patterns are part of that proof; the lab
  is durable, reusable R&D infrastructure, not a throwaway. The prototype tooling
  is not assumed to deploy as-is at the client.
- **The queue-manager-facing tooling is built on `pymqrest`** — the author's own
  typed Python wrapper over the IBM MQ admin REST API (the same code that helped
  win this engagement). **Exercising and showcasing `pymqrest` on a real
  HA/DR workload is an explicit secondary goal** of this project, alongside the
  primary client deliverable. (Mechanics and the two-plane split in §8.)
- **Enabling the MQ administrative REST API on every queue manager is a hard
  requirement** — it is the boundary between the Ansible/bootstrap bring-up plane
  and the `pymqrest` content plane (§8.1). "On every queue manager" means REST is
  **available and addressable on the data plane**: mqweb is data-plane
  infrastructure co-located with the QM, not a management-plane service (see §8.3
  and epic #39).
- **Security configuration is explicitly out of scope (for now).** We are testing
  **functionality and resiliency**, not the security of the configuration.
  Channels and the REST API run with whatever minimal/relaxed security is most
  convenient — deliberately, to cut setup complexity on a dimension we are not
  evaluating. This is *not* a recommendation to run insecurely in production:
  securing MQ for SVC is a **separate, requirements-driven effort** that cannot
  begin until we know *which* of MQ's many channel-security mechanisms SVC (or
  the client) mandates (TLS standards per those security standards, security exits, etc. — see
  §11). If security testing becomes a goal, it gets its own experiments.
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
  MQ is a closed-source black box; for a tier-one app we *must* be able to get
  IBM at the table for a SEV-1. Every architecture is judged partly on how far
  it deviates from what IBM will support (see §3). A self-managed Ubuntu cluster
  that IBM disclaims is a serious mark against it, however elegant.
- **Workload is back-office post-trade messaging/settlement with SVC** — not
  front-office trade execution. Performance/throughput/latency are *not* the
  priority; correctness, recoverability, and failover behavior are. Exact
  uptime/RPO/RTO targets are **TBD** pending SVC/the client requirements, and the
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
- **Q6 — App/infra DR interface:** What must the application (and the SVC
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
- **No external/shared storage — emerging as one of the single strongest
  arguments.** RDQM keeps storage **local to each node** (DRBD block replication,
  shared-nothing). That **removes an entire layer of infrastructure** — no SAN, no
  LUN, no iSCSI target, no array-replication tier — and with it removes a critical
  stability dependency ("we are only as stable as our storage"). The
  Ubuntu/Pacemaker arm *reintroduces* exactly this shared-storage SPOF. This is
  partly **scar tissue**: prior production pain with a NAS/NFS tier whose vendor
  support was poor, which is precisely the failure class a shared-nothing design
  eliminates. Combined with the **keep-the-whole-stack-as-simple-as-possible**
  thesis (§1 scale boundary), the storage argument is shaping up to be one of the
  headline reasons to favor RDQM — not a footnote. (Expanded in §4.4 and Q4 in
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
  tier-one app running a black-box product, a wide gap is a serious liability —
  when (not if) we hit an outage that needs IBM, we must be inside, or close to,
  their supported envelope. Measured concretely: which components IBM supports,
  which they disclaim, and what we'd have to prove/rebuild before they'll engage.
- **Recoverability & diagnostics** — when it breaks, can we (a) recover service
  and (b) capture the diagnostics IBM needs to drive a SEV-1? Each arm is
  assessed on how cleanly it produces `runmqras` archives, FFST/FDC records,
  and cluster/replication state for hand-off to IBM support. Tooling to gather
  this is part of the product (see §8).
- **Performance overhead** *(out of scope for the comparison)* — this is
  back-office messaging, not front-office execution; throughput/latency are not
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
  the SVC protocol must cooperate** to close the gap — sequence numbering,
  acknowledgements, idempotent processing, **replay / re-request of
  unconfirmed trades**, and end-of-day reconciliation.
- This is a **cross-team conversation** (infra ↔ application ↔ possibly SVC),
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
  tolerance (TBD — needs SVC/vendor requirements)?
- What is the cross-site RTO and the cutover procedure (manual vs automated)?
- Failback to primary after a DR event without data loss or split-brain.
- What are SVC's absolute requirements for resilience and message integrity?
  (The client's contractual specifics are TBD, but the public regulatory and
  SVC-disclosed floor is now documented — see §4.6.)

### 4.6 Public & regulatory basis for the two-site DR requirement (researched)

The requirement to run **two geographically separated data centers** — which
is what *forces* a DR architecture rather than HA alone — is **not just client
preference**. It traces to public regulatory mandates and SVC's own disclosed
posture. These are the citable floor; the client's actual contractual numbers
(TBD) will sit on top and we iterate when we have them. *(Citations gathered
from public sources 2026-06-03; verify currency against the version in force at
onboarding.)*

- **Interagency Paper on Sound Practices to Strengthen the Resilience of the
  U.S. Financial System** (FRB / SEC / OCC, **April 2003**) — the post-9/11
  origin of the mandate. Core messaging & settlement organizations target
  recovery/resumption **within ~2 hours**; apps in "significant" market roles
  should strive for a **4-hour** capability; backup sites must be
  **out-of-region** — "as far away from the primary site as necessary to avoid
  being subject to the same set of risks," not sharing the same labor
  pool/infrastructure (i.e. beyond synchronous-replication range for the most
  critical systems). *Confidence: HIGH.*
  - <https://www.sec.gov/news/press/2003-45.htm> ·
    <https://www.federalreserve.gov/boarddocs/srletters/2003/sr0309.htm> ·
    <https://www.occ.treas.gov/news-issuances/bulletins/2003/bulletin-2003-14.html>
- **SEC Regulation SCI** — 17 CFR §242.1001 & §242.1004 (adopted 2014). DTC,
  NSCC, and SVC are registered messaging agencies = **"SCI entities"**, so this
  binds SVC directly: BC/DR must be **"sufficiently resilient and
  geographically diverse"** with next-business-day / **two-hour** resumption of
  critical systems. **§1004 cascades to members:** designated participants must
  take part in BC/DR functional testing **at least annually** — the legal basis
  for SVC mandating member DR-test participation. *Confidence: HIGH.*
  - <https://www.law.cornell.edu/cfr/text/17/242.1001> ·
    <https://www.law.cornell.edu/cfr/text/17/242.1004>
- **FINRA Rule 4370** — the broker-dealer's *own* business-continuity duty
  (written BCP, data backup/recovery, mission-critical systems, annual review).
  No prescribed distance/RTO — deliberately flexible. Relevant as the client's
  obligation, not a gateway distance spec. *Confidence: HIGH.*
  - <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4370>
- **SVC's own disclosed posture** — NSCC/SVC PFMI Disclosure Frameworks
  (CPMI-IOSCO Principle 17) and the public Quantitative Disclosures state a
  ~**two-hour RTO** and geographically dispersed data centers, matching the
  above. *Confidence: MEDIUM on exact wording (PDFs hard to quote cleanly —
  verify directly).* SVC's 2025 "Data Center Rotation Test Plan" shows
  movement toward active-active operation.
- **Historical confirmation** — Computerworld (June 2004) reported SVC built
  data centers **>1,000 miles apart** using EMC SRDF multihop mirroring,
  achieving **~3-hour DR with 0–30 min data loss**, explicitly citing the 2003
  Interagency Paper. Period-accurate; SVC has since tightened toward the
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

**Caveat — role rotation is SVC-constrained, and that's a separate axis from
3+3.** The clean "run live in A for six months, planned swap, run live in B"
cadence is straightforward when **you own the whole stack end to end**. This is
not that situation. Each data center will likely have its **own physical
connectivity to SVC** — historically leased lines, possibly secure
internet/SMART circuits today; *how it's implemented now is unknown to us* and
needs to be established. Because that connectivity terminates at SVC, a site
swap is **not unilaterally ours to schedule** — it may require coordination with
SVC and put us on **their** test calendar, not ours. SVC may even mandate the
operating posture outright: e.g. *stay primary at all times, use the secondary
only on a genuine primary failure, and fail back as soon as the primary is
healthy* — i.e. classic active/standby with no elective rotation. Which model we
can actually run is **dictated by the SVC relationship and contract**, and we
adapt to it.

**This does not weaken the 3+3 requirement — it's orthogonal.** Whether we may
*electively* run live from the secondary is an **operational** question
constrained by SVC. Whether the secondary must be a **full 3-node HA peer** is
a **design** question, and the answer is yes regardless: if you have failed over
to the secondary, *something bad has happened at the primary, and you cannot
assume it will be repaired quickly* — you must design for a long stay with full
HA at the recovery site. SVC constraints only tweak *how we operate* the two
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
  automatic failover. Holds the QM's virtual IP that clients and SVC channels
  attach to.
- **DC-B (recovery):** 3-node HA group (`node-b1/b2/b3`) as the async DR
  target — full 3+3, production-grade (not a single recovery node).
- **Inter-site WAN:** simulated `net-wan` link between the DCs, with optional
  injected latency, carrying the asynchronous DR replication.
- **Networks (per the validated topology diagram):** SVC-facing net; per-DC
  data/VIP net; per-DC private heartbeat/replication net; client/app net.
- **Fixtures run as containers, not VMs.** VMs are reserved for the thing that
  genuinely needs them — the HA cluster nodes (real kernel, DRBD, Pacemaker,
  multi-NIC). The fixtures do not:
  - `svc-sim` QM (server side, sender/receiver channels back to the app
    messaging QM) runs as a **container** (`icr.io/ibm-messaging/mq`), lifted
    almost directly from the `mq-rest-admin-dev-environment` prior art, on the
    containerd/nerdctl runtime the §7.4 dev+lab VM already provides.
  - `app-client` (requester puts trades → app QM; responder replies to SVC
    traffic) likewise runs as a **container**. Its message path uses a **native
    MQI client** (e.g. `pymqi`) over a SVRCONN channel — *not* `pymqrest`, which
    is admin-only (§8.1, §9).
  - Both containers attach to the relevant libvirt networks (SVC-facing,
    client) alongside the cluster VMs.

**Sizing:** this is a *functional* lab — failover correctness and behavior,
not throughput. Each cluster-node VM is small (~**1 GB RAM**); the full 3+3
plus the containerized fixtures fits comfortably on the M5 Max (128 GB). The
Pacemaker/SAN (Ubuntu) arm adds a qdevice/witness and an iSCSI target VM in the
same network shape.

See the validated topology diagram committed alongside this spec —
[`diagrams/topology-rdqm-ha-dr.html`](diagrams/topology-rdqm-ha-dr.html) —
which shows both the single-QM HA+DR building block (panel ①) and the
active/active two-block composition (panel ②).

## 6. OS / Arch Build Matrix

The harness skeleton is identical across the matrix — same node count, same
network shape (§5), same Ansible entry points (§7.3) — with only the **OS image
swapped**. Where Ubuntu and RHEL diverge is confined to the **L0 host-prep layer
(§8.2):** package source and install mechanics (`apt`/`.deb` vs `dnf`/`.rpm`),
the **kernel-module coupling** (RDQM's DRBD module is RHEL-x86-64-only — the row
that forces the emulation/cloud choice below), firewall tooling, and service
management. Above L0 the content converges; that convergence is exactly what the
two-arm comparison (§10-E) depends on.

| OS    | arch   | HA/DR exercisable                                  | Host method on M5 Max |
|-------|--------|----------------------------------------------------|-----------------------|
| Ubuntu| arm64  | Pacemaker/Corosync + SAN/iSCSI; DR via DRBD/Booth  | native, fast          |
| Ubuntu| x86-64 | Pacemaker/Corosync + SAN/iSCSI; DR via DRBD/Booth  | emulation or cloud    |
| RHEL  | arm64  | Pacemaker/Corosync (no RDQM)                       | native, fast          |
| RHEL  | x86-64 | Pacemaker/Corosync, **RDQM (HA + DR)**             | emulation or cloud    |

Consequence: faithfully testing **RDQM forces RHEL-x86-64**, which on this
Mac means emulation or a cheap cloud x86 box.

**Decision: stay local under emulation, by intent.** A real goal is that the
whole lab runs on the laptop with **no connectivity** (developing and running
integration tests offline — e.g. on the commute). So local TCG-emulated x86 is
the *primary* path, not a stopgap, and the cloud-x86 split (§7.2) is **break-glass**:
reached only if emulation proves genuinely infeasible, which we will discover
fast (the setup either works step-by-step or hits an obvious wall).

**What this scopes the evidence to.** RDQM validation here is **functional /
logical correctness only** — does failover happen, does DR cutover/failback
produce the right state — *not* timing or throughput. We explicitly **do not
tune cluster timers to mask emulation jitter**: faking the timing would make the
config unrepresentative, and real tuning belongs on real hardware later. Results
are reported with that scope stated. If emulation jitter produces behavior we
can't distinguish from a real fault (spurious fencing, split-brain), that itself
is the signal to switch that arm to cloud-x86 — not to tune around it.

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

**This extends to the real deployment, not just the lab.** The expectation is
that the production build also runs on **VMs in the client's environment, not
bare metal** — that is the design everyone wants, and it is consistent with
IBM's hypervisor-agnostic position above (if RDQM truly required bare metal, IBM
would struggle to sell it). The lab therefore validates a substrate shape we
intend to carry forward, not a lab-only shortcut. *(The §3 support-boundary
caveat still applies and belongs in the client's production decision.)*

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
client/app net, the SVC-facing net, the per-DC data/VIP net, and crucially the
**private heartbeat and replication networks** the HA stack depends on.
Vagrant's multi-machine + network DSL models exactly this and is a mature,
widely-used, open-source tool that has solved this problem for over a decade.
Hand-rolling subnet wiring around a single-VM tool *to avoid a dependency* would
be reinventing a solved problem — the wrong kind of simplicity.

This is a deliberate divergence from Vergil's Lima choice, and the reasoning is
clean:

- **Lima still does the macOS→Linux step**, consistent with Vergil — but here it
  builds **one large, persistent dev+lab VM** (§7.4) rather than Vergil's small,
  ephemeral, single-purpose agent sandbox.
- **Vagrant runs nested *inside* that Linux VM** (§7.2), where its multi-machine
  DSL and libvirt networking are strongest. The MQ lab is many peer nodes across
  simulated sites with real, separately-addressable networks — Vagrant's home
  turf, not Lima's.
- So Lima and Vagrant are **layered, not siblings**: Lima provides the Linux
  host; Vagrant builds the lab within it (see §7.4).

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
`multipathd`, `ModemManager`, et al.); we reuse that approach on the **lab guest
nodes**. (The dev/host VM itself stays full-featured — it needs the toolchain;
see §7.4.) Leaner guests matter doubly under emulation.

### 7.3 Configuration via Ansible

Vagrant only stands up and networks the bare VMs. The **real MQ HA/DR work — the
actual product (§8) — splits across two planes (§8.1)**: the **bring-up plane**
(OS prep, MQ install, HA/DR, QM create + REST enablement) is **Ansible** over SSH
via Vagrant's Ansible provisioner; the **content plane** (queue-manager objects,
health, ops) is **`pymqrest`** against the now-running REST API. Both are kept
**independent of the harness** as a design discipline — no Vagrant/Lima/provider
assumptions baked in — so the *lab* is reusable and the *patterns* are portable.
That is not a promise the scripts deploy as-is at the client (§0/§8.1); the
harness is disposable, but the proven design and the reusable lab are not.

The containerized fixtures (`svc-sim` QM and `app-client`, §5/§9) are *not*
Vagrant VMs — they run on the containerd/nerdctl runtime the §7.4 dev+lab VM
already carries (the `vergil-vm` precedent), attached to the same libvirt
networks as the cluster VMs.

### 7.4 Development model — one large, persistent VM for dev and lab

We do **not** run this repo bare on the Mac. Instead we build **one large,
persistent Lima VM** that is *both* the Claude development sandbox *and* the
`vagrant-libvirt` virtualization host — and we do **all development inside it**.
This is the natural consequence of §7.2 already putting the lab inside a Linux
VM: rather than edit code on macOS and reach into a separate VM, the agent, the
editor, Vagrant, libvirt, and the lab guests all live in **the same Linux box**.

**How it differs from a standard Vergil agent VM:**

- **Big, not small.** Vergil's agent VMs are ~4 GB, deliberately constrained to
  contain Claude Code for an ordinary repo. This one is ~32–48 GB (§7.5) because
  it hosts the whole nested lab.
- **Persistent, not ephemeral.** Vergil agent VMs are throwaway and rebuilt per
  session; this one is long-lived — built once and kept. A rebuild costs us
  nothing important because the **source of truth is host-mounted** (below) and
  the lab is reproducible from its Vagrant/Ansible definitions.
- **Fuller, not service-minimized.** The §7.2 minimization pass targets the
  **lab guest nodes**, never this VM. The dev/host VM deliberately carries the
  full toolchain — Vagrant, libvirt/KVM, `virsh`, qemu, Ansible, dev tooling —
  because the agent needs freedom to drive the lab. (No tension: *lock down the
  guests, equip the host.*)

**Identity & sandboxing (Vergil-aligned).** Claude runs under the **same Vergil
identity / scoped GitHub App** as elsewhere, so the **git/GitHub blast radius
stays scoped** exactly as Vergil intends. What broadens is only *local execution*
inside this VM — the agent can run whatever the lab needs (`vagrant`, `virsh`,
provisioning, fault injection). That freedom is **contained within the VM**; the
externally-visible identity and push scope are unchanged.

**Storage — just the filesystem we have.** No special block device. The **repo
and all code/scripts live on a host-filesystem mount** (the same pattern as the
dev-projects mount), so work is persisted on the Mac and survives VM rebuilds.
*(Resolved in Phase A: the libvirt guest disk images sit on the VM's own
**ephemeral boot disk** — libvirt's default `/var/lib/libvirt/images` pool — not
on a host mount and not on the persistent data disk. That keeps DRBD block
replication off a 9p/virtiofs layer, and keeps wipe-on-rebuild overlays off the
never-wiped persistent disk. The #376 redirect did the latter — overlays onto
the persistent `/vergil` disk — and broke rebuilds with orphaned volumes;
reverted in #385/#386. The boot disk is sized to fit the image pool instead —
cloud: `boot_disk = "100GiB"`, #388.)*

**The payoff — collapse the macOS/Linux boundary.** Once bootstrapped, **we are
developing on Linux, with Linux tools, for a Linux target.** macOS shrinks to a
thin bootstrap: a small set of **macOS-only scripts that build and configure
this Lima VM** (create it, size it, enable nested-virt pass-through, wire the
projects mount). *Everything else* — the harness, the playbooks, the MQ tooling,
the tests — is written and run **assuming Linux**, with no macOS special-casing.
That erases a whole class of host-portability friction and makes the codebase
match its real deployment substrate from day one.

We still reuse Vergil's conventions where they transfer — Ubuntu LTS base,
provisioning patterns, and the `make docs` documentation-site layout — once this
repo is Vergil-adopted. The "sandbox exception" is therefore narrow: not "runs
on bare macOS," but "uses one bespoke, large, **persistent** VM instead of
Vergil's small ephemeral agent VMs."

### 7.5 Sizing budget

The full 3+3 topology is ~6 cluster-node **VMs** at ~1 GB each (plus, for the
Pacemaker arm, a witness + iSCSI-target VM); the `svc-sim` QM and `app-client`
are **containers**, not VMs, so they cost far less than a VM each. Under the
leading nested-libvirt model (§7.2) the VMs live *inside* one Linux VM that also
runs the fixture containers, so size that outer VM generously — ~**32–48 GB** of
the M5 Max's 128 GB leaves comfortable headroom. Native arm64 guests run
KVM-accelerated; x86-64 (required for RDQM) is TCG-emulated, accepted as slower
since RDQM validation is functional, not performance.

## 8. The Tooling (prototype that proves the design)

This section describes the **prototype tooling** we build to prove the design
(§0). It is generically written and fully functional in the lab, but it is **not
the client's production code** — we expect to re-implement against their internal
infrastructure (§0, §11). What this tooling delivers is **proof**: that the
HA/DR setup, configuration, failover, and DR procedures work, end to end, with
real evidence. The automation, configuration approach, and operational standards
below are the reusable *patterns*; whether any given script ships as-is is a
bonus, not the goal.

### 8.1 What the tooling is — and is not

- **It is** the install → configure → operate → recover lifecycle, automated and
  written down: idempotent, re-runnable, version-controlled, and **measurable
  against §3 and §4**. Every capability has a corresponding fault test that
  proves it.
- **It is harness-independent — as a design discipline, not a deployment
  promise.** The host-level automation runs over SSH via **Ansible** (§7.3); the
  queue-manager-level automation runs over the MQ admin REST API via
  **`pymqrest`** (below). We keep Vagrant/Lima assumptions *out* of the
  automation so the lab harness is never baked in — but the point of that
  discipline is that the *lab* is reusable and the *patterns* are portable, **not**
  a claim that these scripts deploy as-is at the client (§0). They are a
  prototype; the client environment will likely demand re-implementation.
- **It has two configuration planes, divided at one clean boundary — "is the
  queue manager and its REST API up yet?"**
  - **Bring-up plane (everything up to and including a running QM + REST API):**
    **Ansible plus system bootstrap scripts / config snippets.** OS prep, MQ
    install, HA cluster formation, DR pairing, queue-manager creation, and
    enabling the embedded web server. The REST API does not exist yet, so this
    plane *cannot* use it.
  - **Content plane (everything after the QM + REST API are online):**
    **`pymqrest`** — our own typed Python wrapper over the IBM MQ 9.4
    administrative REST API. All queue-manager *content* — queues, channels,
    listeners, auth records, topics, plus health, monitoring, and operational
    queries — is managed through it.
  - This boundary is not arbitrary: it falls exactly at the moment the REST API
    becomes available, which is the first thing `pymqrest` requires.
- **REST API enablement is a hard requirement on every queue manager** (the
  content plane needs it). *Securing* that endpoint is deliberately relaxed in
  the lab — see the security scope note in §1. We enable the embedded web server
  with whatever minimal auth is convenient (basic/LTPA, self-signed TLS, as in
  the dev-environment precedent); hardening it is not part of what we test.
- **Content-plane credentials are runtime-injected and never committed.** The
  `pymqrest` auth material (basic/LTPA/cert) reaching `mqweb` is supplied at run
  time (environment or a secret store) and excluded by `.gitignore` — no
  credentials, keystores, or LTPA tokens land in the repo. This is basic
  **hygiene for a shared hand-off artifact**, independent of the security-hardening
  scope that §1 puts out of bounds: relaxed lab auth is fine, committed secrets
  are not.
- **The QM-facing tooling is built on `pymqrest`, and exercising it is an
  explicit secondary goal (§1).** `pymqrest` already provides idempotent
  `ensure_*` methods (`CREATED`/`UPDATED`/`UNCHANGED`) — declarative,
  drift-correcting config-as-code at the library level — and a set of example
  tools (provisioning, health check, channel status, DLQ inspection, queue-depth
  monitoring) that seed §8.6/§8.7 directly. **Note the scope line:** `pymqrest`
  is the *administrative* REST API — it configures and observes queue managers;
  it does **not** put or get application messages (the trade-message path uses a
  native MQI client — see §9).
- **It is caged to the scale boundary (§1).** A handful of queue managers, a
  handful of sites, a handful of apps. We do **not** build a general-purpose MQ
  platform, an operator, or a self-service portal. "Keep it simple" wins ties.
- **It is two-armed at the interface, arm-specific underneath.** RDQM-on-RHEL
  (§10-C) and Ubuntu Pacemaker/SAN (§10-D) expose the **same operator verbs**
  (bring up HA, show status, fail over, set up DR, cut over, fail back) over
  different mechanics, so the runbooks and the eventual recommendation compare
  like with like.

### 8.2 Layered structure

The content is organized as composable layers, each independently runnable and
testable. Higher layers assume the lower ones converged; none of them assume the
harness. The **plane** column shows the §8.1 boundary: layers up to and
including QM + REST bring-up are the **Ansible/bootstrap** plane; everything
above the line is the **`pymqrest`** plane.

| Layer | Responsibility | Plane | Arm-specific? |
|------|----------------|-------|---------------|
| L0 | Host/OS prep — packages, kernel module prerequisites, users, firewall, time sync | Ansible/bootstrap | yes (RHEL vs Ubuntu) |
| L1 | MQ install (pinned baseline) + QM create + **enable & secure REST API** | Ansible/bootstrap | mostly shared |
| L2 | Intra-site **HA** bring-up | Ansible/bootstrap | yes |
| L3 | Cross-site **DR** setup + cutover/failback (node/OS mechanics) | Ansible/bootstrap | yes |
| — | *— handoff: QM + REST API online —* | | |
| L4 | QM **content** — queues, channels, listeners, auth, topics (declarative) | `pymqrest` | shared |
| L5 | Operational standards, health checks, monitoring | `pymqrest` | shared |
| L6 | Recovery & diagnostics capture | mixed (`runmqras` host-level + `pymqrest` queries) | shared |

### 8.3 Queue-manager install, REST enablement & content

**Bring-up plane (Ansible/bootstrap):**

- **Idempotent MQ install**, pinned to the **9.4 LTS** baseline (§C); re-running
  converges rather than duplicates.
- **Queue-manager create**, then **enable and secure the administrative REST
  API** (embedded web server / `mqweb`): the web server, a minimal auth registry
  (basic/LTPA, self-signed TLS — convenience, not a hardened posture; security is
  out of scope per §1), and the role bindings. This is the last bring-up step; it
  is what makes the content plane possible.

**REST-API-over-HA architecture (decided — stateless `mqweb` per node + VIP
routing).** The admin REST API does **not** need to fail over, because `mqweb`
is stateless. We run an `mqweb` instance on **every** cluster node, each
configured to administer the **local** queue manager. The REST endpoint is
published to clients as the **floating VIP** (which already follows the QM from
node to node). Because clients only ever use the VIP, they always reach the node
where the QM is currently live — its local `mqweb` connects; the `mqweb`
instances on the non-active nodes simply have no local QM to talk to until the
QM lands on them. So `mqweb` is **not** a cluster-managed resource that migrates;
it is an ordinary per-node service that is always running everywhere. *(This is
the architecture the author has run in production for the analogous case; the
exact RDQM mechanics are confirmed in the Phase-B/C lab, per trust-but-verify.)*

**Plane classification (epic #39).** The REST endpoint is a **data-plane
infrastructure** surface — the admin/content control surface co-located with the
QM, reached at the QM's data-plane VIP (pcmk/RDQM, on **each** site: `vip` /
`vip_b`) or, for Native HA (no VIP), the **active** instance's data-plane node IP,
runtime-resolved. It is **not** a management/observability-plane service: `net-mgmt`
is the Watcher, and mqweb is part of the infrastructure we *instrument*, not the
instrumentation observing it. mqweb binds `httpHost=*` (so it also answers on mgmt),
but its **canonical published address is the data plane**; refusing it on mgmt at
the network layer is deferred to the firewall/plane-enforcement follow-on. `svc-sim`'s
mqweb is the **counterparty** surface on `net-ext`, administered lab-only. See the
plane taxonomy in `docs/reference/dns-fqdn-inventory.md`.

A direct corollary, and part of the deliverable: the tooling must **install and
package `mqweb` (and MQ, and the HA resource agents) as boot services** so that
on reboot — and on failover — everything comes up correctly and in the right
order without manual intervention. The `mqweb` setup/configuration is itself a
build target, not an afterthought.

**Content plane (`pymqrest`):**

- **Declarative, idempotent object config via `pymqrest.ensure_*`.** Queue
  manager objects — queues, channels, listeners, auth records, topics — live as
  **declarative definition data under version control** and are applied through
  `pymqrest`'s `ensure_*` methods, which **DEFINE** when absent, **ALTER** only
  the differing attributes, and **no-op** when already correct
  (`CREATED`/`UPDATED`/`UNCHANGED`).
- **Drift detection / convergence comes for free** from `ensure_*`: re-applying
  the definitions reports and corrects divergence, so "what the QM should be" is
  always the file in git. We do not reinvent this — the library already does it,
  and exercising it that way is an explicit goal (§1).

### 8.4 HA setup automation (per arm)

- **RDQM/RHEL:** form the **3-node synchronous HA group** — DRBD config
  generated and validated, Pacemaker resources, floating IP — from a single
  declarative group definition.
- **Ubuntu Pacemaker/SAN:** Corosync/Pacemaker, STONITH fencing, qdevice quorum,
  and the iSCSI/SAN resource agents that the §2.3 arm depends on.
- **Common verbs across both arms:** `form-group`, `add-node` / `evacuate-node`,
  `status`, `failover`. The verb is stable; the implementation differs.

### 8.5 DR setup, cutover & failback

- **RDQM/RHEL:** create the DR-replicated pair (`crtmqm -rr`), drive
  `rdqmdr` cutover and failback.
- **Ubuntu:** DRBD async replication + Booth ticket arbitration for the
  cross-site role.
- **The §4.7 role rotation as a scripted, rehearsable operation.** The
  symmetric-peer-site rotation is not an ad-hoc afternoon — it is a paved-path
  procedure: **quiesce the live site → confirm the replication stream has fully
  caught up (drive the async window to zero) → cut over → run the business from
  the peer → rotate back when ready.** The "confirm caught up before you cut"
  step is what turns an inherently async, lossy failover into a **planned,
  zero-loss** transition; it is the difference between disaster cutover (step 7
  of §3.1) and controlled rotation (step 9). The tooling makes both safe and
  repeatable, and refuses to proceed with the clean path if replication has not
  converged.

### 8.6 Operational standards

The standards are part of the product, not documentation bolted on afterward.

- **Runbooks** — the paved-path procedures for every operator verb above, plus
  the disaster and rotation flows, written so the *next* MQ admin (there is only
  one today) can execute them under pressure.
- **Health checks** — queue-manager liveness, channel state, replication lag,
  quorum/cluster health — as scripts that exit non-zero and are alert-friendly.
  The QM-facing ones build on `pymqrest`'s example tools (`health_check`,
  `channel_status`, `queue_depth_monitor`, `dlq_inspector`); the
  cluster/replication ones are host-level checks on the bring-up plane.
- **Backup** — of the queue-manager definitions and the config-as-code, with a
  documented restore path that is itself a tested recovery procedure.
- **Change procedure** — how a config change flows from edit → review → apply →
  verify, consistent across both arms.

### 8.7 Recovery & diagnostics

- A wrapper around **`runmqras`** (and the FFST/FDC artifacts) that captures
  "everything IBM will ask for" in one step — proven in **§3.1 step 8**. This is
  **host-level** (bring-up plane): `runmqras` runs on the node, not over REST.
- `pymqrest` complements it with the **live queue-manager state** side of a
  diagnostic snapshot (object definitions, channel/listener status, queue
  depths) — the picture you want captured alongside the `runmqras` bundle.
- Together they serve the **vendor-supportability criterion (§3):** when a
  SEV-1 hits a tier-one app, the value is being able to hand IBM a complete
  diagnostic bundle immediately, regardless of which arm is deployed.

### 8.8 Packaging — optional, only if it earns its keep (Phase F)

- **Demoted from "the endgame."** Per §0, this tooling is a prototype, not the
  client's production code, so packaging it as a distributable `.deb`/`.rpm` is
  **optional** — worth doing only if the lab becomes a long-lived enough internal
  test bed (shared with other engineers) that easy install/upgrade pays off. It
  is explicitly **not** a claim that the client installs this package.
- If we do it, the artifact lays down the playbooks, the `pymqrest`-based CLI,
  and the standards on a control host — the "automate setting up the tooling"
  convenience. Until (or unless) then, the tooling is just a **versioned repo of
  Ansible content + Python tooling + standards**, already fully usable in the lab.

### 8.9 Design principles (the through-line)

Idempotent · declarative config-as-code · harness-independent · caged to scope ·
two-arm parity at the operator interface · **two planes divided at
REST-API-online (Ansible/bootstrap → `pymqrest`)** · every capability paired
with a §3/§4 fault test that proves it.

## 9. SVC Simulation & Validation

The `svc-sim` fixture mimics SVC's server side so we can validate the message
path and DR behavior end to end. The public record gives us enough to make the
simulation **realistic in shape** (the exact per-service formats and endpoints
are delivered per-client at onboarding and are not public — so we simulate the
*pattern*, not a real SVC interface). *(Grounded in public SVC material
researched 2026-06-03; see §9.3 references.)*

**It runs as a container, lifted from prior art.** `svc-sim` is a real IBM MQ
queue manager in a container (`icr.io/ibm-messaging/mq`), seeded with reciprocal
channel/queue definitions, taken almost directly from the
`mq-rest-admin-dev-environment` repo (docker-compose + MQSC seed + REST-enabled
web server). It needs no VM — it runs on the containerd/nerdctl runtime inside
the §7.4 dev+lab VM and attaches to the SVC-facing libvirt network. **Two REST
APIs, kept distinct:** the sim's *administrative* REST API (configured with
`pymqrest`, like every QM here) is separate from the **messaging** path the
`app-client` uses to actually put/get trade messages, which is a **native MQI
client** (`pymqi`) over a SVRCONN/sender/receiver channel — not `pymqrest`.

### 9.1 Connectivity model to mirror (from the public SVC FFH MQ guide)

- **Distributed queuing**, not client/server: the app's queue manager and the
  SVC queue manager exchange messages via **sender/receiver channels** with
  **local queues, remote-queue definitions, and transmission queues** on each
  side. The sim therefore runs its own QM with reciprocal channel definitions
  back to the app QM.
- **Application-level fixed-format header inside the message body** (distinct
  from the MQMD): blank-padded, left-justified fields — e.g. Password, Sender
  (the app's SVC account ID), Receiver (a fixed service mnemonic), and
  business date — followed by service-specific **ACK / reject codes** (e.g.
  header-validation failure, stale business date). The responder app validates
  and ACKs this header so we exercise realistic reject/replay handling.
- **Per-client password auth carried in the header**; **a single connection
  ID** per client (multiple IDs cause duplicate delivery on the same channel);
  legacy TCP/CTCI and MQ must **not** be active simultaneously for one account.
- **SVC-side resiliency feature worth modeling:** SVC can deliver a client's
  inbound messages into **multiple queues** to support the client's
  resiliency/DR — a useful pattern to reflect in the DR tests (§3.1 step 7).

### 9.2 Transport & security context (real-world, for fidelity notes)

In production, MQ to SVC runs over a **dedicated SMART circuit** (new-circuit
lead times ~12–14 weeks — a *schedule* risk, not a lab one), and SVC enforces
**channel security/encryption standards (TLS)** per its connectivity security standards
(mandatory since 2024-12-31; members register their MQ channel name;
non-compliant connections are disconnected). The lab need not replicate SMART,
but the tooling and standards **must** produce a TLS-secured channel
configuration so what we build is onboarding-ready.

### 9.3 Validation

Fault-injection failover tests and DR cutover tests (per §3.1) run trades
through `app-client → app QM → svc-sim → responder → back`, proving
message integrity and measuring RTO/RPO across both HA failover and full-site
DR cutover.

**References (public; verify per-service at onboarding):**

- SVC FFH MQ Implementation Guide (SVC, "Public/White") —
- its connectivity security standards (connectivity security standards, incl. MQ) —
- SVC Settlement Service Guide (MQ used on the DTC settlement side) —

**Caveat:** message header layouts and ACK codes are **per-service** (SVC FFH
/ MBSD vs DTC settlement vs NSCC/UTC). QM names, channel names, ports, and IP
endpoints are **not public** and arrive per-client during onboarding — the sim
must not hardcode any assumed real values.

## 10. Phasing

Sub-projects, each its own spec → plan → build. **DR is baked into each arm
from the start** — we do not build HA and then bolt DR on; an arm is only
"done" when it has a demonstrated cross-site DR story (per §1, design for the
full 3+3 architecture up front).

**Both arms are real builds, and RDQM goes first — by intent, not just order.**
The goal is a genuine apples-to-apples POC of *both* HA/DR approaches (C and D).
But there are only ~2 weeks to the 2026-06-15 contract start, so RDQM is the
**priority arm**: if only one thing is running by then, it must be RDQM, because
it is the approach we most expect the client will actually adopt. Concretely,
**get RDQM to a working, demonstrable instance first, then start the Ubuntu
arm** — one finished instance plus Ubuntu in progress beats two half-built arms.
Exact sequencing past that point (how far to push RDQM hardening before pivoting,
whether to interleave) will **adapt as we go**.

**The complexity asymmetry between the arms is itself a finding, not an
accident.** The two arms are deliberately *not* apples-to-apples in
**complexity** (the Ubuntu arm carries an external cluster stack, shared storage,
fencing, and a replication tier RDQM simply doesn't have) — yet they are meant to
be apples-to-apples in **delivered functionality**. That gap is a headline
comparison point: *"look at everything you have to stand up and keep running for
the Ubuntu path — do you want that operational surface for a small, caged
service?"* The honest counterweight (carried in §2.7 and weighed in E) is the
**unknown of the client's existing RHEL-licensing posture and how well their
Ubuntu-shaped internal stack would support/integrate either OS** — if RHEL is
costly or poorly integrated there, it can neutralize part of the
complexity/supportability advantage.

- **A.** Virtualization harness (multi-site, multi-network, {os,arch}-parameterized)
  — **Vagrant**; start here. First task is the **provider spike** (§7.2): confirm
  the leading **nested `vagrant-libvirt`** model — Lima nested-virt pass-through
  on this M5/macOS, severable heartbeat/replication nets, and acceptable
  TCG-emulated x86 for the RDQM arm — with the cloud-x86 split as the fallback.
- **B.** Single standalone QM (Ubuntu arm64) + SVC sim + client — prove the
  end-to-end message path before any clustering. The QM is brought up by
  Ansible/bootstrap **with its REST API enabled**, then configured with
  `pymqrest` (first real exercise of the content plane); `svc-sim` and
  `app-client` run as containers (§5/§9), the client using a **native MQI**
  connection for the trade path.
- **C.** **RDQM arm, full HA+DR on RHEL x86-64 — the priority arm (drive to a
  working instance first).** The comparison baseline: 3-node synchronous HA group
  at the primary site + async DR to a 3-node group at the recovery site (3+3),
  with `rdqmdr` cutover/failback and the §3.1 fault suite incl. full-site-loss.
  This is the one that must be demonstrable before the Ubuntu arm starts (see the
  priority note above).
- **D.** **Ubuntu Pacemaker/SAN arm, full HA+DR** — external Pacemaker/Corosync
  + SAN/iSCSI + STONITH + qdevice for intra-site HA, plus cross-site DR (DRBD
  async + Booth), run through the *identical* §3.1 suite for an apples-to-apples
  comparison.
- **E.** Comparison analysis & recommendation for the client — scores both arms on
  §3 + §4, **weighting the vendor-supportability gap and the operational-complexity
  asymmetry heavily** (the Ubuntu arm's external cluster/shared-storage/fencing/
  replication surface vs. RDQM's shared-nothing turnkey box, at equal delivered
  functionality), and states the conditions under which each wins. The standing
  counterweight is the **client's RHEL-licensing posture and Ubuntu-stack
  integration cost** (§2.7). Decision deferred to SVC/the client requirements.
- **F.** Packaging & operational standards — `.deb`/`.rpm` wrapping the Ansible
  content **and the `pymqrest`-based Python tooling/CLI**, runbooks, health
  checks, and the **recovery & diagnostics tooling** (`runmqras`/FFST capture)
  proven in §3.1 step 8.
- **G.** *(forward-looking, post-requirements)* Dual-path / multi-QM
  active-active across two DCs — see Appendix A. Out of scope for Deliverable
  #1; gated on SVC/app requirements.

## 11. Risks & Open Questions

**Platform / lab:**

- RHEL developer licensing and whether **RDQM** is usable under it.
- x86-64 emulation on the M5 Max (RDQM forces RHEL-x86-64). **Decided (§6):**
  stay local under emulation by intent (offline-capable), scope RDQM evidence to
  functional correctness, don't tune timers to mask jitter; cloud-x86 is
  break-glass if emulation jitter becomes indistinguishable from real faults.
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

**Tooling / dependencies:**

- **`pymqrest` is now a core dependency of the deliverable** (the content plane,
  §8.1). It is the author's own code, actively maintained, and the
  IBM-MQ-9.4-targeted REST surface matches our baseline (§C) — but a version-10
  REST-API change (§C) could need a `pymqrest` update. Tracked as a normal
  dependency, not a blocker.
- **Licensing — moving to MIT.** `pymqrest` and siblings are currently
  GPL-3.0-or-later; the author intends to **relicense his MQ-REST-admin projects
  to MIT** for corporate compatibility (he owns and maintains them, and is
  indifferent to downstream reuse). MIT removes any copyleft concern about
  shipping `pymqrest` inside the client deliverable. Low risk; if any consumer
  ever objects he'll adjust. *(Action: confirm the relicense lands before the
  Phase-F packaging step makes `pymqrest` a distributed dependency.)*

**DR / message integrity:**

- Cross-site synchronous replication is impractical → residual DR message-loss
  window; the app/SVC reconciliation path (§4.3) must close it.
- Target envelope is now grounded (§4.6): **~2-hour RTO, out-of-region** —
  the client's exact contractual numbers remain TBD.

**SVC-specific (grounded in §9, but with real gaps):**

- **Which SVC service** the client clears/settles through (SVC FFH, DTC
  settlement, NSCC/UTC, …) determines message header formats and ACK codes —
  **unknown** until the client tells us. The sim models the *pattern*, not a
  specific service's wire format.
- **Channel security:** SVC mandates TLS on the MQ channel (those security standards) — the
  tooling must emit an onboarding-ready, TLS-secured channel config.
- **Schedule risk:** a new dedicated SMART circuit has a ~12–14 week lead time;
  irrelevant to the lab but material to the client's production rollout plan.
- **Open gap — no public mandate for dual/diverse member MQ circuits.** That
  specific requirement (if it exists) lives in SVC's **gated, internally
  classified DR Guide** and per-client onboarding packets, not public material.
  We treat member-side dual-site connectivity as best practice and a **question
  to confirm with the client/SVC**, *not* a citable public requirement.
- QM names, channel names, ports, and endpoints are delivered per-client at
  onboarding — never hardcode assumed values.

---

## Appendix A. Likely Final Recommendation: Dual-Path, Multi-QM Active/Active (forward-looking)

> **Scope note.** The body of this design is about building **one** solid
> HA/DR queue manager — that is Deliverable #1 and the foundation. This
> appendix records the *expected shape of the eventual production
> recommendation*, which is almost certainly built from **multiple** such
> queue managers. It is deliberately not part of Deliverable #1; it gets
> fleshed out once we understand SVC's and the application's requirements.

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

- **Does SVC permit active/active?** Two simultaneously-live endpoints may or
  may not be allowed by SVC's connection model and sequencing assumptions.
  Unknown until we study the SVC application and its requirements.
- **Can both paths run live at once**, or is it active/standby at the
  path level? Depends on SVC assumptions.
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
> support life and SVC will not retire a connection just because IBM end-of-lifes
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

- **Build on 9.4, not 10.0 — and push back hard on any suggestion otherwise.**
  You cannot found a project *from cold* on a release that just went GA. The
  industry has no mileage on it, and IBM MQ is a **large, complex product whose
  major-version upgrades have historically been painful** — this is not a small,
  fast-moving open-source package where "latest" is usually the safe default. For
  a tier-one institution running this infrastructure because it is **mandatory,
  not because it wants the bleeding edge**, the only rational posture is
  conservative: 10.0 on day 2 is **all risk and no reward**. There is nothing in
  10.0 that buys an advantage over what 9.4 already delivers for this use case.
- **9.4 is the deliberate, defensible baseline**, not a stopgap: it is deeply
  field-proven and supported to **~2029** (C.3), giving years of unhurried runway.
  The realistic 5-year plan is to *plan, implement, and migrate* off 9.4 well
  inside that window — not to start on 10.0.
- **10.0 is documented purely as a forward-looking item** (we show we're looking
  ahead), explored only **if time allows**. It is almost certainly academic for
  this engagement: a conservative messaging partner like SVC is unlikely to jump
  to 10.0 early, and so is the client. Part of arriving prepared is bringing the
  written 9→10 gap analysis and upgrade plan (C.4) — as future planning, not a
  near-term task.

### C.3 EOS boundary conditions (the upgrade window)

- **MQ 9.3 LTS** — End of Service **2027-09-30**.
- **MQ 9.4 LTS** — ~5-year support from 2024-06 GA → EOS **~2029-06**; optional
  paid Extended Support up to ~4 more years (~2033).
- **MQ 10.0 LTS** — fresh 5-year clock from 2026-06 GA.

So a go-live on 9.4 gives a comfortable runway to **~2029** (longer with paid
extended support). The 9→10 upgrade must land **inside** that window — early
enough to be unhurried, before 9.4 support lapses.

### C.4 The gap-analysis task (with a specific question to answer)

A documented **9.4 → 10.0 gap analysis** is a **forward-looking** deliverable
(future upgrade planning, pursued if time allows — not a near-term build task per
C.2). The headline question, directly relevant to the §2 thesis:

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
  ahead of any SVC- or IBM-driven requirement to move.
- Re-verify all dates and the RDQM-platform question against IBM's lifecycle
  and System Requirements pages — vendor claims get the same trust-but-verify
  treatment as everything else.

**References (re-verify; some IBM pages were gated at research time):**

- Introducing IBM MQ v10.0 — <https://www.ibm.com/new/announcements/introducing-ibm-mq-v10-0>
- IBM MQ lifecycle / EOS dates — <https://www.ibm.com/support/pages/lifecycle/details/?q45=mQ>
- RDQM kernel modules (platform coupling) — <https://www.ibm.com/support/pages/ibm-mq-replicated-data-queue-manager-kernel-modules>
