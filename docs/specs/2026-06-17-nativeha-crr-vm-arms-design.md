# Design: Native HA + CRR on VMs — `nativeha-rhel` and `nativeha-ubuntu` arms

- **Status:** Draft (brainstorm output, pending review)
- **Date:** 2026-06-17
- **Author:** Phillip Moore (with Claude)
- **Issue:** #246
- **Relationship:**
  - **Un-parks** the `nativeha-rhel` slot reserved in the RDQM-parity pivot
    ([`2026-06-15-rdqm-parity-pivot-design.md`](2026-06-15-rdqm-parity-pivot-design.md)
    §3.1, §6) and re-points its substrate from `container/k8s` to **`vm`**.
  - **Distinct from** the parked container arm
    ([`2026-06-16-nativeha-k8s-arm-design.md`](2026-06-16-nativeha-k8s-arm-design.md),
    #198) — that design remains ON HOLD; this is the **VM realization** of the
    same `native-ha` mechanism, which IBM now supports outside containers.
  - **Reframes** the `pcmk-rhel` bridge arm
    ([`2026-06-17-pcmk-rhel-third-arm-design.md`](2026-06-17-pcmk-rhel-third-arm-design.md),
    #238) as corroborating contrast, not a comparison we chase (§2).

---

## 1. Why this exists

IBM MQ **9.4.4 (CD)** extended **Native HA** and **Cross-Region Replication
(CRR)** beyond containers to native Linux — VMs and bare metal — with replication
performed **entirely by the queue manager** (raft consensus over the recovery
log) and **no dependency on the operating-system kernel**. IBM's own
documentation now names Native HA *"the preferred solution to RDQM in most
circumstances because it does not have dependencies on the underlying operating
system kernel"* (§9 references).

That single change collapses two constraints the lab had treated as fixed:

1. **The RDQM x86/RHEL lock is gone for this mechanism.** RDQM is bound to RHEL
   x86-64 by its DRBD **kernel module** (the 9.4.5 System Requirements report
   states *"RDQM is supported on RHEL x86-64 only"*). Native HA has no kernel
   module, so it is **kernel- and distro-portable** across IBM's supported Linux
   platforms.
2. **The OS is now a free variable.** MQ 9.4.5 supports Native HA + CRR on
   **RHEL 9** and **Ubuntu 24.04 LTS** as peers (§3.3). The only reason the lab
   leaned RHEL was IBM-supportability comfort; the client has **stronger internal
   support for Debian/Ubuntu**, so Ubuntu re-enters as a first-class candidate.

**The strategic thesis.** Native HA is the long-term, in-service architecture:
replication belongs **inside the application/queue manager**, not offloaded to a
storage tier (DRBD). RDQM and Pacemaker/DRBD both push message-log replication
down to a block-storage layer beneath MQ; Native HA keeps it in MQ's own commit
path. This design builds the evidence to act on that thesis.

**The operative question, given the October window:** *what can we engineer most
reliably and get running?* This spec is scoped to answer it for Native HA on the
two viable OSes, and to make the choice between them data-driven.

## 2. Primary deliverable and scope

### 2.1 The comparison that matters now

The **headline** is a clean, single-variable comparison:

> **Native HA + CRR on RHEL 9** vs **Native HA + CRR on Ubuntu 24.04 LTS** —
> same mechanism, same platform (x86-64 / TCG), OS the only variable.

This directly informs the deployment-platform decision (RHEL vs Ubuntu) on the
axes that actually drive it: reliability-to-engineer-by-October,
IBM-supportability comfort, and the client's internal support depth.

### 2.2 Corroborating, not focus

The lab already holds the DRBD-based arms — **RDQM on RHEL** and
**Pacemaker/DRBD on Ubuntu 24.04**. Against the new Native HA arms they yield
*mechanism* comparisons (in-QM raft vs storage-layer DRBD) that corroborate the
§1 thesis. These are recorded as supporting evidence; they are **not** the
deliverable, and no work is spent perfecting them here.

### 2.3 Explicitly out of scope

- The **`pcmk-rhel`** arm (#238): a bridge idea — "run the OSS Pacemaker/DRBD
  stack on RHEL instead of RDQM." We are not chasing this comparison. If the
  Native HA direction stalls, it remains available to revisit, but it is not a
  target of this work.
- The **container/OpenShift** Native HA arm (#198): stays parked.
- **Security hardening / TLS** beyond defaults — deferred (§6, §7).
- **Performance/timing claims** — both arms are TCG-emulated x86; functional
  correctness only, qualitative RTO, no numeric cross-arm timing (consistent with
  the authoritative design §6 and the pivot non-goals).

### 2.4 The two arms

| Arm | Mechanism | OS | Arch / accel | Substrate | Status |
|---|---|---|---|---|---|
| `nativeha-rhel` | `native-ha` | RHEL 9.x (current 9.6 box) | x86-64 / TCG | `vm` | **new — this spec** |
| `nativeha-ubuntu` | `native-ha` | Ubuntu 24.04 LTS | x86-64 / TCG | `vm` | **new — this spec** |

Both run **x86-64 under TCG emulation**. arm64 is *not* an IBM MQ server platform
(the 9.4.5 SR report lists only x86-64, POWER-LE, and IBM Z for every Linux
distribution — §3.3); production is x86 regardless, so emulation costs CPU on the
dev host but loses **no fidelity**. This is an accepted constraint, identical to
the tax the RDQM arm already pays.

## 3. Verified platform facts (load-bearing)

All validated **2026-06-17** against IBM canonical docs via the content-API
bypass (§9), and against the cached **MQ 9.4.5 Detailed System Requirements**
report (§9). Separated as data, because the design rests on them.

### 3.1 Native HA off-container

- A Native HA node on Linux *"can be a single container … a Virtual Machine, or a
  bare-metal server"*; all three nodes must share the **same processor
  architecture** and be in the **same geographical region**. (Native HA overview)
- Available from **MQ 9.4.4** base install images; the minimum package set is
  `runtime`, `server`, `gskit`. (Native HA overview / Linux deploy example)
- Replication uses the **raft** consensus algorithm; a quorum (majority) of the
  three instances must acknowledge a log write before it is confirmed.
- **Inter-instance replication is plaintext by default** — *"The leader's log is
  replicated in plaintext over the network links to the two other replicas."*
  TLS is optional hardening (a CipherSpec on the local-instance stanza), **not a
  precondition**. (Creating a Native HA queue manager)

### 3.2 CRR off-container

- CRR (introduced MQ **9.4.2 CD**) adds a second three-instance group as a
  **recovery group**; local replication is synchronous (RPO 0 within a group),
  **cross-region replication is asynchronous** (non-zero RPO).
- **Switchover (planned) and failover (unplanned) are manual** config operations,
  not automatic cross-region failover — the same operational shape as RDQM's
  `rdqmdr`, mapping onto the `mqlab dr` `cutover`/`failback` verbs.
- **Licensing:** Native HA + CRR for Linux is available for production with **MQ
  Advanced** entitlement, or via separate prod/non-prod **add-on** components.
  Whether the lab's **developer entitlement** unlocks CRR is the Phase-0 gate
  (§5, §7).

### 3.3 Supported OS × architecture (MQ 9.4.5, from the SR report)

| OS | Architectures |
|---|---|
| RHEL 8 (8.8), 9 (9.2), 10 (Base) | IBM Z, **x86-64**, POWER-LE |
| SLES 15 (SP4) | IBM Z, **x86-64**, POWER-LE |
| **Ubuntu 22.04 LTS** | **x86-64**, POWER-LE, IBM Z |
| **Ubuntu 24.04 LTS** | **x86-64**, POWER-LE, IBM Z |

- **No arm64/aarch64** appears anywhere — settles the architecture question.
- **Ubuntu 24.04 LTS is supported** on 9.4.5 (correcting an earlier "22.04 only"
  understanding) — this is what makes `nativeha-ubuntu` viable.
- The SR report's only "partial" component markers on Ubuntu are **RDQM** rows
  (RHEL-x86-only); every capability this design uses — Native HA, CRR, mqweb,
  clustering — is fully supported on Ubuntu 24.04.

## 4. Architecture

### 4.1 HA — a 3-node Native HA group (identical at the MQ layer on both OSes)

Each arm forms one Native HA group of three instances, one per VM, across the
lab's existing two-DC libvirt topology (three nodes within a site for HA).

- Create per node: `crtmqm -lr <instance> -lf <kb> -lp <n> -ls <n> -p 1414 QMNATIVE`.
- Configure each `qm.ini` with the peer set:
  ```
  NativeHAInstance:
     Name=<alpha|beta|gamma>
     ReplicationAddress=<host>(9414)
  ```
- `strmqm QMNATIVE` on each; raft elects a leader (the first election requires all
  three; subsequent elections require a majority). The leader becomes the active
  instance and opens the listener on the connectivity address.

Failover is **automatic within the group** (raft re-election). The active
instance is reached through a stable connectivity address (the lab's existing VIP
/ floating-address mechanism, reused from the HA-command work).

### 4.2 DR — CRR (3+3 cross-region)

A second three-instance **recovery group** in the second DC, mapping onto the
existing 3+3 two-DC topology. Cross-region replication is asynchronous; role
swap is operator-driven and surfaced through the established `mqlab dr` verbs
(`cutover` / `failback`), exactly as the RDQM and Pacemaker arms already are.
CRR's cross-region link is TLS-recommended; whether it can run plaintext in the
lab (as the HA path does) is confirmed in the Phase-0 spike (§5).

### 4.3 The Ansible seam — shared role + thin OS adapters

The MQ-layer logic (`crtmqm`, `qm.ini` stanzas, `strmqm`, status, CRR `remotes`
config, `mqlab dr` integration) is **OS-transparent** — identical commands and
arguments on RHEL and Ubuntu. It lives in **one shared, OS-agnostic role**,
`mq-nativeha`.

The genuinely OS-specific surface is confined to a thin adapter layer, selected
by `ansible_os_family` via `include_tasks` — the **same structural-parity pattern**
the `pcmk-*` and `rdqm` roles already use (per-family `install-RedHat.yml` /
`install-Debian.yml`):

| Concern | RedHat / RHEL | Debian / Ubuntu |
|---|---|---|
| Package install | `.rpm` via `dnf`, RHEL MQ component packages | `.deb` via `apt`, Ubuntu MQ component packages |
| Firewall | `firewalld` | `ufw` |
| Mandatory access control | SELinux | AppArmor |
| Service/user/dirs | systemd, `mqm`, `/var/mqm` | systemd, `mqm`, `/var/mqm` (largely identical) |

**The size of that adapter layer is itself a recorded finding** — it is the
empirical answer to "is the OS transparent for Native HA?" If the hypothesis
holds, the delta is small, and that smallness is reportable evidence.

### 4.4 Arm registration and the backend seam

- Add `nativeha-rhel` and `nativeha-ubuntu` to `lab/topology.yaml`'s `arms:`
  block (mechanism `native-ha`, OS `rhel` / `ubuntu`, substrate `vm`), updating
  the slot the pivot reserved (which assumed `container/k8s`).
- Implement the **`native-ha` mechanism backend** behind the existing
  `mqlab qm` / `ha` / `dr` verb contract (pivot §3.3). HA formation and CRR
  cutover/failback are the backend-specific operations; the `mqlab dr` Python
  semantics (RPO / reconcile / ledger) are arm-agnostic and reused unchanged.
- **Guardrail note.** The pivot (#187 §6) gated `nativeha-*` build work behind the
  framework being proven on the `rdqm-rhel` + `pcmk-ubuntu` pair. That framework
  has since matured (the parity harness, the arm registry, the `pcmk-rhel`
  extension, the salt roster). This spec **consciously releases that guardrail**
  for the Native HA VM arms as the now-priority work. Phase 1 confirms the
  arm-backend seam is sufficient and extends it where it is not.

### 4.5 App-flow slotting

`QMNATIVE` replaces `QMRDQM` / `QMPCMK` as the in-house HA queue manager in the
distributed mesh
([`2026-06-13-distributed-mq-architecture-design.md`](2026-06-13-distributed-mq-architecture-design.md)):
`QMNATIVE` ↔ `QMDTCC` across the simulated WAN, `app-client` puts trades,
`dtcc-sim` replies. Same app contract, new substrate — the arm drops into the
existing message flow so the comparison stays like-for-like.

## 5. Phasing (one unit of work; PR granularity flexible)

- **Phase 0 — CRR-entitlement spike (HARD GATE, blocking, cheapest-first).**
  Confirm the lab's MQ dev entitlement unlocks Native HA **CRR** on Linux (and
  confirm whether the CRR cross-region link runs plaintext in-lab). If CRR is not
  available, the effort **stops** — Native HA without CRR is not a usable target
  here. Expected to pass, but proven not assumed.
- **Phase 1 — Native HA HA-first on RHEL 9.** Build the `mq-nativeha` role +
  RedHat adapter; form a 3-node group on the existing RHEL 9.6 box; reach first
  automatic failover; slot `QMNATIVE` into the mesh. **Acceptance: a full VM cold
  rebuild proves it one-pass** (cold-rebuild acceptance gate).
- **Phase 2 — Parameterize to Ubuntu 24.04.** Add the Debian adapter; form the
  group on the 24.04 base; reach first failover. This is the direct test of the
  §4.3 OS-transparency hypothesis — record the adapter-layer delta.
- **Phase 3 — CRR (3+3) fast-follow on both arms.** Add the recovery group and
  wire `mqlab dr` `cutover`/`failback`; exercise the §3.1 fault suite + DR drills
  on each arm; produce the RHEL-vs-Ubuntu comparison from recorded harness output.

## 6. Sequencing vs other workstreams

This is **high-priority** work and runs **before** the lab-security layer
(#201 PKI), the observability additions, and the Ansible→Salt migration. The
ordering is sound because Native HA inter-instance replication is **plaintext by
default** (§3.1) — this arm fits the lab's existing deferred-security posture and
does **not** pull the security layer earlier (unlike the parked OpenShift arm,
where Routes forced TLS). Building the comparison systems first is what makes the
eventual platform recommendation data-driven.

## 7. Risks & open items

- **CRR dev-entitlement (headline risk).** Gated by the Phase-0 spike. If the
  developer entitlement does not unlock CRR, the whole arm is blocked.
- **CRR cross-region TLS.** HA replication is plaintext-default and confirmed;
  whether CRR's cross-region link can also run plaintext in-lab is confirmed in
  Phase 0. If TLS is mandatory there, scope a minimal transport cert for the CRR
  link only (still short of the full security layer).
- **Emulation footprint.** Full HA+CRR is **6 VMs per arm** (3+3) under TCG, on
  top of the existing arms. RAM/CPU pressure on the dev host is real; arms run
  one at a time (pivot non-goal: no concurrent multi-arm execution), which
  contains it. Confirm the footprint fits before Phase 3.
- **Mandatory bolt-ons per arm.** Admin REST API (`mqweb`, for `pymqrest`
  content) and metrics must be brought onto each Native HA arm, as for the other
  arms — explicit build tasks, not freebies.
- **Arm-backend seam readiness.** The `native-ha` mechanism backend is new; Phase
  1 may need to extend the seam (pivot §3.3) rather than merely plug into it.
- **Newness of CRR.** CRR is ~16 months old; operational experience is earned in
  the lab, not assumed.

## 8. Definition of done (this spec's scope)

- This design committed on the issue branch and merged via PR into `develop`.
- The implementation **plan** (writing-plans) produced from it, with Phase 0 as
  the first plannable, blocking unit.
- No build work begins until the Phase-0 CRR-entitlement spike passes.

## 9. References (validated 2026-06-17)

Primary IBM docs were retrieved via the content-API bypass documented in the
repo's IBM-docs fetch practice (browser UA → `oldUrl` → `/docs/api/v1/content/`),
because IBM Docs 403 the default fetch bot. Pin to 9.4.

- Native HA overview (container | VM | bare-metal; raft; no kernel dependency) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=availability-native-ha>
- Creating a Native HA queue manager (`crtmqm -lr`, `NativeHAInstance` stanza,
  "replicated in plaintext") —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ha-creating-native-queue-manager>
- Example: deploying a simple Native HA configuration on Linux (9.4.4+; CRR via
  recovery group) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=ha-example-deploying-simple-native-configuration-linux>
- "Beyond containers: Expanding the reach of Native HA & Cross-region
  replication" (IBM, 2025-10-14) —
  <https://community.ibm.com/community/user/blogs/jonathan-rumsey/2025/10/14/nha-beyond-containers>
- Basic configuration of IBM MQ 9.4.4 Native HA in 3 RHEL VMs (tutorial) —
  <https://www.ibm.com/support/pages/basic-configuration-ibm-mq-944-native-ha-3-rhel-vms>
- Hardware and software requirements on Linux systems (RHEL / Ubuntu / SLES
  families) —
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=linux-hardware-software-requirements-systems>
- IBM MQ 9.4.5 Detailed System Requirements (OS × arch matrix; "RDQM … RHEL
  x86-64 only") —
  <https://www.ibm.com/software/reports/compatibility/clarity-reports/report/html/softwareReqsForProduct?deliverableId=218CA2470429462EB2BC62CD566F4030>
  (cached: `build/IBM-MQ-9.4.5-SystemRequirements.pdf`)
