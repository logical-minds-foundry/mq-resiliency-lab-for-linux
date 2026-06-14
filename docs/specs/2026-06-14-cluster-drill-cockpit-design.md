# Cluster Drill Cockpit — PCMK Infrastructure View — Design

- **Date:** 2026-06-14
- **Status:** Design — brainstormed 2026-06-14
- **Issue:** #177
- **Builds on** the layered dashboard (`2026-06-11-observability-layered-dashboard-design.md`:
  `render_dashboard(topo)`, the `node` job, `host-net-state` / `net-reach` textfile
  collectors, `lab_network_*`), the MQ Service panel
  (`2026-06-12-mq-service-panel-design.md`), and the distributed-MQ architecture
  (`2026-06-13-distributed-mq-architecture-design.md`: the
  `mq_fs → mq_vip → mq_vip_ext → mq_qm` resource group, two-VIP model,
  never-both-live invariant, RPO-by-event).
- **Depends (soft):** the §2 log section fills in with the log-streaming work (**#143**).
- **Companion mockups (committed):**
  - `docs/specs/diagrams/cluster-drill-cockpit-layout-options.html` — the three frames considered.
  - `docs/specs/diagrams/cluster-drill-cockpit-pcmk-matrix.html` — the chosen node × component
    matrix (healthy baseline + mid-cutover snapshots).
  - `docs/specs/diagrams/cluster-drill-cockpit-board-layout.html` — the full four-section board.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. The board — four sections](#3-the-board--four-sections)
- [4. Section 1 telemetry — the cluster-state collector](#4-section-1-telemetry--the-cluster-state-collector)
- [5. Section 1 rendering — spike-decided](#5-section-1-rendering--spike-decided)
- [6. Sections 3 & 4 — perf and network from existing metrics](#6-sections-3--4--perf-and-network-from-existing-metrics)
- [7. Overview integration — roll-up + drill-link](#7-overview-integration--roll-up--drill-link)
- [8. Build split](#8-build-split)
- [9. Cross-cutting concerns](#9-cross-cutting-concerns)
- [10. Open questions](#10-open-questions)
- [11. Success criteria](#11-success-criteria)

---

## 1. Problem & motivation

The observability layer shows the cluster as four binary node tiles (VM up/down).
That is fine to start, but it is blind to what is actually *running on each node* and
how the cluster behaves *as a unit*. During an HA failover or a DR cutover — the
events this lab exists to exercise — the operator cannot **watch the HA machinery
move**: quorum holding or breaking, the QM resource relocating, the VIPs floating,
DRBD resyncing, a node being fenced.

The fix is a dedicated board you bring up and watch: a **drill cockpit** for the
Pacemaker/SAN arm across both DR sites. Its primary job is *live observation of state
transitions* — not static config readout — so a transition must become visible
within a scrape interval, and the design favours signals that move during a drill.

This is explicitly the **infrastructure view** of the cluster. Its sibling — a future,
separate board tracing the **application** message flow (app → QM → WAN → remote
service → back) — is out of scope here; these deep-dive boards are a family, and this
one owns the infrastructure layer.

## 2. Goals & non-goals

**Goals**

- A dedicated Grafana board, **PCMK Cluster · Infrastructure View**, for the
  Pacemaker/SAN arm, **both DR sites visible together**.
- A **node × component matrix** (§3.1) that shows per-node component state, the
  cluster-as-a-unit summary, and the cross-site DRBD replication/RPO tail in one read,
  and reads cleanly *mid-transition*.
- **Fail-loud** throughout: a silent collector renders STALE, never a faked green.
- New telemetry confined to **one** node_exporter textfile collector on the existing
  `node` job; perf and network sections ride **existing** metrics.
- The main "Lab — Layered Status" dashboard stays the highlight; this board is reached
  by a **drill-link** from a compact per-side roll-up that replaces the binary
  PCMK-A/B tiles.
- Everything generated from code (`render_dashboard(topo)`); pure functions tested to
  the repo's 100% branch bar; `vrg-validate` the only gate.

**Non-goals**

- **The RDQM arm.** Its internals differ (`rdqmstatus` vs `pcs`/`crm`/`drbdadm`); it
  is deliberately deferred. The board shell is kept **arm-agnostic** so an RDQM twin
  slots in later as a sibling board — but it is not built here.
- **The application message-flow board.** A separate, future sibling (§1).
- **The MQ-service objects** (QM/channel/queue depth) — owned by the MQ Service panel
  (`2026-06-12`), not duplicated here. QM message-rate stays out of §3.
- **Acting on the cluster from the board.** This is observation; control stays in
  `mqlab`/`pcs`/the cutover script.
- **A graphical node-link topology diagram.** The matrix is the representation.

## 3. The board — four sections

Read top-to-bottom, most important first, network foundation last — mirroring the
main page's own philosophy. Authoritative mockup:
`docs/specs/diagrams/cluster-drill-cockpit-board-layout.html`.

| # | Section | v1? | Telemetry |
|---|---------|-----|-----------|
| 1 | **Cluster-state matrix** | ✅ build now | **New** cluster-state collector (§4) |
| 2 | **Critical log stream** | ⏳ designed-in slot | Lands with **#143** |
| 3 | **System perf** (OS + storage) | ✅ build now | **Existing** node/host metrics (§6) |
| 4 | **Network foundation** | ✅ build now | **Existing** `lab_network_*` + virbr rx/tx (§6) |

### 3.1 The cluster-state matrix (§1)

Rows = nodes; columns = the relevant components, **grouped by HA layer**:

- **membership** — `corosync` (ring), `pacemaker` (online/standby)
- **storage** — `DRBD` (role · disk state), `iSCSI` (sessions/paths)
- **resource group** — `mq_fs`, `mq_vip`, `mq_vip_ext`, `mq_qm` (placement + state)
- **fence** — STONITH state / recent fence

Node rows are grouped into **SITE A** (`san-a`, `pcmk-a1..3`) and **SITE B**
(`san-b`, `pcmk-b1..3`). SAN nodes are storage, not cluster members, so their
membership/resource-group cells are not-applicable (`—`); DRBD cells are populated
**only** on SAN rows (DRBD replicates the SAN LUN site-to-site).

Above the grid, a **cluster-as-a-unit summary bar**: per side, quorum + QM owner +
active/standby; in the centre, the **cross-site DRBD** direction, resync %, and
out-of-sync tail (the RPO proxy). A **replication band** stitched between the
`san-a` and `san-b` rows repeats the live DRBD relationship inline.

`★` marks the current QM owner. Colour vocabulary: **green** healthy · **amber**
transitional/resyncing · **red** down/failed · **grey** standby/n-a · **hatched
STALE** when a collector is silent (§9). The mid-cutover mockup snapshot is the
acceptance picture: a fenced node red with daemon STALE, quorum dipped, DRBD flipped,
the resource group relighting amber→green on the target, the RPO tail a live number.

## 4. Section 1 telemetry — the cluster-state collector

A new collector following the **established textfile-collector pattern**
(`host-net-state`, `net-reach`): a tested `mqlab obs cluster-state` verb, invoked by a
per-node **systemd timer**, writes `node_exporter` textfiles to the existing
`--collector.textfile.directory`. **No new Prometheus job** — the metrics ride the
existing `node` job. Collector logic lives in the `mqlab obs` Python CLI (unit-tested,
glass-box), not a fat shell script, consistent with the `net-state`/`reach-peers`
verbs and the repo's ansible/CLI-over-shell convention.

### 4.1 Signals by node role

**On cluster nodes** (`pcmk-a1..3`, `pcmk-b1..3`):

| Signal | Source | Metric (sketch) |
|---|---|---|
| Quorum | `crm_mon --as-xml` / `corosync-quorumtool` | `cluster_quorate{site}` 0/1 |
| Node online/standby | `crm_mon --as-xml` | `cluster_node_online{node}` 0/1, `…_standby` |
| Corosync ring | `corosync-cfgtool -s` | `cluster_corosync_ring_ok{node}` 0/1 |
| Resource placement | `crm_mon --as-xml` | `cluster_resource_location{resource,node}` 0/1, `cluster_resource_state{resource}` enum |
| Fence / STONITH | `stonith_admin --history '*'` | `cluster_fence_event{node}`, `cluster_stonith_ok{node}` |
| iSCSI / multipath | `iscsiadm -m session`, `multipath -ll` | `cluster_iscsi_paths{node}` n/total |
| Daemon liveness | `systemctl is-active` | `cluster_daemon_up{node,unit}` 0/1 |

**On SAN nodes** (`san-a`, `san-b`):

| Signal | Source | Metric (sketch) |
|---|---|---|
| DRBD role | `drbdadm status` (JSON) | `cluster_drbd_role{node,resource}` enum |
| DRBD disk state | `drbdadm status` | `cluster_drbd_disk{node,resource}` enum (UpToDate/Outdated/Inconsistent/…) |
| DRBD connection | `drbdadm status` | `cluster_drbd_conn{node,resource}` enum (Connected/WFConnection/StandAlone/…) |
| Resync progress | `drbdadm status` | `cluster_drbd_resync_pct{resource}` 0–100 |
| RPO tail | `drbdadm status` | `cluster_drbd_out_of_sync_bytes{resource}` |

Exact metric names, enum encodings, and which `drbdadm`/`crm_mon` fields are stable
are **finalised in the rendering spike** (§5) — both are read together so the panel
and the collector agree from day one.

### 4.2 Cluster-wide facts and fail-loud

Quorum, resource placement, and fence history are **cluster-wide** — any live cluster
node reports the same global view via `crm_mon --as-xml`. The collector emits them
from **every** cluster node (labelled by the reporting `instance`); the dashboard reads
`max by (resource, node)(…)`. That redundancy **is** the fail-loud design: if the
node that happened to report dies, a peer still reports, so the panel never goes blank
mid-drill. Each collector run stamps `cluster_state_last_write_timestamp{node}`; a
sample older than a small multiple of the timer interval renders **STALE** (hatched),
distinct from both green and red. A collector that cannot run (permissions, crash,
fenced node) must read stale, never healthy — the silently-blind tile is exactly the
lie the repo's fail-loud rule forbids.

## 5. Section 1 rendering — spike-decided

The matrix (coloured cells, grouped headers, inline replication band, summary bar, ★
owner) lands differently across Grafana panel types and versions. Rather than commit
blind, a **short spike** builds it two ways against real collector metrics in the
lab's actual Grafana and picks on how they look:

- **Table panel** — one query → cell-background colour mappings. Lightest, most
  robust, fully stock. Grouped headers faked via column naming (e.g. `mem·corosync`);
  the replication band + summary become adjacent stacked panels.
- **Canvas panel** — reproduces the mockup faithfully (grouped headers, inline band,
  summary, ★), each element's colour bound to a metric. Best looking; most authoring
  effort and the verbose JSON to maintain.

Either way the panel is **generated by extending `render_dashboard(topo)`**, so the
per-node/per-column projection is code, not hand-edited JSON, and is unit-tested. The
board is a **dedicated dashboard with a stable `uid`** (`lab-pcmk-cluster`, hard-linked
from the overview roll-up and `mqlab obs open`-style deep-links). The spike's output:
the chosen mechanism, the confirmed `drbdadm`/`crm_mon` field set, and the metric
schema (§4.1) frozen against it.

## 6. Sections 3 & 4 — perf and network from existing metrics

Neither needs new telemetry.

- **§3 System perf (OS + storage):** per-node **CPU busy%** (the cluster nodes;
  `node_cpu_seconds_total`, same expression as the main page), **SAN disk I/O**
  (`node_disk_*` on `san-a`/`san-b`), **DRBD replication throughput** (the `net-wan`
  bridge rx/tx the host node_exporter already exports — watch resync bandwidth and the
  tail drain). QM message-rate is deliberately excluded (MQ-Service panel's job).
- **§4 Network foundation:** per-plane active/reachable state in the **same tri-state
  vocabulary as the main page's network layer** (`lab_network_health`) plus throughput
  (virbr rx/tx), **scoped to the planes this cluster rides** — heartbeat
  (`net-hb-a/b`, ★ — its loss drives a fence), SAN (`net-san-a/b`), WAN (`net-wan`,
  the DRBD replication path), data (`net-data-a/b`).

## 7. Overview integration — roll-up + drill-link

The main page's VM layer **drops the binary PCMK-A/B group tiles** and replaces them
with a **compact per-side roll-up**: one tile per site (green/amber/red) folding the
matrix's worst current cell (e.g. any STALE/red component, or quorum loss, reddens the
side). Each tile **drill-links** to the dedicated board. This keeps the highlight
overview scannable while giving the cockpit its own real estate, and it is the concrete
form of "replace the VM tiles." The fold is a pure function (matrix cells → one
tri-state), unit-tested.

## 8. Build split

Each is its own plan and PR.

- **Plan 1 — Matrix + collector** *(the core)*: the `cluster-state` collector role +
  `mqlab obs cluster-state` verb + per-node timers; the rendering spike (§5) and the
  resulting matrix panel; the overview roll-up + drill-link (§7). Delivers the live
  cluster picture.
- **Plan 2 — Perf + network sections** *(existing metrics)*: §3 and §4 on the dedicated
  board. Independent of the collector; can land in parallel once the board exists.
- **Plan 3 — Critical log stream** *(after #143)*: fill the §2 slot once log-streaming
  lands and rebases on this. Transport (Loki / Grafana logs panel), source/severity
  filter, and auto-scroll are that plan's detail.

## 9. Cross-cutting concerns

- **Fail-loud.** STALE on a silent collector; redundant cluster-wide reporting (§4.2);
  a fenced/dead node reads red + STALE, never green. The amber DRBD state during resync
  is the loud "not yet consistent" signal.
- **No new Prometheus jobs / no new secrets.** Pure additive textfile metrics on the
  existing `node` job; cluster tooling is read-only (`crm_mon`, `drbdadm status`,
  `stonith_admin --history`), no privileged mutation.
- **Testing.** The pure functions — `crm_mon`/`drbdadm` parsing → metrics, the
  roll-up fold, the `render_dashboard` matrix projection — are unit-tested to the 100%
  branch bar. Collector scripts validated live in a cold rebuild; dashboard JSON
  lint-validated. `vrg-container-run -- vrg-validate` is the only gate.
- **Cold-rebuild acceptance.** Per the repo's cold-rebuild gate, the collector +
  board are accepted only after a full VM cold rebuild proves them one-pass and a real
  failover/cutover drives the matrix through the mid-cutover picture.

## 10. Open questions

- **`crm_mon --as-xml` vs `pcs status xml` stability** across the Alma/RHEL cluster
  stack — settle in the spike; pick the most stable structured source and pin the
  field set.
- **DRBD endpoint of record.** Confirm `drbdadm status` on the SAN nodes exposes
  resync % and out-of-sync bytes in the form the RPO tile needs, and that promotion
  state during a cutover is legible there (vs. only via the cutover script).
- **Resource-state enum encoding.** How to encode `Started/Stopped/Starting/FAILED`
  per resource as a colourable metric — numeric enum vs. info-metric + value mapping;
  decided with the rendering mechanism.
- **Roll-up fold severity order.** The exact precedence (STALE vs red vs amber) the
  overview tile uses — trivial, but make it explicit so the overview and board never
  disagree.
- **STALE threshold.** Timer interval vs. staleness window, tuned so a real transition
  shows within a scrape interval without flapping STALE.

## 11. Success criteria

1. The dedicated **PCMK Cluster · Infrastructure View** board renders the four-section
   stack; the matrix shows both DR sites with per-node component state, the
   cluster-unit summary, and the cross-site DRBD replication/RPO tail.
2. Mid-drill the board tells the story with no log line read: a **fenced node** reddens
   with its daemon **STALE**, **quorum** dips, **DRBD flips Primary and resync % climbs**,
   the **resource group relights** amber→green on the target node, and the **RPO tail is
   a measured number** — matching the mid-cutover mockup.
3. A silent/blind collector renders **STALE**, never green; killing the reporting node
   does not blank the cluster-wide tiles (a peer still reports).
4. The overview's binary PCMK-A/B tiles are replaced by a per-side **roll-up** that
   folds the matrix and **drill-links** to the board; the overview stays scannable.
5. §3/§4 use only existing metrics (no new scrape job); only §1 adds the cluster-state
   collector.
6. The whole board + collector are reproducible from a **cold rebuild** and pass
   `vrg-validate`; the RDQM twin can be added later without reshaping the board.
