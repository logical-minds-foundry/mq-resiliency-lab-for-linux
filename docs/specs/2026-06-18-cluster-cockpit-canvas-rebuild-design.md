# Cluster Cockpit — Canvas Rebuild & Elevation — Design

**Date:** 2026-06-18
**Issue:** #219
**Builds on:** `docs/specs/2026-06-14-cluster-drill-cockpit-design.md` (the cockpit's
problem statement, collector, role-split sections, colour vocabulary, and integrity
alarm — all still authoritative). This document supersedes that design's **§5
(rendering — spike-decided)** and **§8 (build split)**, and adds the visual elevations.

---

## 1. Why this spec exists (corrected framing)

#219 was opened on the hypothesis that the PCMK cockpit was *lost in a rebase*.
Investigation says otherwise, and the corrected framing changes the work:

- **Plan 1a (collector) landed** (#195): `src/mqlab/clusterstate.py`, the `cluster-state`
  role, and a ~5s timer emit `cluster_*` metrics (quorum, per-node online/unclean,
  resource owner, DRBD/STONITH/iSCSI/daemons). Live-proven.
- **The cockpit *rendering* was never built.** `git log -S` confirms `dashboard.py`
  has **never** contained a single `cluster_*` or Loki panel; `render_cluster_dashboard`
  does not exist. The "beautiful cockpit" was the **design + HTML mockups**
  (`docs/specs/diagrams/cluster-drill-cockpit-*.html`) plus a **live rendering spike**
  (hand-authored JSON loaded straight into Grafana). It evaporated on the next lab
  rebuild because it was never productized into the renderer or provisioned durably.
- Loki/Alloy/obslog **infrastructure** landed (#196), but no dashboard consumes it.

So #219 is **"finish the interrupted build, and elevate it"** — not a regression hunt.
The foundation (a live-proven collector, a worked-out visual design) is solid.

## 2. Goals & non-goals

**Goals**
1. Build the PCMK cluster cockpit as a **dedicated, provisioned, code-generated**
   Grafana board (`uid: lab-pcmk-cluster`) — versioned, so it survives rebuilds.
2. Render the matrix **faithfully to the mockups** — **Canvas** is the target (lab is on
   Grafana **v13.0.2**, Canvas GA), spike-confirmed against the live metrics with **Table**
   as the proven fallback (see §4).
3. **Elevate** it past a now-snapshot: **hero tiles** (instant headline read) and a
   **failover-story timeline band** (watch the cluster move through a drill).
4. **Embed the live Loki log row** in the board — the matrix shows *what* changed, the
   log shows *why*, on one screen.
5. Replace the main board's binary PCMK-A/B tiles with a **per-side roll-up + drill-link**.
6. Build the matrix/hero/timeline/log builders **arm-agnostic** so the cockpits for the
   **other HA/DR arms** — RDQM, **Native HA**, and **CRR** — reuse them with only per-arm
   input tables. PCMK is the first arm and proves the framework; the others follow.

**Non-goals (this spec)**
- The **RDQM / Native HA / CRR cockpit boards** — each a focused follow-on spec: its own
  state collector + a board reusing this framework's builders. Cheap because the builders
  are arm-agnostic (§3.2). Native HA and CRR are **actively under construction** by other
  agents; this spec defines the metric-shape contract (§3.2) they should target so their
  cockpits drop in later without rework — but it does not build those boards.
- **Script-emitted timeline annotations.** v1 annotations are metric-derived (§6.4);
  cutover scripts emitting explicit event markers is a noted future nicety.
- The per-QM **application** view (one QM's queues/channels) — already a separate board.

## 3. Architecture

A new, focused module of **pure, composable panel-builders** generates a **second**
dashboard alongside the existing `lab-fleet-node` board. `src/mqlab/dashboard.py`
(the main board) is left as-is.

```
src/mqlab/clusterboard.py
  # pure functions: data in -> Grafana panel JSON out, no I/O
  matrix(rows, cols, metric_map, y) -> panel               # ①②③; engine spike-decided (§4)
  hero_tiles(folds, y) -> [panel, ...]                     # Stat + sparkline
  integrity_panel(y) -> panel                              # first-class alarm
  timeline_band(signals, annotations, y) -> [panel, ...]   # State-timeline
  log_row(loki_selector, y) -> panel                       # Loki Logs
  perf_section(nodes, y) / net_section(planes, y)          # existing metrics
  fold_side(cells) -> green|amber|red|STALE                # SHARED; STALE preserved (§6.8)
  active_side(owner) -> A|B|none|split                     # SHARED; edge states (§6.8)
  render_cluster_dashboard(topo, arm="pcmk") -> dict       # assembles the board
```

- **`render_cluster_dashboard(topo, arm)`** assembles the builders onto the board.
  The arm-specific inputs — node rows, component columns, metric expressions, Loki
  selector — live in small per-arm tables. The RDQM board (later spec) adds only those
  tables; the builders are unchanged.
- **Canvas is scoped to the matrix** (①②③), where faithful grouped-cell layout earns
  the verbose JSON. **Hero, timeline, logs, perf, network are stock panel types**
  (Stat, State-timeline, Logs, timeseries) — striking *and* low-maintenance.
- **`fold_side` / `active_side` are shared** by the hero tiles, the timeline, and the
  overview roll-up, so those three can never disagree about a side's health.
- **No new Prometheus jobs, no new secrets.** Everything reads existing `cluster_*` +
  `node_*` + Loki. Cluster tooling stays read-only.

### 3.1 Provisioning

The `grafana` role deploys a **second** rendered file
(`build/grafana/dashboards/lab-pcmk-cluster.json`) to `/var/lib/grafana/dashboards/`
beside `lab-status.json` — a new `copy` task + the dashboard rendered by
`mqlab obs dashboard` / `obs up`. The dashboard provider (`dashboards.yml.j2`) already
serves the whole directory, so no provider change is needed.

### 3.2 Arm extensibility (the contract for RDQM / Native HA / CRR)

The framework serves four HA/DR arms; PCMK is built here and proves it. **Adding an arm
requires no new builders** — only:

1. **A state collector** for that arm, emitting metrics in the **same shape** as the
   PCMK `cluster_*` set: a per-node/per-component value with `node`/`member`/`resource`
   (or arm-equivalent) labels, a quorum/health roll-up, an active/owner signal, and a
   replication-backlog signal (out-of-sync bytes) where the arm has one. Same
   fail-loud/STALE discipline. The
   arm's collector source differs (PCMK: `crm_mon`/`drbdadm`; RDQM: `rdqmstatus`/
   `rdqmadm`; **Native HA**: `dspmq`/MQ native-HA status; **CRR**: the cross-region
   replication status), but the *projected metric shape* is the contract.
2. **Per-arm input tables** passed to the builders — node rows, component columns,
   metric expressions, the Loki selector, and which sections apply. (Arms differ in
   shape: PCMK/RDQM are node×component matrices with a storage role-split; Native HA is
   instances × {role, quorum, replication, in-sync}; CRR leans on the replication-backlog
   hero + the replication timeline more than a wide matrix. The builders take rows/cols/metric-map,
   so each arm supplies its own.)

`render_cluster_dashboard(topo, arm)` dispatches on `arm` to the right input tables.
**Coordination note:** Native HA and CRR are under active construction now — their
collectors should target this metric shape as they are built, so the cockpit is a
drop-in rather than a retrofit.

## 4. Rendering decision: Canvas the target, spike-gated (settles 2026-06-14 §5)

**Canvas is the target.** The lab runs **Grafana v13.0.2** (`/api/health`); Canvas is GA
since Grafana 10, and it reproduces the mockups faithfully (grouped headers, coloured
cells, inline DRBD replication band, `★` owner, summary) — directly serving "make it
shine."

**But we do not commit blind — the 2026-06-14 §5 spike still runs.** Canvas is built for
custom annotated *scenes*, not dense *data grids*: our matrix is ~48 data-bound cells
(6 nodes × ~8 columns) plus grouped headers, each cell's colour bound to its own
`cluster_*` series. A **Table** panel (one query → rows × columns, cell-background by
value-mapping) is the natural data-grid tool and the §5 "robust" option. So the first
build step (§8.1) is the spike: **build the matrix both ways against the live
`cluster_*` metrics, judge on real rendering, and freeze the winner** into `matrix()`.
Canvas if it renders the grid cleanly; Table as the proven fallback. Either way the panel
is *generated from code* and unit-tested — never the hand-loaded live JSON that made the
last cockpit evaporate. Colour vocabulary per 2026-06-14 §4.3
(green/amber/red/grey + hatched STALE).

## 5. Board layout — `lab-pcmk-cluster`, top-to-bottom

```
┌─ PCMK Cluster · Infrastructure View ──────────── [Site A active] ─┐
│ HERO  [✔ CLUSTER]  [REPL 12MB·78% ▁▂▅▇]  [ACTIVE A]  [QUORUM 3/3]  │  Stat + sparkline
│       + first-class INTEGRITY light (loud banner on split-brain)  │
├───────────────────────────────────────────────────────────────────┤
│ ① CLUSTER STATUS   Site A card │ DRBD A⇄B card │ Site B card       │  Canvas
├───────────────────────────────────────────────────────────────────┤
│ ② COMPUTE (pcmk-a1..3 / b1..3)  corosync·pacemaker·iSCSI·fence     │  Canvas matrix
│    + mq_fs·mq_vip·mq_vip_ext·mq_qm  (● on the ★ active row)        │  (the heart)
├───────────────────────────────────────────────────────────────────┤
│ ③ STORAGE (san-a/san-b)  role·disk·conn·resync%·out-of-sync·iSCSI  │  Canvas matrix
├───────────────────────────────────────────────────────────────────┤
│ ⟳ FAILOVER TIMELINE  quorum│active│DRBD role│integrity ▼fence ▼cut │  State-timeline
├───────────────────────────────────────────────────────────────────┤
│ ▤ CLUSTER LOGS (live, WARN+, auto-scroll, cluster nodes)           │  Loki Logs
├───────────────────────────────────────────────────────────────────┤
│ ▦ PERF  cpu busy% · SAN disk I/O · DRBD throughput (net-wan)       │  timeseries
│ ▦ NETWORK  hb-a/b★ · san-a/b · wan · data-a/b (tri-state + rx/tx)  │  stock
└───────────────────────────────────────────────────────────────────┘
```

Read top-to-bottom, most important first — mirroring the main board's philosophy and the
2026-06-14 §3 ordering, with the hero band and timeline added above/below the matrix.

## 6. Section detail

### 6.1 Hero tiles + integrity light
A top band of **Stat panels with sparklines**, each a single headline number derived
from existing `cluster_*` series:
- **Cluster health** — `fold_side` over both sides → `{green, amber, red, STALE}` (§6.8).
- **Replication backlog** — DRBD **out-of-sync bytes** + resync %
  (`cluster_drbd_out_of_sync_bytes`, `cluster_drbd_resync_pct`). Healthy = in-sync (0 B).
  **No time/seconds claim:** the lab is functional-only under TCG, so bytes — not an RPO in
  seconds — is the honest data-loss-exposure signal.
- **Active site** — `active_side(cluster_resource_owner)` → `A | B | none | split` (§6.8).
- **Quorum** — `sum(cluster_node_online) by (site)` / 3, with quorate state.

The **first-class integrity light** (2026-06-14 §3.1) sits here as a distinct loud
banner (treatment unlike routine green/amber/red), tripping on split-brain
(`StandAlone`/dual-Primary), `Diskless`, or an `Outdated` secondary being promoted. It
must be unmistakable from a routine (amber) resync.

### 6.2 The three matrix sections (①②③)
Exactly the role-split of 2026-06-14 §3.1, rendered via `matrix()` (engine per §4):
- **① Cluster status** — per-site cards (quorum n/3, QM owner, active/standby) + a
  cross-site **DRBD card** (direction, resync %, out-of-sync **bytes**).
- **② Compute** — rows `pcmk-a1..3` + `pcmk-b1..3` banded by site; node-health columns
  (`corosync`, `pacemaker`, `iSCSI`, `fence`) + resource-group columns (`mq_fs`,
  `mq_vip`, `mq_vip_ext`, `mq_qm`) lighting on the `★` active row.
- **③ Storage** — rows `san-a`/`san-b`; DRBD `role`/`disk`/`connection`/`resync %`/
  `out-of-sync`/`iSCSI target`/`drbd` service.

### 6.3 Failover-story timeline band
**State-timeline panels** over the drill window for the signals that tell the story:
**quorum (n/3)**, **active side (A/B/none/split)**, **DRBD role flip (P/S)**, and
**integrity**.
The matrix is *now*; this band is the *transition* — watch a cutover unfold and see
exactly when each signal moved.

### 6.4 Timeline annotations (v1: metric-derived)
Grafana annotation queries over the same `cluster_*` series mark events on the band with
no new plumbing. The owner-change query must be **holder-agnostic**: `cluster_resource_owner`
carries the holder in a *label*, so an owner change spawns a *new* series and a naïve
`changes(cluster_resource_owner)` won't fire. Annotate instead on a holder-independent
transition — e.g. `changes((count by (resource)(cluster_resource_owner))[5m:])`, or a
derived active-side series — alongside **quorum dip** and **integrity trip**.
*Future nicety (out of scope):* cutover scripts emitting explicit markers for richer labels.

### 6.5 Embedded Loki log row
A **Loki Logs panel** directly below the matrix: live, auto-scrolling, severity-filtered
(WARN+), scoped via a Loki selector to the cluster nodes' relevant units
(corosync/pacemaker/drbd + MQ). Uses the `loki` datasource (uid pinned in
`datasource.yml.j2`). This makes the 2026-06-14 §2 reserved slot real.

### 6.6 Perf + network sections
From existing metrics (2026-06-14 §6), no new telemetry:
- **§3 perf** — per-node CPU busy% (`node_cpu_seconds_total`), SAN disk I/O
  (`node_disk_*` on `san-a`/`san-b`), DRBD throughput (`net-wan` virbr rx/tx).
- **§4 network** — per-plane tri-state (`lab_network_health`) + throughput, scoped to
  the planes this cluster rides (heartbeat `net-hb-a/b` ★, SAN, WAN, data).

### 6.7 Overview roll-up + drill-link
The main `lab-fleet-node` board drops the binary PCMK-A/B group tiles for a **per-side
roll-up**: one tile per site (green/amber/red/STALE) folding the matrix's worst current
cell via the **shared `fold_side`** (§6.8), each drill-linking to `lab-pcmk-cluster`.

### 6.8 Derived-signal state machines (shared, unit-tested)

The shared functions are enumerated explicitly so the hero, timeline, and overview
roll-up never disagree — and so they stay honest mid-drill, which is the whole point:

- **`active_side(owner)` → `A | B | none | split`.** `none` = no resource owner (the QM is
  down everywhere mid-transition); `split` = two owners (a hazard — also trips the
  integrity light). The headline tile shows the real state, never a stale `A` while nobody
  owns it.
- **`fold_side(cells)` → `green | amber | red | STALE`,** precedence
  **STALE > red > amber > green** (per 2026-06-14 §4.3). A silent/blind collector folds to
  **STALE** (hatched), *distinct from* red (down): silently-blind must never masquerade as
  healthy or as a clean failure. The overview roll-up uses this same fold + precedence.

Both are pure functions; every state — including `none`, `split`, and `STALE` — is
unit-tested.

## 7. Replication telemetry — already emitted (no collector change)

The replication hero tile and the DRBD timeline row need DRBD out-of-sync / resync as
series. **Confirmed present:** `clusterstate.py` already parses and emits
`cluster_drbd_out_of_sync_bytes`, `cluster_drbd_resync_pct`, and
`cluster_drbd_role`/`disk`/`conn`. So — unlike the 2026-06-14 §10 open question — **no
collector change is needed;** the cockpit wires to existing series. **This spec adds no
new telemetry at all.**

## 8. Build split (each its own plan/PR, ordered)

*(No collector PR — the replication metrics already exist, §7.)*

1. **Matrix engine spike + framework + ①②③ sections** — **start** with the 2026-06-14 §5
   spike: build the matrix both ways (Canvas and Table) against the live `cluster_*`
   metrics, judge on real rendering, and **freeze the winner into `matrix()`**. Then
   `clusterboard.py` + `render_cluster_dashboard` + the dedicated `lab-pcmk-cluster` board,
   provisioned as a second file. The heart; everything else hangs off it.
2. **Hero tiles + first-class integrity light** — incl. the `fold_side` / `active_side`
   state machines (§6.8).
3. **Failover-story timeline band** + holder-agnostic annotations (§6.4).
4. **Embedded Loki log row** (the §2 slot, made real).
5. **Perf + network sections** (existing metrics).
6. **Overview roll-up + drill-link** on `lab-fleet-node` (uses the shared `fold_side`).
   The one shared-surface PR — sequence **last** and rebase-on-merge (§10).

## 9. Testing & acceptance

- **Unit (100% branch):** every builder (`matrix`, `hero_tiles`, `integrity_panel`,
  `timeline_band`, `log_row`, `perf_section`, `net_section`) and the shared `fold_side` /
  `active_side` — data in, panel JSON / state out, with every `fold_side` and `active_side`
  state (incl. `none`, `split`, `STALE`) exercised. The fold is shared so the overview and
  cockpit cannot disagree.
- **JSON validity:** the rendered board parses and has the expected panel set + `uid`
  (a render test mirroring the existing `lab_dashboard` tests).
- **Cold-rebuild acceptance (load-bearing):** a full VM cold rebuild **plus a real PCMK
  failover/cutover** must drive the matrix through the mid-cutover picture, the timeline
  must record the transition, and the integrity light must stay clean (loud only on a
  genuine split-brain). Lint-green ≠ done.
- `vrg-container-run -- vrg-validate` is the only gate.

## 10. Cross-cutting concerns

- **Fail-loud / STALE** precedence per 2026-06-14 §4.3 + §9: a silent collector hatches
  STALE; a fenced node reads fenced/offline; split-brain trips the integrity banner.
- **No new Prometheus jobs / no new secrets;** read-only cluster tooling.
- **Durability** is the headline lesson of #219: the board is **code-generated and
  provisioned from a versioned file**, never hand-loaded into a live Grafana — so it
  survives the next rebuild.
- **Parallel safety (honest):** PRs 1–5 are **isolated** — new files (`clusterboard.py`,
  the new `lab-pcmk-cluster` board, its provisioning task) that the arm work doesn't touch.
  **PR 6** (overview roll-up) is the one change to the *shared* main board
  (`dashboard.py`), whose VM layer references the arm/node set in `topology.yaml` that the
  Native HA/CRR agents are actively editing — so it is sequenced **last** and rebased on
  their merges. **§3.2** is a coordination point (arm collectors targeting the metric
  shape), not pure isolation.

## 11. Success criteria

1. `lab-pcmk-cluster` renders the full stack — hero band, three canvas matrix sections,
   timeline band, embedded log row, perf, network — from existing metrics, generated by
   `render_cluster_dashboard` and provisioned from a versioned file.
2. **Mid-drill the board tells the story with no log line read:** a fenced compute node
   reads fenced/offline; quorum dips on its site; the resource-group cells relight
   amber→green on the Site-B target row; the storage table shows DRBD flipping to
   Primary on `san-b` with a live out-of-sync byte count; the timeline records the
   transition with fence/cutover annotations; the integrity light stays clean.
3. The main board's per-side roll-up agrees with the cockpit (shared fold) and
   drill-links to it.
4. The matrix/hero/timeline/log builders are arm-agnostic — a follow-on cockpit for any
   other arm (RDQM, Native HA, CRR) needs only a state collector matching the §3.2 metric
   shape plus per-arm input tables, not new builders.
5. Survives a cold rebuild: the board is present and correct with no manual step.
