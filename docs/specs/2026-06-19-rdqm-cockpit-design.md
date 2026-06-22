# RDQM Cluster Cockpit — Design

**Date:** 2026-06-19
**Issue:** #287
**Builds on:** the arm-agnostic cockpit framework (`src/mqlab/clusterboard.py`, §3.2 contract) —
the third arm after PCMK (#219) and Native HA (#279). Adds **no new low-level builders**.

---

## 1. Goal & scope

A code-generated `lab-rdqm-cluster` Grafana board making the IBM MQ **RDQM** cluster legible:
DRBD-replicated, Pacemaker-managed HA wrapped behind `rdqmadm`/`rdqmstatus`/`rdqmint`, plus its
cross-site **DR** (`rdqmdr`). Target the **`rdqm_dr`** setup — RDQM 3+3: site-A HA group
`rdqm_a` (rdqm-a1/2/3) + site-B DR group `rdqm_b` (rdqm-b1/2/3), QM **QMRDQM**, single data VIP
`10.10.1.100` (site A) / `10.10.2.100` (site B after cutover), RHEL 9 (x86_64, TCG).

**In scope:** reuse every `clusterboard.py` builder; **new** work is a state collector + per-arm
input tables (§3.2). RDQM is the richest of the three boards — it has Native-HA-style HA roles +
cross-site DR **and** a real DRBD storage section (which Native HA lacked).

**Out of scope:** changes to the PCMK/NHA boards or the builders (already arm-agnostic); the
distributed mesh (`distributed-rdqm-rhel`, which shares svc/app).

## 2. Dependency

The framework (#219/#279) and the RDQM arm (#227) are merged to develop; build on develop. The
live `rdqm_dr` 3+3 provides the fixtures + live verification. **The QM (QMRDQM) and its DRBD
resources only exist once provisioning forms them** — the collector's parse code is anchored to
**real `rdqmstatus -m QMRDQM` / `drbdsetup status` captures** taken after the QM is up (as #272
captured real DRBD and #279 real dspmq).

## 3. Architecture (mirrors the PCMK/NHA collector→board pattern)

- **`src/mqlab/rdqmstate.py`** — a stdlib-only collector deployed verbatim to the rdqm nodes
  (`/usr/local/bin/lab-rdqm-state`, 5s systemd timer). RDQM owns Pacemaker/DRBD, so it probes
  the RDQM-native commands, not crm_mon:
  - `rdqmstatus -m QMRDQM` — per-QM HA view: which node runs the QM, HA role (Primary/Secondary),
    HA status (Normal/Degraded/Not available), current vs preferred location, the single floating
    IP (address + interface), per-member status, and DR state where reported.
  - `drbdsetup status --verbose --statistics` — DRBD sync state for the QM's replicated volume
    (resync % / out-of-sync bytes). **Reuses `clusterstate.parse_drbd`** verbatim.
  - Bounded/non-blocking probes; a silent/timed-out probe → no fresh sample → the cell reads
    **STALE** (the fail-loud discipline, verbatim from the other collectors).
- **`ansible/roles/rdqm-state/`** — mirrors `cluster-state`/`nativeha-state`: deploys the
  collector + systemd service/timer. `ansible/observability.yml` adds an `rdqm-state` role block
  gated on `rdqm_a`/`rdqm_b` (passing `rdqm_qm` from `qm.name | default('QMRDQM')`). Deployed by
  `mqlab obs instrument rdqm_dr`.
- **`src/mqlab/clusterboard.py`** — add RDQM input tables + an `arm="rdqm-rhel"` render path that
  emits `lab-rdqm-cluster` from the existing builders.

## 4. Metric mapping (`rdqmstatus`/`drbdsetup` → the §3.2 `cluster_*` shape)

Reuse the shared shape so the existing builders + overview roll-up work unchanged; RDQM-specific
facets get `cluster_rdqm_*` names. **Exact field names confirmed against the live capture before
coding** (the `rdqmstatus` format differs from docs — e.g. `rdqmstatus -n` really prints
`Node <name> is online`, not the `Node:/HA status:` blocks the docs imply).

| Source field | Metric(s) | Drives |
|---|---|---|
| node running the QM (`HA current location`) | `cluster_resource_owner{resource="QMRDQM",holder}` | running-on hero · ★ active row · LIVE/RECOVERY chip |
| per-member HA status (Normal/…) | `cluster_node_online{node,member}` (1 if Normal) · `cluster_rdqm_member_status{…,status}` | ② instance rows |
| HA role (Primary/Secondary) | `cluster_rdqm_role{node,member,role}` + `cluster_rdqm_role_code` (coded for the matrix) | ② role column |
| HA status (group) | `cluster_quorate{node}` (1 if Normal) · `cluster_rdqm_ha_status{node,status}` | quorum/health hero · integrity |
| floating IP (addr + iface) | `cluster_rdqm_floating_ip{node,ip,interface}` | Floating-IP hero tile |
| current vs preferred location | `cluster_rdqm_location{node,kind,holder}` | failback signal · timeline |
| DRBD resync % / out-of-sync | `cluster_drbd_resync_pct` · `cluster_drbd_out_of_sync_bytes` (reused) | ③ Storage matrix |
| DRBD conn/disk/role | `cluster_drbd_conn|disk|role` (reused) | integrity (split-brain) |
| DR role/status/backlog (`rdqmdr`) | `cluster_rdqm_dr_role{node,role}` · `cluster_rdqm_dr_status` · `cluster_rdqm_dr_backlog` | ④ DR card · timeline |
| (staleness) | `cluster_state_last_write_timestamp{node,source}` | STALE precedence |

`active_side`/`fold_side` work as-is over `cluster_resource_owner{resource="QMRDQM"}`.

## 5. Board layout — `lab-rdqm-cluster`

- **Title banner:** "RDQM Cluster · DRBD + Pacemaker (rdqmadm-managed) · RHEL 9 (x86_64)".
- **① Cluster status** (one compact full-width row, like NHA): Running-on node · HA status ·
  Nodes online · **Floating IP** (the single VIP + holder) · Integrity.
- **② Instances** (`matrix()`): Site A / Site B matrices banded by fixed group, with the
  data-driven **LIVE/RECOVERY chip** per site (DR-primary = LIVE green, DR-secondary = RECOVERY
  yellow — derived from which site holds the running QM, flips on `rdqmdr` cutover). Columns:
  HA status · role (Primary/Secondary) · QM-running · DRBD in-sync.
- **③ Storage — DRBD** (`matrix()`): resync % · out-of-sync — the storage section RDQM brings
  back (reuses the PCMK storage matrix shape).
- **④ Cross-site DR** (`rdqmdr`): DR role (primary/secondary) · DR status · replication backlog.
- **Failover timeline** (`timeline_band()`): running-on node · HA status · DRBD in-sync ·
  DR-connected, with the always-on QM-move annotation.
- **Logs** (`log_row()`): the rdqm/MQ units on `rdqm-*` hosts (noted stub — file-based AMQERR
  awaits #282/#284, same as the other boards).
- **Perf/network** (`perf_section`/`net_section`): CPU + net-hb (DRBD sync) + net-wan (DR).

## 6. Integrity — PCMK-style (not the NHA reframing)

Unlike Native HA (raft, can't split-brain), RDQM is DRBD under Pacemaker and **can** split-brain
at the storage layer. So the integrity light is PCMK-style — it goes loud on:
- **DRBD StandAlone / dual-primary / Diskless** (the storage hazards, reused from `integrity_panel`),
- **plus RDQM HA status ≠ Normal** (Degraded / Not available),
- gated on data present so no-data reads **STALE**, never green.

## 7. Testing & acceptance

- **Unit (100% branch):** the `rdqmstatus` parse (each field/state incl. Secondary / Degraded /
  Not-available / QM-not-running), the DRBD reuse, and the render projection; the RDQM input
  tables + `arm="rdqm-rhel"` render path produce the expected board (uid, panels, columns).
  Fixtures **captured from real `rdqmstatus`/`drbdsetup`** on the live 3+3.
- **JSON validity + PCMK/NHA boards byte-unchanged** (regression-pinned).
- **Live acceptance:** `mqlab obs instrument rdqm_dr` → the board binds real data — HA status,
  which node/site holds QMRDQM + the floating IP, DRBD in-sync, DR state. A real failover
  (move QMRDQM to another node via `rdqmadm`) drives the matrix + timeline; integrity stays clean.
- `vrg-container-run -- vrg-validate` is the only gate.

## 8. Build split

1. **Collector** — `rdqmstate.py` (parse + render) + `rdqm-state` role + `observability.yml`
   wiring. Anchored to the real fixtures; headless-verifiable + live on-node smoke test.
2. **Board** — RDQM input tables + the `arm="rdqm-rhel"` render path → ① status + ② instances
   (with chips) + ③ DRBD storage + integrity.
3. **DR card + timeline DR signal** — the `rdqmdr` cross-site view.
4. **Logs + perf/network** — retargeted to `rdqm-*` hosts.
5. **Overview roll-up + drill-link** on `lab-fleet-node`.

## 9. Success criteria

1. `lab-rdqm-cluster` renders the full stack (status · instances · DRBD storage · DR · timeline ·
   perf/net) from `cluster_*`/`cluster_rdqm_*`, via `render_cluster_dashboard(topo,
   arm="rdqm-rhel")`, with **no new builders**.
2. Site A / Site B labelled by fixed group; LIVE/RECOVERY derived from the running-QM/DR role.
3. The single floating IP and its holder are first-class; the DRBD storage section is populated.
4. Integrity is loud only on real DRBD split-brain / HA-not-Normal.
5. PCMK + Native HA boards unchanged; survives live verification on `rdqm_dr`.
