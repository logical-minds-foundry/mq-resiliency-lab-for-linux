# IBM MQ HA/DR Network Requirements — Detailed Fact-Check

> **Issue:** #208 · **Date:** 2026-06-16 · **Status:** draft for review
> **Scope:** Fact-check of a handed-down one-page network-requirements document
> comparing IBM MQ HA/DR architectures. Three architecture columns:
> **A** = RDQM-DR (RHEL bare-metal/VM); **B** = Native HA + CRR on
> Kubernetes/OpenShift; **C** = Native HA + CRR on bare-metal/VM Linux
> (non-container, MQ 9.4.4+).
> **Baseline:** IBM MQ 9.4.x Continuous Delivery (CRR in scope).
> **Posture:** neutral — verify, sanity-check, fill gaps. **No recommendation.**

---

## 0. How to read this report

Every claim is labelled **data** (what a source actually says) vs **judgment**
(our reasoning on top of it), per the project's source-discipline rule. Verdicts:

| Verdict | Meaning |
|---|---|
| ✅ **Confirmed** | Supported by a primary IBM page and/or strong multi-source corroboration. |
| ❌ **Incorrect** | Contradicted by sources (adversarial verification refuted it). |
| ⚠️ **Reframe** | True but mis-stated for the substrate, or needs rewording. |
| ❓ **Unverified** | Could not be confirmed against a primary source *in this pass* — not "false", just not yet evidenced. State plainly. |

**Sourcing caveat (applies throughout).** `ibm.com/docs` and `ibm.com/support`
returned **HTTP 403** to automated retrieval (anti-bot, not paywall). Load-bearing
numbers were confirmed against the **IBM 9.2/9.3 primary pages that did resolve**,
the **`setgetweb.com` verbatim mirror**, the **LINBIT/DRBD** primary docs, and
**IBM-authored community blogs**. URLs still needing human-cred confirmation are in
the companion *source-retrieval annotations* doc. Where a number was only seen on
9.2/9.3 pages, we flag that the 9.4.x page should be confirmed unchanged.

---

## 1. Verdict summary (the at-a-glance table)

| # | Document claim | A: RDQM-DR | B: NHA/CRR on K8s | C: NHA/CRR on Linux |
|---|---|---|---|---|
| 1 | 3 nodes per DC (1 primary + 2 secondaries) | ✅ | ✅ *(reframe terms)* | ✅ |
| 2 | Protocols: RDQM=TCP+UDP+ping; NHA=TCP only | ✅ TCP+UDP; ❓ ping | ❓ TCP-only likely, unverified | ❓ same |
| 3 | Floating VIP preferred | ✅ | ❌ N/A → Service/Route | ⚠️ → conn-name list |
| 4 | Each DC a separate subnet/VLAN | ✅ *(should, not just can)* | ⚠️ → separate clusters | ✅ |
| 5 | 5 ms intra-DC / 100 ms cross-DC | ✅ 5 ms; ❌ **50 ms** not 100 | ❓ no IBM number | ❓ no IBM number |
| 6 | Bonded NICs (+ separate pair for replication) | ✅ separate net; ❓ bonding | ⚠️ node/CNI concern | ⚠️ host concern |

---

## 2. Architecture A — RDQM-DR (RHEL bare-metal/VM)

### A1 — Topology: "3 nodes per DC (1 primary + 2 secondaries)" → ✅ Confirmed
- **Data.** An RDQM HA configuration consists of **exactly three** servers running
  three instances of the queue manager; one is the running (primary) instance and
  the other two are synchronously-replicated secondaries, with quorum across the
  three (confirmed 3-0). For cross-site DR, RDQM pairs HA groups — the proven lab
  shape is **3 + 3** (a 3-node HA group at each of two sites).
- **Judgment.** The "1 primary + 2 secondaries" phrasing is correct *at any instant*
  but slightly misleading: the three nodes are equal members and the primary role
  **floats** on failover. Prefer "a fixed 3-node HA group; one node active at a time."
- **Source:** IBM RDQM HA requirements (9.3, primary); corroborated by the repo's
  Phase-C findings (3+3 cutover/failback, RPO 0).

### A2 — Protocols & ports: "TCP + UDP + ping (ping perhaps optional)" → ✅ TCP+UDP; ❓ ping
- **Data (confirmed 3-0).** RDQM HA/DR requires:
  - **DRBD replication — TCP ports 7000–7100** (block-level data replication).
  - **Pacemaker/Corosync — UDP ports 5404–5407** (cluster heartbeat/membership).
  - **MQ listener — TCP 1414** (client/channel traffic; site-configurable).
- **❓ ping/ICMP.** Could **not** be confirmed as a hard requirement in this pass.
  Corosync membership runs over UDP, not ICMP. **Judgment:** your "perhaps optional"
  instinct is right — ICMP is useful for diagnostics but is not documented as
  mandatory. Confirm against the RDQM firewall page (annotations A3).
- **❓ pcsd TCP 2224.** Standard for `pcs` cluster management; **unconfirmed** here —
  list it as expected-but-verify.
- **Source:** IBM RDQM DR firewall requirements (confirmed via primary + mirror).

### A3 — Floating VIP: "preferred" → ✅ Confirmed (expand the trade-off)
- **Data.** RDQM supports a **floating IP address** bound to the active instance via
  `rdqmint` (proven in the repo's RDQM cheat sheet). It requires all three nodes on a
  **common data subnet** so the IP can move between them.
- **Judgment — the trade-off the doc asked us to expand:**
  - *Floating VIP (preferred):* clients use **one** connection name; failover is
    transparent at the network layer. **Cost:** all HA nodes must share an L2/L3
    subnet (constrains network design); the VIP is a single logical endpoint.
  - *Alternative — multi-instance connection-name list / CCDT:* clients list all
    three node addresses and reconnect-retry. **Benefit:** nodes need not share a
    subnet. **Cost:** every client must be configured with the full list, and it
    leans on client reconnect logic.
  - **For a greenfield, risk-averse deployment** the floating VIP is the simpler,
    more transparent client contract — hence "preferred" is reasonable.

### A4 — Segmentation: "each DC *can* be a separate subnet/VLAN" → ✅ (should, not just can)
- **Data.** RDQM supports configurations using two or three IP addresses/subnets,
  separating the **replication** network from the **client/data** network
  (confirmed 3-0).
- **Judgment — your "should vs can":** for a production deployment this is **should**,
  not merely **can**. A dedicated replication subnet/VLAN (i) isolates the
  latency-sensitive DRBD traffic from client traffic, (ii) lets you size/QoS it
  independently, and (iii) is the basis for the separate-NIC recommendation (A6).
  *Caveat:* the **HA floating VIP** still requires the HA nodes to share the client
  subnet (A3), so "separate subnet per DC" applies to the **DR pairing across sites**
  and to the **replication plane**, not to splitting the 3 HA nodes within a DC.

### A5 — Latency: "5 ms within DC / 100 ms cross-DC" → ✅ 5 ms; ❌ **50 ms, not 100 ms**
This is the most load-bearing line in the document, so it gets the most sourcing.
- **✅ 5 ms intra-DC (synchronous HA) — Confirmed (data).** IBM publishes a **hard**
  latency limit for RDQM synchronous replication of **up to 5 ms**, with **1–2 ms**
  the practical target. (Confirmed 3-0; one phrasing 2-1 — still passed.) *This
  validates the document's "5 ms max within DC."*
- **❌ 100 ms cross-DC (asynchronous DR) — Incorrect (data).** IBM's published figure
  for **asynchronous** DR replication is **50 ms**, not 100 ms. The "100 ms" claim was
  **refuted unanimously (0-3)** against *three independent sources*: the IBM 9.2 DR
  requirements page (primary), the setgetweb mirror of the same IBM page, and an
  IBM MQ engineer's community blog. **No source supported 100 ms.**
- **Nuance (data).** RDQM **DR** can itself run **synchronous (≤ 5 ms)** or
  **asynchronous (≤ 50 ms)**. So there are *two* DR numbers, not one.
- **Why latency matters, mechanistically (data, LINBIT primary).** RDQM's synchronous
  mode is **DRBD Protocol C** — a write is **not acknowledged to the application until
  the peer has committed it**, so round-trip latency is added to *every* persistent
  write. LINBIT measured that adding just **20 ms** to the replication link cut IOPS by
  **~41 %**. This is *why* the 5 ms ceiling exists and why async is mandated beyond it.
- **⚠️ Version note.** The confirmed numbers come from IBM **9.2/9.3** pages (the
  9.4.x pages 403'd). They are long-standing and almost certainly unchanged, but the
  9.4.x DR-requirements page should be confirmed (annotations A1/A2). Also confirm
  whether IBM states the figure as **round-trip** or **one-way** — the document says
  "round-trip"; IBM's exact wording should be matched.
- **Sources:** IBM MQ 9.2 RDQM DR requirements (primary); setgetweb mirror of
  `q131690`; LINBIT "impact of network latency on DRBD write performance" (primary);
  Alex Chatt (IBM) RDQM networking blog.

### A6 — Bonded NICs: "for redundancy; +separate bonded pair for replication" → ✅ separate net / ❓ bonding
- **Data (confirmed).** A **dedicated/separate replication network** is supported and
  recommended (A4).
- **❓ Bonding specifically — Unverified.** The IBM "RDQM network interface best
  practices" support page/PDF **403'd** (annotations A6), so IBM's exact bonding
  recommendation could not be quoted in this pass.
- **Judgment.** NIC bonding for redundancy is **standard datacenter practice** and
  consistent with IBM's separate-replication-network guidance; a separate bonded pair
  for replication isolates the latency-sensitive plane *and* removes a single NIC as a
  SPOF. Treat as **sound and expected**, but mark **⚠️ pending primary confirmation**
  before quoting IBM as the authority.

---

## 3. Architecture B — Native HA + CRR on Kubernetes/OpenShift

> Substrate confirmed as **Kubernetes/OpenShift** (MQ Operator), per repo design #198.

### B1 — Topology: "3 nodes per DC" → ✅ Confirmed (reframe the terms)
- **Data (confirmed 3-0).** IBM MQ Native HA deploys **3 replica instances** of the
  queue manager using a **Raft-style quorum with leader election** — one active, two
  replicas kept current by **log replication**. CRR then pairs a **Live** group and a
  **Recovery** group across two clusters/regions.
- **Judgment — reframe.** Don't carry RDQM's "1 primary + 2 secondaries" wording here:
  it's an **active instance + 2 replicas elected via Raft**. The "3 pods" map onto 3
  separate nodes only if anti-affinity spreads them (the repo's 3-node compact cluster
  per DC does this). 6 pods total across the two clusters for CRR.

### B2 — Protocols: "TCP only" → ❓ Unverified (likely true)
- **❓ Not confirmed this pass.** The Native HA / CRR **network-layer** pages (replication
  transport, default port, Route/SNI exposure) **403'd** and could not be primary-confirmed.
- **Judgment (plausible, not evidenced).** Native HA replicates its recovery log over
  **TCP** with **no Corosync/UDP** layer (unlike RDQM) — so "TCP only" is very likely
  *correct* and is in fact a genuine **contrast** with RDQM (no UDP heartbeat ports).
  CRR's cross-region link is TCP, **TLS-secured**, exposed via **OpenShift Routes
  (TLS passthrough + SNI)** or **LoadBalancer** Services. **Confirm the replication
  port and the TLS/Route exposure** against the annotations (B2/B3) before asserting.

### B3 — "Floating VIP preferred" → ❌ Not applicable on Kubernetes
- **Judgment (substrate mismatch).** There is **no floating VIP** on K8s/OpenShift.
  Clients reach a Native HA queue manager through a **Kubernetes Service** (and, for
  external/cross-cluster access, an **OpenShift Route** with TLS passthrough + SNI, or
  a LoadBalancer Service). The Operator points the Service at the active instance — the
  Service/Route *is* the stable endpoint that plays the VIP's role.
- **Action:** rewrite this line for column B as **"stable client endpoint via
  Service/Route (Operator-managed); no floating VIP."**
- **❓** The exact Route/SNI mechanism should be primary-confirmed (annotations B3).

### B4 — Segmentation: "separate subnet/VLAN per DC" → ⚠️ Reframe to clusters
- **Judgment.** On K8s this maps to **two independent clusters, one per DC/region**
  (the repo #198 shape), each with its own pod/service networks. Intra-cluster
  segmentation is **NetworkPolicy**, not VLANs; cross-cluster is the **Route ingress**
  plus the **CRR replication link**. "Separate subnet/VLAN" is an underlay concern
  beneath the cluster, largely abstracted by the CNI.

### B5 — Bonded NICs → ⚠️ Node/platform concern
- **Judgment.** NIC bonding lives at the **worker-node / platform** layer and is
  abstracted from MQ by the **CNI**. It is a sound infrastructure practice but **not an
  IBM MQ requirement** on this substrate. Owned by the platform team, not the MQ config.

---

## 4. Architecture C — Native HA + CRR on bare-metal/VM Linux (MQ 9.4.4+)

> **The key discovery in this engagement:** Native HA is **no longer container-only**.

### C1 — Native HA without Kubernetes → ✅ Confirmed (data, 3-0)
- **Data.** As of **IBM MQ 9.4.4**, Native HA **and** Cross-Region Replication are
  **supported outside containers** — directly on RPM-installed MQ on Linux VMs / bare
  metal. (CRR itself was introduced in **9.4.2**.) Confirmed 3-0; corroborated by IBM's
  dedicated support page *"Basic configuration of IBM MQ 9.4.4 Native HA in 3 RHEL VMs"*
  and IBM developer Jonathan Rumsey's "Native HA beyond containers" (2025-10).
- **Judgment.** This makes column C a genuine apples-to-apples peer of RDQM-DR (both
  bare-metal, 3 nodes/site, same VIP/NIC questions) — **and** it means the document's
  VIP/bonded-NIC language actually fits *this* substrate better than it fits K8s.

### C2 — What changes vs K8s (column B) — Judgment
- **No Operator orchestration.** You manage the 3 instances and failover yourself
  (`crtmqm` + Native HA INI stanzas; 3 instances on 3 servers).
- **Client connection.** No Service/Route — clients use a **multi-address
  connection-name list / CCDT** spanning the 3 nodes (reconnect-retry). A floating VIP
  is **not** the native mechanism here (contrast with RDQM, where it is).
- **Same MQ-level facts as B transfer:** 3-instance Raft quorum, TCP log replication
  (❓ port unverified), CRR async cross-region with possible data loss, TLS for CRR.
- **Maturity caveat (judgment).** 9.4.4 non-container Native HA is **very new** (CD,
  2025) — less field-proven than RDQM. Weigh against the supportability criterion.

### C3 — CRR licensing → ❓ Unverified
- **❓** "CRR is a paid production add-on" could **not** be confirmed against a primary
  licensing page in this pass (the license page 403'd). Repo doc #198 asserts it
  (9.4.3 expanded CRR licensing for K8s/OpenShift). **Confirm before relying on it** —
  it is a budget/procurement gate (annotations B4).

---

## 5. Important items the document is MISSING

Surfaced by the research as gaps a complete network-requirements doc should cover.
Several are **❓ unverified** here — listed so they're not silently omitted.

| Item | Why it matters | Status |
|---|---|---|
| **Replication bandwidth sizing** | Latency *and* throughput govern sync replication; under-provisioned bandwidth stalls writes (LINBIT: +20 ms → −41 % IOPS). Specify min bandwidth per link. | ✅ mechanism confirmed (LINBIT) |
| **MTU / jumbo frames** | Replication links commonly use jumbo frames (MTU 9000) to cut per-packet overhead. Mismatched MTU causes silent fragmentation/perf loss. | ❓ verify IBM/DRBD guidance |
| **Time sync (NTP/chrony)** | Quorum, logs, and TLS cert validity assume synced clocks across nodes/sites. The repo already treats time-sync as first-class (#186). | ❓ not in IBM net-reqs; include anyway |
| **DNS / name resolution** | RDQM commonly uses `/etc/hosts` for the 3 nodes; K8s uses cluster DNS + **cross-cluster resolution of Route hostnames** for CRR. A real requirement for column B. | ⚠️ partial |
| **TLS / certificates** | CRR replication is **TLS-secured**; OpenShift Route SNI **requires** TLS (mandatory on B, not optional). RDQM DRBD replication is **not** TLS by default. | ⚠️ asymmetric across columns |
| **MQ listener port 1414** | The client/channel port — belongs in any net-reqs doc. | ✅ confirmed |
| **REST / `mqweb` port (9443)** | Admin REST API + console; needed for the content-plane automation. | ❓ verify exact port |
| **Quorum / odd-node rationale** | Both architectures need an **odd** member count (3) for a **majority quorum** — *this is why it's 3, not 2 or 4.* Worth stating explicitly. | ✅ (quorum confirmed both) |
| **Split-brain prevention** | RDQM: Pacemaker/Corosync quorum + fencing. Native HA: Raft majority. Both prevent split-brain by majority, but via different mechanisms. | ✅ mechanism confirmed |
| **pcsd TCP 2224 (RDQM)** | `pcs` cluster admin channel — typically required alongside Corosync. | ❓ verify |
| **ICMP/ping (RDQM)** | Diagnostics, not a documented hard requirement. | ❓ verify |

---

## 6. Corrected requirements list (drop-in replacement for the one-pager)

### RDQM-DR (RHEL bare-metal/VM)
- **3-node HA group per site** (one active, two synchronous replicas, quorum). For DR,
  **3 + 3** across two sites.
- **Protocols/ports:** DRBD replication **TCP 7000–7100**; Pacemaker/Corosync
  **UDP 5404–5407**; MQ listener **TCP 1414**; `pcsd` **TCP 2224** *(verify)*. **ICMP
  not required** *(verify)*.
- **Floating VIP** via `rdqmint` (HA nodes share the data subnet) — preferred for a
  single transparent client endpoint; alternative is a client connection-name list.
- **Networks:** **dedicated replication subnet/VLAN** separate from client/data;
  **bonded NICs** for redundancy, ideally a **separate bonded pair for replication**
  *(bonding rec. pending primary confirmation)*.
- **Latency:** **≤ 5 ms** synchronous (1–2 ms practical); **≤ 50 ms** asynchronous DR
  **(NOT 100 ms)**. Beyond 5 ms you must use async and accept a non-zero RPO.
- **Add:** replication bandwidth sizing, MTU/jumbo frames, NTP, name resolution.

### Native HA + CRR — Kubernetes/OpenShift
- **3-instance Native HA group** (Raft quorum, leader election) per cluster; **CRR**
  pairs Live + Recovery across **two clusters** (6 instances total).
- **Protocols:** TCP log replication (**TCP-only likely; verify port**); CRR
  cross-region **TCP + TLS** via **OpenShift Route (TLS passthrough + SNI)** or
  LoadBalancer Service.
- **Client access:** **Kubernetes Service / OpenShift Route** — **no floating VIP**.
- **Segmentation:** **two clusters** (one per DC); NetworkPolicy intra-cluster; Route
  ingress + CRR link cross-cluster.
- **Bonded NICs:** node/platform concern, abstracted by the CNI — **not an MQ requirement**.
- **TLS is mandatory** (Route SNI + CRR), not optional.
- **Latency:** **no IBM-published number** for Native HA/CRR — size empirically; CRR is
  async (non-zero RPO).
- **Version:** CRR = **9.4.2+ (CD)**; **paid add-on** *(verify)*.

### Native HA + CRR — bare-metal/VM Linux (9.4.4+)
- Same 3-instance Native HA + CRR model as K8s, **without** the Operator: `crtmqm` +
  Native HA INI; **clients use a connection-name list/CCDT** (no Route, no VIP).
- **New since MQ 9.4.4** — less field-proven than RDQM; weigh maturity.
- Same "no IBM latency number" and "CRR paid add-on *(verify)*" caveats as B.

---

## 7. Open items for human confirmation (browser creds)
See the companion **source-retrieval annotations** doc. Priority (★):
1. ★ **RDQM async DR = 50 ms** on the current **9.4.x** DR-requirements page (we
   confirmed 9.2/9.3; the "100 ms" in the source doc is refuted).
2. ★ **RDQM 5 ms** wording — confirm round-trip vs one-way on the 9.4.x HA page.
3. ★ **Native HA/CRR network layer** — replication transport "TCP only" + default port.
4. ★ **NIC bonding** recommendation — the RDQM network-best-practices PDF.
5. **CRR paid-add-on** licensing wording; **pcsd 2224**; **ICMP**; **mqweb 9443**.

---

## 8. Sources

**Primary (resolved):**
- IBM MQ 9.4 — Requirements for RDQM HA solution:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-requirements-rdqm-ha-solution>
- IBM MQ 9.4 — Requirements for RDQM DR solution:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=recovery-requirements-rdqm-dr-solution>
- LINBIT — Impact of network latency on DRBD write performance:
  <https://linbit.com/blog/the-impact-of-network-latency-on-write-performance-when-using-drbd/>
- IBM Community (J. Rumsey, IBM) — Native HA beyond containers (2025-10):
  <https://community.ibm.com/community/user/blogs/jonathan-rumsey/2025/10/14/nha-beyond-containers>

**Secondary / mirror (used to confirm 403'd primaries):**
- setgetweb mirror of IBM `q131690` (RDQM replication requirements):
  <https://www.setgetweb.com/p/MQ92/con/q131690_.htm>
- IBM Community (A. Chatt, IBM) — RDQM networking best practices (2024):
  <https://community.ibm.com/community/user/blogs/alex-chatt/2024/09/24/rdqm-networking-setup-best-practices-and-diagnosin>
- IBM support — Basic configuration of MQ 9.4.4 Native HA in 3 RHEL VMs:
  <https://www.ibm.com/support/pages/basic-configuration-ibm-mq-944-native-ha-3-rhel-vms>

**403'd (need human-cred retrieval):** see the source-retrieval annotations doc.

> **Research provenance:** deep-research harness, 2026-06-16 — 5 search angles, 20
> sources fetched, 38 claims extracted, 25 adversarially verified (3-vote), **21
> confirmed / 4 refuted**. The 4 refuted all concerned the **100 ms** async figure.
