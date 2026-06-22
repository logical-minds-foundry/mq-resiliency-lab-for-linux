# IBM MQ Native HA on Kubernetes/OpenShift — Arm Design (`nativeha-ocp`)

> ## ⏸️ ON HOLD (2026-06-16) — paused, not cancelled
>
> **The app has decided not to pursue Kubernetes.** They are committed to a
> **DMZ-based deployment on bare metal / VMs**, so this Native-HA-on-Kubernetes
> arm is **paused**. The design below is complete and remains valid — revisit it
> if the Kubernetes option reopens.
>
> **Its lasting value:** it forced the lab-security / PKI foundation
> ([`2026-06-16-lab-pki-design.md`](2026-06-16-lab-pki-design.md), #201) to be
> built properly — which was needed regardless. Active focus moves to **lab
> security**, **platform parity** (RDQM/Pacemaker), and **testing infrastructure**.

> **Status:** design, first pass — brainstormed & pushback-reviewed 2026-06-16.
> **(ON HOLD — see banner above.)**
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

1. The app standardizes on **RHEL/OpenShift** and is deliberately
   risk-averse — IBM-supportability first, deviation from the status quo second
   (consistent with the authoritative design's most heavily-weighted criterion,
   §2.7/§3, and confirmed at engagement start).
2. Outside Kubernetes, the app MQ service is expected to land in a
   **DMZ**.
3. The DMZ has **no support for VMs** → it forces **bare metal**.
4. Bare metal has **no support for the kickstart/automation estate** → it forces
   **manual, by-hand installation**.
5. Manual installation is **not reproducible** — for a critical, regulated,
   greenfield service, that is serious technical debt the moment it ships.

The escape hatch the app itself surfaced:

> The security team will **exempt a workload from the DMZ if it runs in
> Kubernetes**, because they are satisfied they can control and limit its access
> there.

And Kubernetes has exactly one first-class HA mechanism for a queue manager:
**IBM MQ Native HA** (container-native, log-replication quorum). So the escape
hatch and the HA mechanism are the same decision.

**The play:** if the lab can demonstrate *reliable HADR message flow* through a
Native-HA-on-Kubernetes deployment — slotted into the same app↔QM↔SVC
architecture already built — that evidence becomes the basis to propose
Kubernetes as a **secondary, or even a replacement, architecture**. Success
collapses the DMZ + bare-metal + manual-install complexity into a single, far
simpler ask: **punch controlled firewall holes to a Kubernetes ingress** so
remote queue managers can reach the app service.

### 1.1 Greenfield — a tailwind, not only a challenge

The app has **zero internal MQ infrastructure**. No one there has MQ
experience; the engagement exists precisely to build this app, off a
third-party gateway provider, on a tight timeline. Two consequences:

- **It removes a whole class of objection to the leapfrog.** There is no
  incumbent MQ deployment the Kubernetes path must stay compatible with — "it
  won't match how we already run MQ" cannot be raised, because there is nothing
  to disrupt. The only real compatibility constraints are (a) the app's
  Kubernetes/platform/security standards and (b) the external SVC interface,
  both of which the Native-HA-on-K8s path can satisfy directly.
- **It raises the stakes on the lab being right.** For the next six months the
  lab is effectively the app's *sole* source of MQ HADR truth. The design owes
  the same evidence discipline (§3, §4 of the authoritative design) it always
  did — only now there is no internal expertise to catch a wrong call.

The mission framing for the engagement: navigate the next six months to deliver
a true MVP **without cutting corners** — optimizing *scope and sequencing*, not
*rigor* — and without making this strategic play harder to land later.

## 2. Positioning vs. the RDQM-parity roadmap

The pivot (#187 §6) set a guardrail: **no build work on the Native HA slot until
the framework is proven on the `rdqm-rhel` + `pcmk-ubuntu` pair.** This arm is
positioned as a **parallel exploratory spike that generates evidence for the
DMZ-collapse pitch** — *not* a disruption of the RDQM-parity mainline.

**Guardrail amendment (explicit and bounded, decided 2026-06-16).** A "parallel
spike" technically crosses the #187 guardrail, so the relaxation is made
*consciously and narrowly*, not implicitly:

- **What is unblocked now:** a **time-boxed, builds-nothing-permanent feasibility
  spike** answering the one question the whole play hinges on — *can multi-node
  OKD/arm64 (×2 clusters) even bootstrap and run on the laptop* (§4.7). Justified
  by the engagement's October pressure.
- **What stays gated:** the **full arm build** — HA/DR formation, app-slot,
  observability, declarative content — remains behind the RDQM + Pacemaker proof,
  *and* (new, §4.9) behind the lab-security layer. The line is "spike to answer a
  feasibility question" vs. "build the slot."
- **Reality check:** the arm-backend seam (#187 P2) and the RDQM proof that
  *releases* the guardrail are **not built yet** — latest landed work is P1
  (parity harness on Pacemaker). So "slots into the existing registry" is
  *designed-open, not yet-built*; that openness is the cost the deferral bought,
  not a present capability. Keep #187 in sync with this amendment.
- If the pitch lands, the arm is promoted from exploratory spike to a first-class,
  co-maintained arm.

*(Positioning remains the author's call as the engagement clarifies.)*

## 3. The strategic case (the pitch surface)

This is the half of the doc intended to be *argued*, not just built. The lab
build (§4) is its proof.

### 3.1 Posture — staged escalation, opening as an early-warning flag

**Decided 2026-06-16:** open as an **early-warning flag plus evidence-building**,
not a decision demand. Rationale: credibility must precede deviation from the
IBM/status-quo path, and the author is new to the app — the play is high-reward,
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
message flow (§4); the app confirms the OpenShift/security DMZ-exemption **in
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
  one: the client/SVC **ingress** (Routes, §4.5) and the **inter-region CRR
  replication link** (TLS, async, §4.4). Naming both up front is more honest than
  implying a single boundary.
- **Bare metal eliminated.** Workloads are pods on the existing OpenShift estate;
  no DMZ-specific bare-metal footprint to provision and hand-maintain.
- **Supportability preserved.** The **IBM-recommended** shape (Operator + Native
  HA + CRR), so the play does *not* trade away the IBM-supportability criterion
  the app weights highest — it stays inside IBM's blessed envelope, unlike a
  bespoke self-managed cluster.
- **Greenfield tailwind (§1.1).** Nothing to stay compatible with internally.

### 3.4 Honest framing

This is a long shot on **timeline**, not on **soundness**: the risk is whether
the team can come up to speed and the app can deploy inside the engagement
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

Two **independent OKD clusters**, one per simulated data center, on the existing
two-DC libvirt network topology (§5 of the authoritative design). Each is a
**3-node compact cluster** — schedulable control-plane nodes doubling as workers —
so the Native HA 3-pod quorum spreads across three real, individually-killable
VMs. That is **6 OKD node-VMs total** (3 per DC). 3-node compact is the realistic
*floor*; the §4.7 spike confirms whether the footprint actually fits.

**Storage.** Native HA needs **RWO block persistent volumes** (IBM recommends
ext4/XFS) for each instance's recovery log, so the lab clusters need an in-cluster
**block CSI/storage provider** — a spike build task (candidate: OKD LVM Storage /
local-storage operator — verify). Without it the Native HA pods cannot bind their
logs and will not start.

**Why arm64 — and the load-bearing assumption it rests on.** Unlike RDQM —
hard-locked to x86 by the DRBD kernel module, which is why the RDQM arm pays the
slow TCG-emulation tax — **Native HA has no x86 lock**, so this arm *can* run
**arm64-native and KVM-accelerated**, sidestepping the TCG penalty that otherwise
makes two multi-node OpenShift clusters prohibitive on an M-series laptop. This is
the single decision that turns "probably infeasible locally" into "probably
feasible." **⚠️ It depends on an unverified assumption** — that IBM's **MQ server
container image + MQ Operator + Native HA + CRR are all shipped and supported on
arm64** (arm64 is confirmed for MQ *client/toolkit* tooling, but the
server/Operator/CRR stack on arm64 is *not* yet confirmed — see the open
[mq-container arm64 request](https://github.com/ibm-messaging/mq-container/issues/476)).
**This is the first thing the §4.7 spike verifies, before any OKD build.** If it
fails, the arm falls back to TCG-emulated x86 (heavier — feasibility then leans on
SNO-per-DC or a cloud-x86 box).

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
boundary** (distinct from the §4.5 client/SVC ingress): the CRR replication
endpoints connect cluster-A ↔ cluster-B over the simulated WAN, TLS-secured.

**Endpoint exposure — resolved (validated 2026-06-16).** IBM documents
**OpenShift Routes with TLS passthrough + SNI** as the mechanism for CRR data
traffic — the *same* mechanism as the client/SVC listener ingress (§4.5), so one
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
the strategic case (§3) anchors here. External fixtures (`svc-sim`, `app-client`)
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

Before committing the full arm, a **Phase-A spike** answers the questions the
whole play hinges on, **in order, cheapest-to-kill first** (spike-first,
checkpointed):

1. **arm64 support (first, blocking).** Confirm IBM ships and supports the **MQ
   server image + Operator + Native HA + CRR on arm64** — inspect the Operator/QM
   image manifests for an arm64 variant and check IBM's CRR arm64-support
   statement. Decisive and cheap; do it before standing up anything. **If
   unsupported → the arm64 lever is gone**, and the spike instead asks whether two
   multi-node x86 OKD clusters can run under TCG, or whether to fall back to
   SNO-per-DC / cloud-x86.
2. **Footprint/RAM (second).** Estimate, then measure, whether **two 3-node
   compact OKD clusters (6 node-VMs)** fit the dev-VM budget — the §7.5 sizing was
   built for ~6 × 1 GB VMs, and OKD nodes are far heavier (§7).
3. **Bootstrap (third).** Confirm multi-node OKD/arm64 on nested libvirt actually
   bootstraps and runs.

**Fallback if multi-node won't fit: Single-Node OpenShift per DC** — keeps the
OpenShift surface (Operator, Routes, `oc`, RHCOS) but puts all three Native HA
pods on one node, so it demonstrates pod-failure HA + cross-site DR rather than
node-failure HA. Record the compromise explicitly if reached.

### 4.8 App-flow slotting

The app HA queue manager becomes **`QMNATIVE`**, replacing `QMPCMK`/`QMRDQM`
as the app substrate in the existing distributed architecture
([`2026-06-13-distributed-mq-architecture-design.md`](2026-06-13-distributed-mq-architecture-design.md)):
`QMNATIVE ↔ QMSVC` across the simulated WAN, `app-client` puts trades,
`svc-sim` replies. **Same app contract, new substrate** — so the arm drops into
the message flow already built, and the comparison stays like-for-like.

**Fixture wiring (cross-substrate).** `app-client` and `svc-sim` stay containers
on the dev-VM runtime, but they now reach `QMNATIVE` **as external clients through
its Route** — which is *good* fidelity: they behave exactly as a remote
counterparty/app would in production. The consequence: the fixtures must speak
**TLS + SNI** to traverse the Route (plaintext SVRCONN won't pass), which ties
directly into the security dependency (§4.9).

### 4.9 Security is a hard, up-front dependency (TLS is not optional here)

*(Decided 2026-06-16, elevated from a pushback finding.)*

On the VM arms, security is deferred per §1 of the authoritative design — channels
can run plaintext for convenience. **On this substrate that is impossible:**

- **OpenShift Route SNI routing requires TLS** — the router selects the backend
  from the SNI hostname *inside the TLS handshake*. No TLS, no routing. This binds
  the client/SVC ingress (§4.5), the CRR replication link (§4.4), the admin REST
  Route (§4.10), and the fixture connections (§4.8).
- **CRR replication is TLS-secured** between sites by design.

So TLS — per-instance SNI certs, a CA, and cross-cluster trust between the two
clusters' endpoints — is **mandatory plumbing**, not optional hardening. Rather
than bolt on a minimal cert hack, the decision is to **do it properly:**

- **The lab-security layer (TLS + cert/PKI management, securing the stack) becomes
  a hard, blocking dependency.** The `nativeha-ocp` arm **will not be implemented
  until that security layer is brainstormed → designed → planned → built.** It is
  the *next* design effort after this spec.
- This **raises the security bar lab-wide**, not only for this arm — a consequence
  accepted as the price of doing it right.
- **Distinguish two things:** *transport-TLS-as-plumbing* (mandatory here, in
  scope) vs. *security posture/hardening* — auth policy, mTLS client-auth, channel
  exits, SVC's mandated transport security — which remains a separate,
  requirements-driven effort (still out of scope for *this* arm, per §1). The
  DMZ-vs-NetworkPolicy security-equivalence argument for the pitch also lands in
  that workstream.

### 4.10 Mandatory MQ bolt-ons on this substrate (admin REST API + metrics)

Two components are **non-negotiable over and above the base QM**, and both need a
substrate-specific solution. Good news: on the Operator substrate the Operator
does most of the lifting (more tractable here than on the VM arms).

**Admin REST API (`mqweb`) — required; co-located in-pod.** Without it the content
plane (`pymqrest`, the systems-management automation) is dead, and the only
fallback is **PCF** — not implemented in the lab and far more engineering — so
making REST work is strongly preferred. *(Data)* The MQ Operator runs the web
server (admin REST API + console) **inside the QM container**, exposed via a
**Route** (the metrics config lists `web` as a source, confirming mqweb runs
in-pod). This is the supported pattern and mirrors the VM-arm §8.3 logic (mqweb
travels with the active QM; VIP there, Route here). **Engineering tasks:** enable
`web` in the CRD, wire the Route, manage its TLS cert (→ §4.9). *(Verify the exact
CRD field.)* The "independent client-connected instance" option is an unnecessary
fallback.

**Metrics — required; native Operator metrics, likely no `mq_prometheus` at all.**
*(Data)* The Operator exposes **Prometheus metrics natively** (config values
`qmgr,web`), collected by OpenShift **user-workload-monitoring** Prometheus as
`ibmmq_qmgr*` series. So the VM-arm `mq_prometheus` client/local-bindings exporter
is likely **unnecessary** here. *(Judgment)* The real decision is **dashboard
parity:** native metric names differ from the `mq_prometheus` (mq-metric-samples)
names the existing Grafana dashboards use — so either **adapt the dashboards to
native metrics** (recommended) or run `mq_prometheus` as a **client-mode
Deployment** to preserve them as-is. Either way, no host/local-bindings exporter.

*Reference (verify):* Monitoring when using the MQ Operator —
<https://www.ibm.com/docs/en/ibm-mq/9.3?topic=operator-monitoring-when-using-mq>.

## 5. The HADR-on-Kubernetes question (can MQ DR piggyback on the platform?)

A natural and important question: can MQ simply inherit Kubernetes' own HADR and
have it "just work"? The honest answer shapes both the design and what we ask the
app.

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
- *(The nuance that makes it a real question)* The app may nonetheless have a
  *blessed platform DR pattern* for stateful workloads that they expect everything
  to use. If so, that is a gap we must understand — it could mean modeling *their*
  DR layer alongside, or instead of, MQ-native DR. This is why it leads the
  question bank (§6 bucket A).

**Confirmed 2026-06-16:** IBM's own DR answer for Native HA *is* MQ-owns-DR —
**CRR** (§4.4), asynchronous, MQ-layer replication. The piggyback judgment holds;
the only open part is whether the app mandates a platform DR pattern we must
*also* accommodate.

## 6. Gap-analysis question bank

The lab proves a *concept*; its value depends on knowing where the lab diverges
from the app's reality and whether each divergence *matters*. Because the app
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
  author is asking the app today. Pivotal — it gates the timeline of the whole
  play, §7.)*

### Bucket B — the third-party gateway & the SVC interface (answerable, but from the vendor relationship / contract / SVC, not internal MQ staff)
The one place MQ-adjacent reality exists today.

- What does the current **third-party gateway** actually do — what protocol and
  direction is the app→gateway link, and what must app MQ replace?
- **Connection direction to SVC:** does SVC initiate **inbound** to us, or do
  we initiate **outbound** to SVC? (Changes the firewall-hole story entirely;
  partly pre-answerable from SVC's public SVC FFH MQ guide — §9.1 of the
  authoritative design already references it.)
- SVC's mandated transport/security and resilience/test requirements (public
  floor documented in §4.6; the app's contractual specifics TBD).

### Bucket C — questions the lab owns (research agenda, not questions for the app)
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
  LTS-preferring app needing this by October, that is a real tension: they must
  run a CD stream or wait for the next LTS to roll CRR up. **The app's CD-vs-LTS
  posture is currently unknown and pivotal** — it gates the timeline of the whole
  play. Question raised to the app (§6 bucket A, author asking 2026-06-16);
  verify the lab dev-entitlement unlocks CRR before building (§6 bucket C).
- **CRR is newer/less-proven** (~16 months old at writing) — confirms the
  learning-cost flag (#187 §6); the DR *mechanism* is now understood (§4.4), but
  hands-on operational experience is still to be earned in the lab.
- **arm64 support for the MQ server/Operator/CRR stack is unverified** — the
  load-bearing assumption under the entire arm64 feasibility lever (§4.2). The
  §4.7 spike checks it **first**, before any build; if it fails, fall back to TCG
  x86 / SNO / cloud-x86.
- **Feasibility of multi-node OKD on arm64 nested libvirt** (§4.7) — gated by the
  Phase-A spike; SNO-per-DC is the documented fallback.
- **OKD footprint vs. the §7.5 sizing budget** — that budget assumed ~6 × 1 GB
  cluster VMs; two 3-node compact OKD clusters are far heavier. The spike's second
  gate is the RAM/footprint check (§4.7).
- **Lab security is now a blocking up-front dependency** (§4.9) — TLS is mandatory
  plumbing on this substrate, so the arm build waits on the lab-security layer
  being designed and built. Adds schedule, but non-negotiable.
- **Two mandatory bolt-ons need engineering** (§4.10) — admin REST API
  (co-located mqweb + Route) and metrics (native Operator metrics + dashboard
  adaptation). Tractable, but explicit build tasks, not freebies.
- **arm64-lab vs x86-prod arch gap** — believed immaterial (§6); confirm via
  Bucket A.
- **The app's Kubernetes/security specifics are unconfirmed** — pending direct
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
5. App slot: **`QMNATIVE`** replaces the app HA QM in the distributed mesh.
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

### Pushback resolutions (2026-06-16)

A `paad:pushback` review hardened the spec. Resolutions:

1. **arm64-support check is the spike's first, blocking gate**, with an explicit
   x86/SNO/cloud fallback (§4.2, §4.7).
2. **#187 guardrail relaxation made explicit and bounded** — spike unblocked, full
   build still gated (§2).
3. **TLS is mandatory → lab security becomes a hard, up-front blocking
   dependency**; the arm waits on a security layer designed/built first (§4.9).
4. **Node count pinned** — 3-node compact OKD per DC (6 node-VMs); RAM/footprint
   is the spike's second gate (§4.2, §4.7).
5. **Storage named** — in-cluster RWO block CSI provider, a spike build task
   (§4.2).
6. **Fixture wiring** — `app-client`/`svc-sim` attach as external clients via the
   Route, must speak TLS+SNI (§4.8).
7. **Mandatory bolt-ons documented** — admin REST API (co-located) + metrics
   (native Operator), with the must-engineer flag (§4.10).
8. **Observability** folded into §4.10 (native metrics + dashboard parity).

## 9. Definition of done (this doc's scope) & next steps

- This design + the gap-analysis question bank captured and committed (PR into
  `develop`, issue #198). ✅ on merge.
- **The next design effort is the lab-security layer** (§4.9) — now a blocking
  dependency for this arm, and the immediate next brainstorm.
- *Pending external input:* the app's **CD-vs-LTS posture** and the other Bucket A
  questions (§6), which the author is asking the app.
- Implementation planning (writing-plans) is **not** triggered yet. The arm build
  is gated behind **three** things: the RDQM/Pacemaker framework proof (§2), the
  §4.7 feasibility spike (arm64 first), and the **lab-security layer** (§4.9). The
  first plannable unit is the **Phase-A feasibility spike**, once the security
  brainstorm is under way and the app's stream answer is in.
