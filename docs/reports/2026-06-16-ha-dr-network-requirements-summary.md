# IBM MQ HA/DR Network Requirements — Executive Summary

> **Issue:** #208 · **Date:** 2026-06-16 · One-page summary. Full detail and
> citations: companion *detailed fact-check* report. **Posture: neutral** — this
> verifies the requirements, it does not recommend an architecture.

## What we checked
A handed-down one-page network-requirements document comparing two IBM MQ HA/DR
options, fact-checked against canonical IBM documentation (primary) and reputable
secondary sources, with adversarial verification. Baseline: **MQ 9.4.x Continuous
Delivery**. We split the comparison into **three** columns once we confirmed Native
HA now runs outside Kubernetes:

- **A — RDQM-DR** on RHEL bare-metal/VM
- **B — Native HA + CRR** on Kubernetes/OpenShift
- **C — Native HA + CRR** on bare-metal/VM Linux *(new — MQ 9.4.4+)*

## The one correction that matters most
**❌ "100 ms for cross-DC" is wrong.** IBM's published limit for RDQM **asynchronous**
DR replication is **50 ms**, not 100 ms — refuted unanimously against three
independent sources (an IBM primary page, the IBM doc mirror, and an IBM engineer's
blog). **No source supported 100 ms.** The intra-DC figure is fine: **✅ 5 ms** is a
real IBM-published synchronous limit (1–2 ms in practice).

## What held up (✅)
- **3-node groups are correct** for both — RDQM = a fixed 3-server quorum; Native HA =
  a 3-instance Raft quorum. (The "3" is deliberate: an odd count gives majority quorum.)
- **RDQM protocols/ports:** DRBD **TCP 7000–7100**, Corosync **UDP 5404–5407**, MQ
  **1414**. RDQM genuinely needs **both TCP and UDP**; Native HA does **not** (no
  Corosync) — so "TCP only" for Native HA is a real contrast *(port still to confirm)*.
- **RDQM floating VIP** (via `rdqmint`) and a **dedicated replication network** are
  supported and sensible.
- **CRR** was introduced in **9.4.2**; **Native HA + CRR run outside containers as of
  9.4.4** — which is why column C exists at all.

## What needs reframing (⚠️) or is unverified (❓)
- **"Floating VIP" does not apply to Kubernetes (B).** Client access there is a
  **Service/Route**, not a VIP. On Linux Native HA (C) clients use a **connection-name
  list**, not a VIP either. The VIP concept is RDQM-specific.
- **"Bonded NICs"** is sound practice but: for RDQM the exact IBM recommendation is
  **unconfirmed** (the best-practices PDF blocked automated retrieval); for Native HA it
  is a **platform/CNI concern, not an MQ requirement**.
- **Unverified this pass** (stated plainly, not assumed true): Native HA/CRR
  **replication port & "TCP-only"**, **CRR being a paid add-on**, `pcsd` **2224**,
  **ICMP/ping**, and replication **MTU/bandwidth/NTP/DNS/TLS/mqweb** details.

## Gaps to add to the requirements list
Bandwidth sizing for the replication link (latency *and* throughput govern sync
replication — adding 20 ms cut DRBD IOPS ~41 %), **MTU/jumbo frames**, **time sync
(NTP)**, **DNS/name resolution** (cross-cluster for CRR Routes), **TLS** (mandatory on
OpenShift Routes and for CRR; not default for RDQM), the **mqweb/REST port**, and an
explicit **quorum/split-brain** rationale.

## Sourcing honesty
IBM's documentation site **blocked automated retrieval (HTTP 403)** throughout — not a
paywall, an anti-bot block. Load-bearing numbers were confirmed via the IBM pages that
did resolve, the verbatim IBM doc mirror, LINBIT/DRBD primary docs, and IBM-authored
blogs. **Five priority items still warrant a quick human-credentialed confirmation** —
listed in the source-retrieval annotations doc — chief among them that current 9.4.x
still states **50 ms** for RDQM async DR.

---
*Confidence: 21 of 25 adversarially-verified claims confirmed; the 4 refuted all
concerned the 100 ms figure. Verdicts separate **data** (what IBM says) from
**judgment** (our reasoning) throughout the detailed report.*
