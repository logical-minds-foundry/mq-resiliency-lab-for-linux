# IBM MQ Native HA on Kubernetes/OpenShift — Arm Design (`nativeha-ocp`)

> **Status:** design, first pass — brainstormed 2026-06-16, iterating.
> **Date:** 2026-06-16
> **Author:** Phillip Moore (with Claude)
> **Tracking issue:** #198
> **Relationship:** un-parks and reshapes the `nativeha-rhel` slot reserved in
> the RDQM-parity pivot
> ([`2026-06-15-rdqm-parity-pivot-design.md`](2026-06-15-rdqm-parity-pivot-design.md)
> §6) and the parked "arm 3" of the authoritative design
> ([`2026-06-03-mq-cluster-lab-design.md`](2026-06-03-mq-cluster-lab-design.md)
> §2.3). Where this doc and those conflict on the Native HA arm, this doc is the
> current thinking; the pivot's **build guardrail** (§6) still stands (see §2).

---

## 1. Why this exists — the strategic driver

The existing design treated Native HA as *a future parity arm we would reach
eventually*, gated behind proving the RDQM and Pacemaker arms first. **The
engagement's day-one reality reframes it as a potential project-collapsing
leapfrog**, and that reframing — not the technology — is what is new here.

The chain that creates the problem:

1. The firm standardizes on **RHEL/OpenShift** and is deliberately
   risk-averse — IBM-supportability first, deviation from the status quo second
   (consistent with the authoritative design's most heavily-weighted criterion,
   §2.7/§3, and confirmed at engagement start).
2. Outside Kubernetes, the in-house MQ service is expected to land in a
   **DMZ**.
3. The DMZ has **no support for VMs** → it forces **bare metal**.
4. Bare metal has **no support for the kickstart/automation estate** → it forces
   **manual, by-hand installation**.
5. Manual installation is **not reproducible** — for a critical, regulated,
   greenfield service, that is serious technical debt the moment it ships.

The escape hatch the firm itself surfaced:

> The security team will **exempt a workload from the DMZ if it runs in
> Kubernetes**, because they are satisfied they can control and limit its access
> there.

And Kubernetes has exactly one first-class HA mechanism for a queue manager:
**IBM MQ Native HA** (container-native, log-replication quorum). So the escape
hatch and the HA mechanism are the same decision.

**The play:** if the lab can demonstrate *reliable HADR message flow* through a
Native-HA-on-Kubernetes deployment — slotted into the same app↔QM↔DTCC
architecture already built — that evidence becomes the basis to propose
Kubernetes as a **secondary, or even a replacement, architecture**. Success
collapses the DMZ + bare-metal + manual-install complexity into a single, far
simpler ask: **punch controlled firewall holes to a Kubernetes ingress** so
remote queue managers can reach the in-house service.

### 1.1 Greenfield — a tailwind, not only a challenge

The firm has **zero internal MQ infrastructure**. No one there has MQ
experience; the engagement exists precisely to build this in-house, off a
third-party gateway provider, on a tight timeline. Two consequences:

- **It removes a whole class of objection to the leapfrog.** There is no
  incumbent MQ deployment the Kubernetes path must stay compatible with — "it
  won't match how we already run MQ" cannot be raised, because there is nothing
  to disrupt. The only real compatibility constraints are (a) the firm's
  Kubernetes/platform/security standards and (b) the external DTCC interface,
  both of which the Native-HA-on-K8s path can satisfy directly.
- **It raises the stakes on the lab being right.** For the next six months the
  lab is effectively the firm's *sole* source of MQ HADR truth. The design owes
  the same evidence discipline (§3, §4 of the authoritative design) it always
  did — only now there is no internal expertise to catch a wrong call.

The mission framing for the engagement: navigate the next six months to deliver
a true MVP **without cutting corners** — optimizing *scope and sequencing*, not
*rigor* — and without making this strategic play harder to land later.

## 2. Positioning vs. the RDQM-parity roadmap

The pivot (#187 §6) set a guardrail: **no build work on the Native HA slot until
the framework is proven on the `rdqm-rhel` + `pcmk-ubuntu` pair.** That guardrail
still holds. This arm is positioned as a **parallel exploratory spike that
generates evidence for the DMZ-collapse pitch** — *not* a disruption of the
RDQM-parity mainline, which remains aligned with the platform decision the firm
confirmed.

- The RDQM/Pacemaker work continues as the committed mainline.
- This arm produces the *strategic evidence*; if the pitch lands, it gets
  promoted from exploratory spike to a first-class, co-maintained arm.
- The arm registry was deliberately written **open** (mechanism × OS × substrate
  axes; the substrate axis must not assume `vm`), so this folds in without a
  retrofit — that openness is exactly the cost the deferral was meant to buy
  (#187 §6 guardrail).

*(Default to confirm: this parallel-spike positioning is a recommendation, not a
settled decision — it is the author's call as the engagement clarifies.)*

## 3. The strategic case (the pitch surface)

This is the half of the doc intended to be *argued*, not just built. The lab
build (§4) is its proof.

### 3.1 Posture — staged escalation, opening as an early-warning flag

**Decided 2026-06-16:** open as an **early-warning flag plus evidence-building**,
not a decision demand. Rationale: credibility must precede deviation from the
IBM/status-quo path, and the author is new to the firm — the play is high-reward,
but a premature hard ask risks the standing needed to land it later. The argument
below is therefore framed as *what the investigation aims to demonstrate*, not as
an immediate demand.

The escalation ladder (stages, not stances):

1. **Flag + investigate (now).** Raise the reproducibility/tech-debt risk (§3.2)
   as a concern being actively de-risked; position K8s/Native HA as the mitigation
   under investigation in the lab. No decision requested.
2. **Parallel secondary-architecture proposal.** Put it forward as a co-equal
   option to evaluate against the DMZ plan, lab demo as evidence.
3. **Replacement proposal.** Pitch K8s as collapsing the DMZ workstream — only
   when evidence is overwhelming and standing is established.

**Triggers to escalate 1 → 2:** the lab demonstrates reliable end-to-end HADR
message flow (§4); the firm confirms the OpenShift/security DMZ-exemption **in
writing**; and a CRR-capable version stream (CD vs LTS, §7) is viable on the
timeline.

### 3.2 The risk being flagged (the opener)

A foreseeable, avoidable debt, raised early so it can be weighed *before* it is
incurred: the DMZ mandate forces bare metal (no DMZ VM support), which forces
manual installation (no kickstart/automation estate), which yields a
**non-reproducible** build of a critical, regulated, greenfield service. Framed
constructively — not "the plan is wrong," but "here is a debt we can see coming,
and here is the mitigation I am already proving out."

### 3.3 What the evidence will show (the argument)

- **Reproducibility restored.** The Native HA deployment is declarative end to
  end — the **IBM MQ Operator** reconciling a **`QueueManager` custom resource**,
  GitOps-able, re-appliable, drift-correcting. The categorical opposite of a
  by-hand bare-metal install.
- **DMZ eliminated.** The cluster's **controlled, policy-enforced ingress**
  replaces the DMZ gateway — limited and inspectable by mechanisms the security
  team already trusts (NetworkPolicies, ingress/Route config, mTLS), which is
  *why* they will exempt it.
- **Two controlled boundaries, named honestly.** *Two* cross-cluster paths, not
  one: the client/DTCC **ingress** (Routes, §4.5) and the **inter-region CRR
  replication link** (TLS, async, §4.4). Naming both up front is more honest than
  implying a single boundary.
- **Bare metal eliminated.** Workloads are pods on the existing OpenShift estate;
  no DMZ-specific bare-metal footprint to provision and hand-maintain.
- **Supportability preserved.** The **IBM-recommended** shape (Operator + Native
  HA + CRR), so the play does *not* trade away the IBM-supportability criterion
  the firm weights highest — it stays inside IBM's blessed envelope, unlike a
  bespoke self-managed cluster.
- **Greenfield tailwind (§1.1).** Nothing to stay compatible with internally.

### 3.4 Honest framing

This is a long shot on **timeline**, not on **soundness**: the risk is whether
the team can come up to speed and the firm can deploy inside the engagement
window — not whether the architecture is right. The CRR CD-vs-LTS gate (§7) is
the single biggest timeline variable. State both plainly; overselling the
timeline is how trust is lost.

## 4. The lab arm design

### 4.1 The arm

Register a new arm. The pivot reserved it as `nativeha-rhel`; the defining trait
is the **OpenShift platform**, not merely RHEL lineage, so it is named
**`nativeha-ocp`**:

| Field | Value |
|---|---|
| Mechanism | `native-ha` (container-native log-replication quorum) |
| OS / node platform | RHCOS (OKD) |
| Substrate | `container/k8s` (OKD) |
| Arch | **arm64 in lab** (KVM-accelerated) **modeling x86 in prod** (§4.8) |

The registry must not bake in a `vm` substrate assumption (already a pivot
acceptance check); this arm is the first exerciser of the `container/k8s`
substrate.

### 4.2 Substrate — multi-node OKD on arm64, per DC

Two **independent multi-node OKD clusters**, one per simulated data center,
running on the existing two-DC libvirt network topology (§5 of the authoritative
design). Minimum **3 worker nodes per cluster** so the Native HA pods spread
across real, individually-killable worker VMs.

**Why arm64 (the lever that makes this feasible on the laptop).** *(Data)* Unlike
RDQM — hard-locked to x86 by the DRBD kernel module, which is why the RDQM arm
pays the slow TCG-emulation tax — **Native HA has no x86 lock**: IBM ships arm64
MQ container images and OpenShift/OKD support arm64. So this arm runs
**arm64-native and KVM-accelerated**, sidestepping the TCG penalty that makes a
multi-node OpenShift cluster (×2) otherwise prohibitive on an M-series laptop.
*(Judgment)* This is the single decision that turns "probably infeasible locally"
into "probably feasible" — and it is gated by the §4.7 spike, not assumed.

**Why real VMs, not containers-as-nodes.** The lab's entire identity is
high-fidelity fault injection (§3.1, §7.1 of the authoritative design). On
Kubernetes the fault primitives reshape (§4.6), but that reshape stays *honest*
only if the "nodes" are real, independently-killable things. k3s/kind-in-a-VM
would collapse "kill a node" into a container kill and quietly drop the fidelity
the lab exists to provide. Real worker VMs preserve real node power-off and real
NIC-sever faults.

### 4.3 HA — Native HA via the MQ Operator

The **IBM MQ Operator** deploys a Native HA **`QueueManager`** custom resource: a
**3-pod log-replication quorum**, one active instance, automatic in-cluster
failover. Kubernetes handles scheduling and node-failure response; Native HA owns
the MQ-level quorum, log replication, and failover. This is the IBM-recommended
shape and the thing to model faithfully (Operator + CRD + RHCOS + `oc`).

### 4.4 DR — Native HA Cross-Region Replication (CRR)

*(Validated against IBM docs 2026-06-16; re-verify against the licensed version
per trust-but-verify. References in §4.4.1.)*

The IBM-supported DR mechanism is **Native HA Cross-Region Replication (CRR)**,
introduced in **MQ 9.4.2** (a Continuous Delivery release, Feb 2025).

- **Topology.** Two Native HA groups — a **Live** group and a **Recovery**
  group — each a 3-pod quorum, in **two separate Kubernetes/OpenShift clusters**:
  6 pods across two clusters, mapping exactly onto the existing 3+3 two-DC
  topology (§4.2).
- **Replication model.** **Local replication is synchronous** (RPO 0 within a
  group/site); **cross-region replication is asynchronous** (the active QM
  updates local replicas, then ships deltas to the remote region) → non-zero RPO,
  a message-loss window the app must reconcile (§4.3 of the authoritative
  design). This is the **same sync-HA / async-DR split as RDQM** — §4.2's
  analysis transfers wholesale.
- **Roles & operations.** Live/Recovery roles are swappable. **Planned
  switchover** swaps the roles via config; **unplanned failover** promotes
  Recovery to Live and sets `nativeHAGroups.remotes.enabled: false`. **Both are
  manual config operations**, not automatic cross-region failover — the same
  operational shape as RDQM's manual `rdqmdr`, mapping onto the `mqlab dr`
  `cutover`/`failback` verbs (§3.3 of the pivot contract).
- **Config surface.** The **`QueueManager` CRD** (`nativeHAGroups.remotes` —
  addresses + TLS), with advanced options via INI. Replication between sites is
  TLS-secured.
- **Composition.** A Native HA QM "appears to MQ clustering as a single node,"
  and CRR extends recovery across regions — clustering + Native HA + CRR layer
  cleanly.
- **Drills.** Planned switchover → §3.1 step 9 (reversible role rotation);
  unplanned failover → step 7 (full-site disaster) + the §4.3 RPO-window
  reconciliation.

**The inter-region replication link is a second controlled cross-cluster
boundary** (distinct from the §4.5 client/DTCC ingress): the CRR replication
endpoints connect cluster-A ↔ cluster-B over the simulated WAN, TLS-secured.

**Endpoint exposure — resolved (validated 2026-06-16).** IBM documents
**OpenShift Routes with TLS passthrough + SNI** as the mechanism for CRR data
traffic — the *same* mechanism as the client/DTCC listener ingress (§4.5), so one
ingress pattern covers both boundaries. The flow: deploy the Recovery group,
retrieve its Route address(es), and populate them as the `address` values in the
`nativeHAGroups.remotes` config of each group. (On vanilla Kubernetes/Helm, where
Routes don't exist, the equivalent is a **LoadBalancer** Service per endpoint —
the fallback for any non-OpenShift substrate.)

**Lab consequence (a real networking task).** Routes carry the replication only
if each cluster can **resolve and reach the other's Route hostnames over the
simulated WAN** — so the lab must wire cross-cluster DNS (the `*.apps.<cluster>`
wildcard domains) and router exposure across `net-wan`, with per-instance SNI
hostnames (expect ~3 Routes per group, SNI-distinguished — confirm exact count at
build time). This extends the existing `mqlab net` slice.

#### 4.4.1 References (verify against the licensed version)

- Native HA CRR overview — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=containers-native-ha-cross-region-replication>
- Configuring Native HA CRR using the MQ Operator — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=operator-configuring-native-ha-crr-using-mq>
- Adding a recovery group to an existing Native HA config (Operator) — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=chaqmumo-example-adding-recovery-group-existing-native-ha-configuration-using-mq-operator>
- CRR switchover & failover — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=operating-native-ha-crr-switchover-failover>
- Configuring CRR with the MQ Operator (Route addresses in `remotes`) — <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=chaqmumo-example-configuring-native-ha-crr-using-mq-operator>
- Red Hat / Cloud Pak reference — cross-region active/passive MQ — <https://production-gitops.dev/guides/cp4i/mq/high-availability/ha2-cr-ap/>
- MQ 9.4.3 announcement (CRR add-on licensing) — <https://www.ibm.com/new/announcements/enhancing-security-productivity-and-resilience-with-ibm-mq-9-4-3>

### 4.5 Ingress — the DMZ-replacement boundary

**OpenShift Routes (TLS SNI passthrough)** — IBM's documented MQ-on-OpenShift
ingress pattern — are the controlled cluster ingress. This is not an incidental
wiring choice: **the Route is the artifact that replaces the DMZ gateway**, and
the strategic case (§3) anchors here. External fixtures (`dtcc-sim`, `app-client`)
attach through the Route exactly as a remote counterparty QM would in production.

*(Supersedes the earlier brainstorm fork between a MetalLB LoadBalancer IP and an
SNI ingress: choosing OpenShift fidelity settled it on Routes.)*

### 4.6 Fault primitives reshape (as the pivot anticipated)

The §3.1 fault suite runs identically in *intent* across arms; the *primitives*
change shape on Kubernetes:

| §3.1 scenario | VM arms (RDQM/Pacemaker) | `nativeha-ocp` |
|---|---|---|
| Kill the QM process | `kill -9` the QM | kill the **active Native HA pod** |
| Hard-kill the active node | power off the VM | power off the **worker VM** hosting the active pod |
| Sever heartbeat/replication | down the private NIC | sever **pod-to-pod replication** network |
| Sever shared storage | detach LUN | (n/a — shared-nothing; verify PV-loss behaviour instead) |
| Planned failover + failback | resource move | drain/cordon node; controlled pod move |
| Rolling patch | node-at-a-time | rolling node/operator update |
| Full-site DR | kill primary site | kill primary **cluster**; cross-cluster cutover |
| Diagnostics capture | `runmqras`/FFST | `runmqras` in-pod + Operator/CRD status + `oc` state |
| Role rotation | `rdqmdr` / Booth | controlled cross-cluster recovery-group rotation |

### 4.7 Feasibility spike (Phase-A gate)

Before committing the full arm, a **Phase-A spike** confirms the load-bearing
assumption: that **multi-node OKD on arm64, two clusters, actually bootstraps and
runs within budget on nested libvirt** inside the dev+lab VM. This matches the
established spike-first, checkpointed execution pattern. If the spike fails, the
documented fallback is **Single-Node OpenShift per DC** — which keeps the
OpenShift surface (Operator, Routes, `oc`, RHCOS) but loses within-site
node-power-off HA (all three Native HA pods land on one node), so it demonstrates
pod-failure HA + cross-site DR rather than node-failure HA. Record the
compromise explicitly if reached.

### 4.8 App-flow slotting

The in-house HA queue manager becomes **`QMNATIVE`**, replacing `QMPCMK`/`QMRDQM`
as the in-house substrate in the existing distributed architecture
([`2026-06-13-distributed-mq-architecture-design.md`](2026-06-13-distributed-mq-architecture-design.md)):
`QMNATIVE ↔ QMDTCC` across the simulated WAN, `app-client` puts trades,
`dtcc-sim` replies. **Same app contract, new substrate** — so the arm drops into
the message flow already built, and the comparison stays like-for-like.

## 5. The HADR-on-Kubernetes question (can MQ DR piggyback on the platform?)

A natural and important question: can MQ simply inherit Kubernetes' own HADR and
have it "just work"? The honest answer shapes both the design and what we ask the
firm.

- *(Data)* Kubernetes natively provides **intra-cluster HA** — it reschedules
  pods onto healthy nodes and handles node failure. Native HA leans on this. But
  Kubernetes has **no built-in cross-site/cross-region DR primitive.** A cluster
  is a single site (at most a "stretched" cluster across low-latency zones).
  Cross-site DR is *always* an added layer: storage-level replication (CSI),
  multi-cluster management (Red Hat ACM / Submariner), backup-restore (Velero),
  or **application-level replication.**
- *(Data)* IBM's recommended pattern is that **MQ owns its own replication** —
  Native HA for in-cluster HA, MQ's own cross-region mechanism for DR — rather
  than depending on the storage tier to replicate its logs.
- *(Judgment)* Leaning MQ's DR on a platform storage-replication tier is likely
  the **wrong** call here, for the exact reason §2.4 of the authoritative design
  rejected NFS/filer replication: it offloads message-data replication to an
  asynchronous infrastructure layer with a coarse RPO, when the whole thesis is
  *the data layer should own replication.* Expectation: **piggyback on
  Kubernetes for HA; have MQ own DR.**
- *(The nuance that makes it a real question)* The firm may nonetheless have a
  *blessed platform DR pattern* for stateful workloads that they expect everything
  to use. If so, that is a gap we must understand — it could mean modeling *their*
  DR layer alongside, or instead of, MQ-native DR. This is why it leads the
  question bank (§6 bucket A).

**Confirmed 2026-06-16:** IBM's own DR answer for Native HA *is* MQ-owns-DR —
**CRR** (§4.4), asynchronous, MQ-layer replication. The piggyback judgment holds;
the only open part is whether the firm mandates a platform DR pattern we must
*also* accommodate.

## 6. Gap-analysis question bank

The lab proves a *concept*; its value depends on knowing where the lab diverges
from the firm's reality and whether each divergence *matters*. Because the firm
is **greenfield for MQ** (§1.1), the bank splits by *who can answer*:

### Bucket A — for the platform / Kubernetes / security team (answerable)
Not MQ questions, so the "no MQ experience" gap does not apply — they have a
Kubernetes practice.

- Which OpenShift distro/version; on-prem bare metal / VMs / cloud; **x86 or
  arm64?**
- **For stateful workloads generally: how do you do HA, and how do you do
  cross-site DR?** (storage replication / ACM / Submariner / stretched cluster /
  backup-restore — or "stateful DR is unsolved"). *This is the §5 piggyback
  question, correctly aimed.*
- One **stretched cluster** across DCs, or **two independent clusters** per DC?
  (decides whether MQ DR is intra- or cross-cluster; validates the §4.2 shape).
- Inter-DC latency/bandwidth (sync-vs-async feasibility).
- CSI/storage provider; GitOps tooling (ArgoCD/Flux); observability stack
  (alignment with the lab's Prometheus/Grafana obs stack).
- **Security specifics:** what exactly do they require to "control and limit
  access" (NetworkPolicies, ingress type, mTLS, egress), and is the
  DMZ-exemption-for-Kubernetes blessed **in writing**?
- **Release stream — LTS or CD?** CRR (the DR mechanism, §4.4) is **CD-only
  (9.4.2+), not in 9.4.0 LTS**, and is a **paid license add-on**. Which stream
  will they run, and is the CRR add-on budgeted? *(Unknown as of 2026-06-16; the
  author is asking the firm today. Pivotal — it gates the timeline of the whole
  play, §7.)*

### Bucket B — the third-party gateway & the DTCC interface (answerable, but from the vendor relationship / contract / DTCC, not internal MQ staff)
The one place MQ-adjacent reality exists today.

- What does the current **third-party gateway** actually do — what protocol and
  direction is the firm→gateway link, and what must in-house MQ replace?
- **Connection direction to DTCC:** does DTCC initiate **inbound** to us, or do
  we initiate **outbound** to DTCC? (Changes the firewall-hole story entirely;
  partly pre-answerable from DTCC's public FICC EPN MQ guide — §9.1 of the
  authoritative design already references it.)
- DTCC's mandated transport/security and resilience/test requirements (public
  floor documented in §4.6; the firm's contractual specifics TBD).

### Bucket C — questions the lab owns (research agenda, not questions for the firm)
No internal MQ expertise exists, so these become the lab's job, informed by IBM
docs and the engagement.

- **Resolved 2026-06-16: the DR mechanism is CRR** (§4.4). Remaining: verify
  **MQ Advanced for Developers unlocks CRR** for non-prod lab use, and confirm the
  **CRR replication-endpoint exposure** cross-cluster (Route vs LoadBalancer,
  ports, TLS).
- MQ Operator + Native HA CRD + Routes specifics on OpenShift.
- Whether MQ-owns-DR or platform-DR is right, *given* what Bucket A reveals.

### Pre-declared "probably doesn't matter" gaps (state them, don't hide them)
- **OKD (community) vs. licensed OCP** — expected immaterial to the mechanics.
- **arm64 lab vs. x86 prod** — Operator/CRD/Route behaviour is arch-identical;
  lab scope is functional correctness, not timing (§6 of the authoritative
  design).
- **Emulation/timing non-representative** — known and accepted; functional
  validation only.

Listed explicitly so no one mistakes silence for an oversight.

## 7. Risks & open questions

- **CRR is Continuous-Delivery-only and a paid add-on (headline risk).** *(Data)*
  CRR landed in **MQ 9.4.2 (CD, Feb 2025)** — it is **not in the 9.4.0 LTS**
  baseline Appendix C assumes — and it is a **paid production license add-on**
  (expanded for K8s/OpenShift in 9.4.3). *(Judgment)* For a risk-averse,
  LTS-preferring firm needing this by October, that is a real tension: they must
  run a CD stream or wait for the next LTS to roll CRR up. **The firm's CD-vs-LTS
  posture is currently unknown and pivotal** — it gates the timeline of the whole
  play. Question raised to the firm (§6 bucket A, author asking 2026-06-16);
  verify the lab dev-entitlement unlocks CRR before building (§6 bucket C).
- **CRR is newer/less-proven** (~16 months old at writing) — confirms the
  learning-cost flag (#187 §6); the DR *mechanism* is now understood (§4.4), but
  hands-on operational experience is still to be earned in the lab.
- **Feasibility of multi-node OKD on arm64 nested libvirt** (§4.7) — gated by the
  Phase-A spike; SNO-per-DC is the documented fallback.
- **arm64-lab vs x86-prod arch gap** — believed immaterial (§6); confirm via
  Bucket A.
- **The firm's Kubernetes/security specifics are unconfirmed** — pending direct
  questions (§6); the DMZ-exemption premise needs to be confirmed *in writing*,
  not verbal.
- **Timeline, not soundness, is the real risk** (§3) — team ramp-up and
  deployment inside the engagement window.
- **Substrate-assumption leakage** — if `vm` is baked into topology schema or
  fault primitives elsewhere, this arm becomes a retrofit (already a pivot
  acceptance check; re-assert here).

## 8. Decisions captured / defaults to confirm

Settled in brainstorming (2026-06-16):

1. One combined doc — strategic case up front, lab build as proof. ✅
2. Substrate: multi-node OKD on **arm64 (KVM)**, one cluster per DC, real-VM
   nodes. ✅
3. IBM-recommended stack modelled: **Operator + Native HA CRD + Routes + oc /
   RHCOS**. ✅
4. Ingress: **OpenShift Routes** (settles the earlier LB-vs-ingress fork). ✅
5. App slot: **`QMNATIVE`** replaces the in-house HA QM in the distributed mesh.
   ✅
6. DR mechanism: **Native HA CRR** validated (§4.4) — two clusters,
   sync-local/async-cross, manual switchover/failover via the CRD. ✅
7. CRR endpoint exposure: **OpenShift Routes (passthrough/SNI)** — same mechanism
   as the client ingress; one pattern, two boundaries (§4.4). ✅
8. Pitch posture: **early-warning flag + evidence-building**, staged escalation
   (§3.1). ✅

Defaults recommended, to confirm:

- **Positioning:** parallel exploratory spike for pitch evidence, not a
  disruption of the RDQM mainline (§2).
- **Build staging:** HA-first as Deliverable #1 (one cluster, prove Native HA +
  app-flow slotting), DR designed up front and built as fast-follow — mirroring
  §1's "architect full HADR up front, prove one solid QM first."
- **Feasibility spike** as the Phase-A gate before committing the full arm (§4.7).

## 9. Definition of done (this doc's scope) & next steps

- This design + the gap-analysis question bank captured and committed (PR into
  `develop`, issue #198). ✅ on merge.
- Natural next brainstorming drill-downs, in priority order:
  1. **The strategic framing** (§3) — sharpen the pitch surface for the firm.
     *(The CRR endpoint-exposure sub-fork is now resolved — Routes/SNI, §4.4.)*
  - *Pending external input:* the firm's **CD-vs-LTS posture** (§6 bucket A),
    which the author is asking today and which gates the timeline.
- Implementation planning (writing-plans) is **not** triggered yet: build is
  gated behind the RDQM/Pacemaker framework proof (§2) and the §4.7 spike.
