# Source-retrieval annotations — IBM pages that 403'd automated fetch

> **Issue:** #208 · **Status:** research complete (2026-06-16). Companion to the
> two reports (exec summary + detailed fact-check) under the same issue. The
> deep-research pass confirmed 21 of 25 verified claims; the **50 ms-vs-100 ms**
> RDQM async figure is **resolved against 9.2/9.3** (IBM = 50 ms, the doc's 100 ms
> refuted 0-3) — the only remaining task on that item is confirming the **current
> 9.4.x** page still says 50 ms.
>
> **Purpose:** the research could not fetch IBM Docs / IBM Support pages directly
> — they return **HTTP 403** to automated (non-browser) requests. This is an
> **anti-bot block, not a paywall or auth gate**: the content is public, the
> machine just can't reach it headless. Every claim below was therefore confirmed
> via **`setgetweb.com` verbatim mirrors** and/or **search-snippet extraction of
> IBM's own text**, never from the primary page body directly.
>
> **Action for a human with browser creds:** open each URL below, confirm the
> quoted wording, and paste the exact sentence back so the load-bearing numbers
> rest on primary IBM text rather than mirrors. Priority items (the numbers we'll
> be challenged on) are marked **★**.

---

## How to read this

| Field | Meaning |
|---|---|
| **Claim it backs** | The requirements-doc line this page is the primary source for. |
| **Confirm** | The specific wording/number to verify on the page. |
| **Fallback used** | What the research leaned on instead (mirror / search snippet). |

All `ibm.com/docs` and `ibm.com/support/pages` URLs below returned **403** to
automated fetch. Prefer the **9.4.x** URL where two versions are listed.

---

## A. RDQM-DR (RHEL bare-metal/VM)

### ★ A1 — Latency: "5 ms intra-DC (HA)" and DR async latency
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=requirements-rdqm-high-availability>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=multiplatforms-requirements-rdqm-ha-solution>
- <https://www.ibm.com/docs/en/ibm-mq/9.4?topic=rdqm-requirements-replication>
- **Claim it backs:** "5 ms max within DC" (synchronous HA) and the cross-DC DR figure.
- **Confirm:** the exact HA latency sentence (mirrors say **≤ 5 ms**, with ~1–2 ms ideal)
  AND the DR-async figure — **mirrors of 9.0/9.2 say 50 ms; the doc's "100 ms" could
  NOT be confirmed on a 9.4 primary page.** This is the single most important number
  to pin: confirm whether current 9.4.x raised it to **100 ms** or still says **50 ms**.
- **Fallback used:** setgetweb.com mirrors of 9.0/9.2 (consistent "5 ms / 50 ms"); search snippets.

### ★ A2 — DR solution requirements (cross-site)
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=disaster-requirements-rdqm-dr-solution>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=dr-requirements-rdqm-disaster-recovery>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=requirements-rdqm-dr-solution>
- **Claim it backs:** cross-DC DR latency/bandwidth; the 3+3 HA/DR shape.
- **Confirm:** the DR replication latency ceiling and any bandwidth guidance.

### A3 — RDQM firewall / ports (TCP + UDP + "ping")
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=rdqm-configuring-firewall-replicated-data-queue-managers>
- <https://www.ibm.com/support/pages/ibm-mq-firewall-security-port-selection>
- **Claim it backs:** "TCP+UDP+ping required (ping perhaps optional)."
- **Confirm:** the exact `firewall-cmd`/service list — Pacemaker/Corosync **UDP**
  ports, DRBD **TCP** port range, `pcsd` **TCP 2224** — and whether **ICMP/ping**
  is actually listed as required (we expect it is *not* mandatory).
- **Fallback used:** search snippets of IBM's firewalld service list + IBM community blog (Alex Chatt).

### A4 — RDQM HA architecture / config
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-rdqm-high-availability>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-rdqm-disaster-recovery>
- **Claim it backs:** "3 nodes per DC (1 primary + 2 secondaries)"; 3+3 for HA/DR.
- **Confirm:** the 3-node HA group definition and the HA-only-vs-HA/DR distinction.

### A5 — Floating IP (VIP)
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ria-add-delete-floating-ip-address-rdqm>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-creating-deleting-floating-ip-address>
- **Claim it backs:** "Floating VIP between the nodes preferred."
- **Confirm:** `rdqmint` floating-IP mechanism and that it requires nodes on a common subnet.

### ★ A6 — Bonded NICs + separate replication network (RDQM network best practices)
- <https://www.ibm.com/support/pages/ibm-mq-rdqm-network-interface-best-practices>
- <https://www.ibm.com/support/pages/system/files/inline-files/IBM%20MQ%20RDQM%20network%20interface%20Best%20Practices.pdf> (PDF)
- **Claim it backs:** "Bonded NICs for redundancy" + "additional bonded NIC pair for data replication."
- **Confirm:** IBM's exact recommendation on NIC bonding and a **dedicated/separate
  replication interface** (this is the primary source for the "separate bonded pair" line).
- **Fallback used:** the support PDF was 403'd; relied on the IBM community best-practices blog.

---

## B. Native HA + CRR on Kubernetes/OpenShift

### B1 — Native HA overview & via Operator
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configurations-native-ha>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=operator-native-ha>
- **Claim it backs:** "3 nodes per DC" (3-pod quorum); "TCP only."
- **Confirm:** the 3-instance log-replication quorum and the **TCP-only** replication
  transport (no Corosync/UDP), plus the default replication port.

### ★ B2 — Native HA CRR (containers) + Operator config
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=containers-native-ha-cross-region-replication>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-native-ha-crr>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=operator-configuring-native-ha-crr-using-mq>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=operating-native-ha-crr-switchover-failover>
- **Claim it backs:** CRR DR mechanism; sync-local/async-cross; manual switchover/failover.
- **Confirm:** that CRR exists in **9.4.2+ (CD)**, is a **paid add-on**, and the
  switchover/failover are manual config operations.

### B3 — CRR endpoint exposure on K8s/OpenShift (the "VIP" reframe)
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=dcqmk-example-configuring-native-ha-cross-region-replication-in-kubernetes>
- <https://www.ibm.com/docs/en/ibm-mq/9.2?topic=dcqmumo-configuring-route-connect-queue-manager-from-outside-red-hat-openshift-cluster> (find 9.4 equivalent)
- **Claim it backs:** "Floating VIP preferred" → on K8s this is a **Service/Route**, not a VIP.
- **Confirm:** client connection is via Route (TLS passthrough + SNI) / LoadBalancer Service.

### B4 — Licensing
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=mq-license-information>
- <https://www.ibm.com/new/announcements/enhancing-security-productivity-and-resilience-with-ibm-mq-9-4-3>
- **Confirm:** CRR add-on licensing wording (9.4.3 expanded it for K8s/OpenShift).

---

## C. Native HA + CRR on bare-metal/VM Linux (9.4.4, non-container) — NEW 3rd column

### ★ C1 — Native HA on Linux without containers (the existence proof)
- <https://www.ibm.com/support/pages/basic-configuration-ibm-mq-944-native-ha-3-rhel-vms>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ha-example-deploying-simple-native-configuration-linux>
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=cd-whats-new-changed-in-mq-944>
- <https://www.ibm.com/new/announcements/enhanced-resilience-compliance-and-simplicity-with-ibm-mq-9-4-4>
- **Claim it backs:** "Native HA does NOT require Kubernetes — non-container Linux support landed in 9.4.4."
- **Confirm:** the exact 9.4.4 sentence stating Native HA on Linux VMs/bare metal,
  and the `crtmqm` + Native HA INI stanza mechanism, 3 instances on 3 servers.
- **Fallback used:** WebSearch synthesis across two IBM hits (403 on both pages directly).

### C2 — CRR on Linux (non-container)
- <https://www.ibm.com/support/pages/node/7261515> ("Configuring IBM MQ Native HA CRR on Linux")
- <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=dt-troubleshooting-native-ha-native-ha-crr-configurations-linux>
- **Claim it backs:** CRR is available on the Linux substrate too, not just containers.
- **Confirm:** the Linux CRR config steps + client connection-name-list (no Operator/Route).

---

## D. Cross-cutting / "missing items" to add to the requirements list

- MQ network tuning (MTU/jumbo frames, bandwidth): <https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=performance-tuning-your-mq-network>
- MQ listener port / firewall port selection (1414, mqweb/REST): <https://www.ibm.com/support/pages/ibm-mq-firewall-security-port-selection>
- System requirements 9.4: <https://www.ibm.com/support/pages/system-requirements-ibm-mq-94>
- 9.4 fix list (CD): <https://www.ibm.com/support/pages/fix-list-ibm-mq-version-94x-continuous-delivery>

---

## E. Secondary sources actually used (for cross-checking the mirror text)

These resolved fine and corroborated the 403'd primary pages — listed so a human
can sanity-check that the mirror text matches IBM's live wording:

- IBM Community (Alex Chatt, IBM MQ engineer) — RDQM networking best practices (2024):
  <https://community.ibm.com/community/user/blogs/alex-chatt/2024/09/24/rdqm-networking-setup-best-practices-and-diagnosin>
- IBM Community (Jonathan Rumsey) — "Native HA beyond containers" (2025-10):
  <https://community.ibm.com/community/user/blogs/jonathan-rumsey/2025/10/14/nha-beyond-containers>
- IBM Community (Jean de Garrigues) — Native HA CRR (2025-02):
  <https://community.ibm.com/community/user/blogs/jean-de-garrigues/2025/02/27/nativeha-cross-region-replication-mq>
- IBM developer — MQ HA/DR options overview: <https://developer.ibm.com/articles/mq-ha-dr-options/>
- LINBIT / DRBD user guide (for the DRBD replication-latency mechanics behind RDQM):
  <https://linbit.com/drbd-user-guide/drbd-guide-9_0-en/>

---

## F. Research outcome (2026-06-16) & finalization notes

**Resolved by the research pass (21/25 claims confirmed; verdicts in the detailed report):**
- ★ **RDQM async DR = 50 ms, NOT 100 ms** — confirmed against the IBM 9.2 DR page
  (primary, resolved), the setgetweb mirror, and an IBM engineer's blog; the doc's
  100 ms refuted **0-3** (unanimous). **Remaining:** confirm the current **9.4.x** page
  is unchanged (A1/A2).
- ★ **RDQM HA = 5 ms** synchronous (1–2 ms practical) — confirmed. **Remaining:** confirm
  round-trip vs one-way wording on the 9.4.x page.
- **RDQM ports** (DRBD TCP 7000–7100, Corosync UDP 5404–5407, MQ 1414) — confirmed.
- **CRR introduced 9.4.2**; **Native HA + CRR outside containers as of 9.4.4** — confirmed.

**Still resting only on mirror/snippet or unverified — retrieve to close (flagged ❓ in the reports):**
- Native HA/CRR **network layer** ("TCP only", replication port) — B1/B2.
- **NIC bonding** recommendation (RDQM best-practices PDF) — A6.
- **CRR paid add-on** licensing — B4.
- **pcsd 2224** (A3), **ICMP/ping** (A3), **mqweb/REST port** (D).

Anything still mirror-only after human retrieval stays flagged **⚠️ secondary-sourced**
in the detailed report (source-discipline rule).
