# Plan C — RDQM 3+3 cross-site HA/DR (design)

**Date:** 2026-06-17 · **Issue:** #233 (P3 of the parity epic #199) · **Follows:**
Plan B (#216, RDQM HA + distributed).
**Goal:** bring the RDQM arm to **DR parity** with the Pacemaker/SAN arm — a
demonstrable cross-site HA+DR RDQM with a forced-DR cutover/failback drill that
mirrors the Pacemaker forced-DR findings (`docs/reports/2026-06-09-forced-dr-findings.md`).
**Substrate:** RHEL 9.6 x86_64 under TCG emulation — **functional only, no timing
claims** (the ms figures below are IBM's published ceilings, not lab measurements).

## 1. Scope & acceptance

**In scope:** the `rdqm_dr` setup — a single DR/HA queue manager **QMRDQM** spanning a
3-node HA group on site A (rdqm-a1/2/3) and a 3-node HA group on site B
(rdqm-b1/2/3), with controlled cutover, **forced** (disaster) cutover with a
**measurable RPO**, and failback. The app rides the cutover via client reconnect.

**Acceptance:** the forced-DR drill (§7) round-trips — app keeps producing across an
abrupt loss of site A, site B takes over, and failback restores site A — with RPO
(messages lost) and RTO (reconnect time) recorded in a findings report. Parity with
the Pacemaker forced-DR drill.

**Out of scope (follow-ups):** the distributed/DTCC inter-business path *under* DR
(Plan B proved it without DR; combining is a later step); the cluster cockpit
dashboard (#219); promoting DR to first-class `mqlab dr` registry verbs (for both
arms). These do not block Plan C.

## 2. Why build from scratch (canonical)

IBM 9.4: *"You cannot upgrade an existing RDQM to be a DR/HA RDQM, you must create a
DR/HA RDQM."* So Plan C creates QMRDQM fresh as DR/HA — it does **not** extend Plan
B's HA QM. (Plan B's `rdqm_ha`/`distributed-rdqm-rhel` setups are unaffected.)

## 3. Topology & addressing

The `rdqm_b` nodes already exist in `lab/topology.yaml` (rdqm-b1/2/3), each with the
disks/NICs DR needs:

- `drbdpool` extra disk (`extra_disk: 10`) — DRBD storage (canonical: each node needs
  a `drbdpool` volume group; each QM uses two logical volumes from it).
- `net-data-b` (10.10.2.4x) — site-B client/data plane.
- `net-hb-b` (172.16.2.4x) — site-B intra-group HA heartbeat.
- `net-wan` (10.99.0.4x) — **cross-site DR replication link** (the "interface used for
  data replication"; ideally an independent NIC — net-wan is dedicated here).

**Floating IPs (one per HA group — canonical: "can be the same or different for each
HA group"):** site-A FIP `10.10.1.100` (as in Plan B), **site-B FIP `10.10.2.100`**
(new). The single-FIP-per-QM limit (#223) is *per group*, so across the two sites we
get two FIPs — directly analogous to the Pacemaker arm's two site VIPs
(10.10.1.200 / 10.10.2.200), and the data-plane DR cutover is unaffected by #223.

**Replication posture:** within an HA group, synchronous (HA); **between sites,
always asynchronous** (canonical, for DR/HA). IBM's published ceilings: 5 ms sync,
**100 ms async** (9.4 — this resolves the 50-vs-100 ms question flagged in #208).

## 4. Substrate — `ansible/site-rdqm-dr.yml`

Provision **both** HA groups DR-ready in one playbook (the Phase-C/D lesson: DR-ready
from the start, never bolt it on). Mirrors `site-pcmk-dr.yml`'s structure:

- Cold-boot SSH guard over `rdqm_a:rdqm_b` (the #151/#160 lesson).
- `rdqm-install` on all six nodes (reuses the Plan B role).
- **Firewall:** open the RDQM ports using IBM's shipped definitions —
  `rdqm-drbd` (TCP 7000–7100) and `rdqm-mq` (TCP 1414) firewalld services
  (`/opt/mqm/samp/rdqm/firewalld/`). DR replication and inter-node traffic need these
  across net-wan/net-hb.
- `mqweb` on all six (REST on every QM — design §1).
- Form the HA group on each site (the `rdqm-ha` role, once per group) — this is the
  "configure an HA group on each site" canonical prerequisite.

`site-rdqm.yml` (Plan B, site-A-only) is unchanged; Plan C adds the DR superset
playbook rather than mutating the HA one.

## 5. QM create as DR/HA — `lab/scripts/rdqm-dr-qm-create.sh`

The canonical six-step DR/HA create (first token = HA role, second = DR role):

1. **primary/primary** DR/HA RDQM on **rdqm-a1**.
2. **primary/secondary** on **rdqm-a2**, **rdqm-a3**.
3. site-A floating IP `10.10.1.100` (`rdqmint`).
4. **secondary/primary** on **rdqm-b1**.
5. **secondary/secondary** on **rdqm-b2**, **rdqm-b3**.
6. site-B floating IP `10.10.2.100` (`rdqmint`).

Each `crtmqm` names the DR partner (the peer site's node) and the replication
addresses on **net-wan**, plus the HA group config (as in Plan B). The exact
`crtmqm` DR/HA flag set is taken verbatim from the canonical "Creating DR/HA RDQMs"
page during planning (cached under `build/refs/ibm-docs/`). The QM carries the same
lab MQSC posture as Plan B (listener, `APP.SVRCONN`, `HA.TEST`).

Secondaries cannot be started while in the secondary role (canonical) — the script
creates them in the right order and lets RDQM/Pacemaker place the active instance.

## 6. Connectivity — the app rides the cutover

The app (`app-client`, already on net-data-a + net-data-b) connects client-mode with
a **CONNAME list of both site FIPs** and `MQCNO_RECONNECT`:
`10.10.1.100(1414),10.10.2.100(1414)`. On an HA failover it reconnects within the
active site; on a DR cutover it reconnects to the other site's FIP and resumes —
exactly the Pacemaker arm's app contract. `e2e-test.sh` already accepts QM + CONN
args (#216), so it drives this directly.

## 7. DR operations + the forced-DR drill (parity with `pcmk-dr-*`)

Three scripts, RDQM-native analogs of the Pacemaker DR family:

- **`lab/scripts/rdqm-dr-cutover.sh a2b|b2a`** — *controlled* cutover. Stop the
  primary-site QM, `rdqmdr` promote the peer site's DR-secondary to DR-primary,
  `rdqmadm -p` to set preferred location. RDQM refuses to promote a secondary while
  the primary is still running and the link is up (built-in split-brain guard), so
  the controlled path stops the source first.
- **`lab/scripts/rdqm-dr-force.sh`** — *forced* cutover (abrupt full-site loss). The
  source nodes are gone (so the old primary is already stopped — the canonical
  safety requirement), so `rdqmdr` force-promotes site B. On site-A return, RDQM
  brings it back as secondary and resynchronizes; failback reverses the roles.
- **`lab/scripts/wan-degrade.sh`** — `tc`/`netem` delay+loss on **net-wan**. RDQM
  encapsulates DRBD (we can't run `drbdadm` like the Pacemaker arm's
  `drbd-degrade.sh`), so we widen the async window at the only layer RDQM leaves open
  to us — the network. This makes RPO measurable and *illustrates the encapsulation
  trade-off* explicitly.

**The drill (acceptance):** provision `site-rdqm-dr.yml` → `rdqm-dr-qm-create.sh` →
**snapshot the provisioned 3+3 (#218)** so re-runs skip the slow rebuild → app drives
load → `wan-degrade` widens the window → abruptly `virsh destroy` rdqm-a1/2/3 →
`rdqm-dr-force` promotes site B → app reconnects to `10.10.2.100` and resumes →
record **RPO** (messages committed at A but not replicated) and **RTO** (reconnect
time) → `rdqm-dr-cutover.sh b2a` failback. Findings →
`docs/reports/<date>-rdqm-forced-dr-findings.md`, mirroring the Pacemaker report and
separating data (observed) from judgment.

## 8. Wiring — registry & setup

- `lab/topology.yaml`: the `rdqm_dr` setup already exists (`groups: [rdqm_a, rdqm_b]`).
  Change its `provision` to `ansible/site-rdqm-dr.yml`, add its `qm` config
  (`name: QMRDQM`, `vip: 10.10.1.100`) and a site-B FIP field (e.g. `vip_dr:
  10.10.2.100`; `vip_ext` stays unset per #223).
- DR stays **script-driven** (parity with the Pacemaker arm, which uses
  `pcmk-dr-*.sh`, not registry verbs). Promoting DR cutover/failback to first-class
  `mqlab dr` arm-registry verbs — for *both* arms together — is a deliberate
  follow-up, not Plan C.
- New artifacts: `ansible/site-rdqm-dr.yml`, `lab/scripts/rdqm-dr-qm-create.sh`,
  `rdqm-dr-cutover.sh`, `rdqm-dr-force.sh`, `wan-degrade.sh`. Reused: `rdqm-install`,
  `rdqm-ha`, `mqweb` roles; `app_requester.py`; `e2e-test.sh`; the #218 snapshot tool.

## 9. Sources

Canonical IBM 9.4 (cached under `build/refs/ibm-docs/ibm-mq/9.4.x/`, retrieved
2026-06-17 via `tools/ibm_doc_cache.py`):

- RDQM disaster recovery and high availability — `configurations-rdqm-disaster-recovery-high-availability`
- RDQM disaster recovery — `configurations-rdqm-disaster-recovery`
- Requirements for RDQM DR solution — `recovery-requirements-rdqm-dr-solution`
- Setting the Preferred Location for an RDQM — `availability-setting-preferred-location-rdqm`
- (planning) Creating DR/HA RDQMs — for the exact `crtmqm` flags.

Lab analogs: `ansible/site-pcmk-dr.yml`; `lab/scripts/pcmk-dr-cutover.sh`,
`pcmk-dr-force.sh`, `drbd-degrade.sh`, `dr-provision.sh`;
`docs/reports/2026-06-09-forced-dr-findings.md`.
