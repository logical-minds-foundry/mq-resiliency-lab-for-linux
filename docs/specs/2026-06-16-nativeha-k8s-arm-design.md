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

- **Reproducibility restored.** The Native HA deployment is declarative end to
  end — the **IBM MQ Operator** reconciling a **`QueueManager` custom resource**,
  GitOps-able, re-appliable, drift-correcting. It is the categorical opposite of
  a by-hand bare-metal install. This directly answers the reproducibility
  objection that motivated the whole pivot.
- **DMZ eliminated.** The cluster's **controlled, policy-enforced ingress**
  replaces the DMZ gateway. Access is limited and inspectable by the mechanisms
  the security team already trusts (NetworkPolicies, ingress/Route config,
  mTLS) — which is *why* they will exempt it from the DMZ.
- **Bare metal eliminated.** Workloads are pods on the existing OpenShift
  estate; no DMZ-specific bare-metal footprint to provision and hand-maintain.
- **Supportability preserved.** This is the **IBM-recommended** deployment shape
  for MQ on Kubernetes (Operator + Native HA), so the leapfrog does *not* trade
  away the IBM-supportability criterion the firm weights highest — it stays
  inside IBM's blessed envelope, unlike a bespoke self-managed cluster.
- **Greenfield tailwind (§1.1).** Nothing to stay compatible with internally.

**Honest framing for the pitch:** this is a long shot on *timeline*, not on
*soundness*. The risk is whether the team can come up to speed and the firm can
deploy it inside the engagement window — not whether the architecture is right.
The doc must say so plainly; overselling the timeline is how trust is lost.

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

### 4.4 DR — cross-site async replication

Cross-site DR replicates from the DC-A Native HA group to a DC-B recovery group,
**asynchronously** (same WAN-latency reasoning as RDQM DR, §4.2 of the
authoritative design: synchronous across out-of-region sites is impractical →
non-zero RPO → a message-loss window the app must reconcile, §4.3). Cutover and
failback drills mirror the §3.1 fault suite, steps 7 (full-site DR) and 9
(planned, reversible role rotation).

> ⚠️ **Trust-but-verify (the riskiest unknown).** The *exact* IBM mechanism for
> Native HA cross-region DR — its configuration surface in the `QueueManager`
> CRD, its supported topologies, and its RPO characteristics — is **not asserted
> here from memory.** It gets the same discipline §4.4 of the authoritative
> design applies to RDQM: validate against IBM MQ 9.4 docs, cite for re-check
> against the licensed version. This is the first item on the lab's own research
> agenda (§6 bucket C) and the natural next brainstorming drill-down.

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

- The exact MQ **Native HA DR mechanism** (§4.4 — the riskiest unknown).
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

- **Native HA DR is the riskiest unknown** (§4.4) — newer, less-proven, and
  carries the learning cost the pivot flagged (#187 §6). Resolve by IBM-doc
  research before any build.
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
  1. **The Native HA DR mechanism** (§4.4) — research against IBM 9.4 docs; the
     riskiest unknown and the gate on the DR half of the design.
  2. **The strategic framing** (§3) — sharpen the pitch surface for the firm.
- Implementation planning (writing-plans) is **not** triggered yet: build is
  gated behind the RDQM/Pacemaker framework proof (§2) and the §4.7 spike.
