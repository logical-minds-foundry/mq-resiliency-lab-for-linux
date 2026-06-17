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
- **Name resolution on the instrumented cluster.** DR replicates *across* sites over
  net-wan and DRBD identifies peers by name, but the `rdqm-ha` role's `/etc/hosts` is
  same-site-only ("no DNS in the lab"). The mechanism is needed only on the **system
  under test** — the 3+3 cluster (`rdqm_a:rdqm_b`) — because that is the only place
  hostnames are *referenced* (DRBD partners, cluster config). The **surrounding lab**
  (dtcc, app, obs) is scaffolding, hardwired by IP — names there don't matter. So Plan
  C renders a **canonical hosts file from `topology.yaml`** (the same single source the
  Ansible inventory comes from; the renderer is lab-generic and reusable) with
  **per-plane name aliases** — every node IP addressable by name: `<node>`
  (primary/mgmt) plus `<node>-mgmt` / `-data-a` / `-data-b` / `-hb-a` / `-hb-b` /
  `-wan` / `-ext` for each NIC the node declares — and **syncs it to the six
  instrumented nodes**. DR config then names partners on the **net-wan** plane
  unambiguously. *Broader want, separate and NOT a Plan C dependency: real lab DNS fed
  from the same topology source (#234).*
- `rdqm-install` on all six nodes (reuses the Plan B role).
- **Firewall:** open the RDQM ports using IBM's shipped definitions —
  `rdqm-drbd` (TCP 7000–7100) and `rdqm-mq` (TCP 1414) firewalld services
  (`/opt/mqm/samp/rdqm/firewalld/`). DR replication and inter-node traffic need these
  across net-wan/net-hb.
- `mqweb` on all six (REST on every QM — design §1; starts `--no-block`, the Plan B
  TCG fix).
- Form the HA group on each site (the `rdqm-ha` role, once per group, with that site's
  `rdqm_site_nodes`) — the "configure an HA group on each site" canonical prerequisite.

`site-rdqm.yml` (Plan B, site-A-only) is unchanged; Plan C adds the DR superset
playbook rather than mutating the HA one.

## 5. QM create as DR/HA — `lab/scripts/rdqm-dr-qm-create.sh`

**Run as a standalone script, not via the `qm-create` registry verb.** The registry
resolves verbs per *arm*, and both `rdqm_ha` and `rdqm_dr` share the `rdqm-rhel` arm,
whose `qm-create` is the HA `rdqm-qm-create.sh`. Rather than overload that script or
add per-setup verb overrides, the DR create lives in the `rdqm-dr-*` script family and
is invoked directly — exactly as the Pacemaker arm drives DR by standalone
`pcmk-dr-*.sh` scripts, not verbs. `mqlab qm create` stays the HA-only path. The DR
scripts own the DR addresses directly (the two FIPs as args/constants; node planes via
the §4 lab hosts names) — no `QmConfig` change.

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
- **`lab/scripts/rdqm-dr-force.sh`** — *forced* cutover (abrupt full-site loss). Site A
  is brought **down by a hard power-off** (not a graceful shutdown — so in-flight /
  un-replicated messages are lost = the RPO; and **not** erased — the VMs and disks
  persist, ready to power back on). With the source primary down, `rdqmdr`
  force-promotes site B. On site-A power-up, RDQM brings it back as DR-secondary and
  resynchronizes; failback then reverses the roles.
- **`lab/scripts/wan-degrade.sh`** — `tc`/`netem` delay+loss applied on the **active
  site-A primary's net-wan egress** (the replication sender), so it widens the
  *cross-site DR* async window only; intra-site HA rides net-hb/net-data and is
  untouched. RDQM encapsulates DRBD (we can't run `drbdadm` like the Pacemaker arm's
  `drbd-degrade.sh`), so the network is the only layer it leaves open to us. This makes
  RPO measurable and *illustrates the encapsulation trade-off* explicitly.

**The drill (acceptance):** provision `site-rdqm-dr.yml` → `rdqm-dr-qm-create.sh` →
**snapshot the provisioned 3+3 (#218)** so the *whole* drill can be re-run cheaply from
the baseline → app drives load → `wan-degrade` widens the async window → **hard
power-off** rdqm-a1/2/3 (down, not erased) → `rdqm-dr-force` promotes site B → app
reconnects to `10.10.2.100` and resumes → record **RPO** and **RTO** → **power site A
back on** → RDQM resyncs it as DR-secondary → `rdqm-dr-cutover.sh b2a` failback.

**Measuring RPO/RTO:** the app logs every PUT's id as *sent-and-acked* (committed at
the site-A primary). After the forced promote, diff that ledger against the messages
that actually survived on site B (ids / depth on `HA.TEST`): **RPO = acked-at-A −
survived-at-B** (the un-replicated tail the netem window widened). **RTO** = the app's
reconnect-and-resume gap across the cutover. Mirrors how the Pacemaker
`forced-dr-findings` quantified loss. Findings →
`docs/reports/<date>-rdqm-forced-dr-findings.md`, mirroring the Pacemaker report and
separating data (observed) from judgment. (The #218 snapshot is for re-running the
drill from the provisioned baseline, independent of the power-off/on failback path.)

## 8. Wiring — registry & setup

- `lab/topology.yaml`: the `rdqm_dr` setup already exists (`groups: [rdqm_a, rdqm_b]`).
  Change only its `provision` to `ansible/site-rdqm-dr.yml`. **No `qm:` block and no
  `QmConfig` change** — DR is script-driven (§5), so the DR addresses live in the
  `rdqm-dr-*` scripts (FIPs) and the lab hosts names (§4); a `vip_dr` schema field
  would be dead weight nothing reads.
- **Hosts renderer:** add a small renderer that emits the canonical hosts file from
  `topology.yaml` (parallel to the existing inventory renderer). The renderer is
  generic/reusable, but Plan C's `site-rdqm-dr.yml` **syncs it only to the instrumented
  cluster (`rdqm_a:rdqm_b`)** — the nodes that reference hostnames. Distributing it
  more widely is the lab-DNS follow-up's job, not Plan C's.
- DR stays **script-driven** (parity with the Pacemaker arm, which uses
  `pcmk-dr-*.sh`, not registry verbs). The `qm-create` verb remains the HA-only path.
  Promoting DR cutover/failback to first-class `mqlab dr` arm-registry verbs — for
  *both* arms together — is a deliberate follow-up, not Plan C.
- New artifacts: `ansible/site-rdqm-dr.yml`, the hosts renderer + sync step,
  `lab/scripts/rdqm-dr-qm-create.sh`, `rdqm-dr-cutover.sh`, `rdqm-dr-force.sh`,
  `wan-degrade.sh`. Reused: `rdqm-install`, `rdqm-ha`, `mqweb` roles;
  `app_requester.py`; `e2e-test.sh`; the #218 snapshot tool.
- **Backlog (separate issue):** real lab DNS to replace the synced hosts file (§4).

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
