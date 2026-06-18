# Cockpit matrix — metric→cell recipe (frozen build contract, #219)

The engine-independent contract: which `cluster_*` series + label drives each cell, and
the value→colour mapping. The eventual `matrix()` builder implements *this*. Grounded in
the exact series `render_cluster_state_prom` emits (`src/mqlab/clusterstate.py`) and
**verified live** against the provisioned `pcmk_san_dr` cluster during the engine spike
(2026-06-18). The chosen engine is **Table** (design spec §4 spike outcome); the proven
panel is frozen at `docs/specs/diagrams/cluster-matrix-table-contract.json`.

## The one insight that makes the matrix code-generatable

Every series carries a node name, but in **different labels** depending on the fact's
nature:

- **Self-facts** (the reporter is the subject): `node` label IS the row.
  `cluster_daemon_up{node,unit}`, `cluster_iscsi_sessions{node}`, `cluster_drbd_*{node,resource}`.
- **Cluster-wide facts** (redundantly reported by every node; the subject is a *different*
  label): the row is `member` or `holder`, and `node` is just the reporter.
  `cluster_node_online{node,member}`, `cluster_node_unclean{node,member}`,
  `cluster_fence_count{node,member}`, `cluster_resource_owner{node,resource,holder}`.

**Normalize the row key.** For each column query, `label_replace(...)` the node-name label
(`node` | `member` | `holder`) → a common `n`, and collapse redundant reporters with
`max by (n, …)`. Then rows = `n`, each column = one query. This normalization is the
mechanical core `matrix(rows, cols, metric_map)` emits — identical for Table or Canvas.

## Section ② Compute — rows = pcmk-a1..3, pcmk-b1..3 (banded by site)

| Column | Query (→ value per row `n`) | value → colour |
|---|---|---|
| corosync | `max by (n)(label_replace(cluster_daemon_up{unit="corosync"}, "n","$1","node","(.*)"))` | 1 → green · 0 → red · no-data → **STALE** |
| pacemaker | same with `unit="pacemaker"` | 1 → green · 0 → red · no-data → STALE |
| iSCSI | `max by (n)(label_replace(cluster_iscsi_sessions, "n","$1","node","(.*)"))` | ≥1 → green · 0 → red · no-data → STALE |
| fence | `max by (n)(label_replace(cluster_fence_count, "n","$1","member","(.*)"))` | 0 → green · ≥1 → **red (fenced)** |
| online | `max by (n)(label_replace(cluster_node_online, "n","$1","member","(.*)"))` | 1 → green · 0 → red (offline) |
| unclean | `max by (n)(label_replace(cluster_node_unclean, "n","$1","member","(.*)"))` | 0 → (ok) · 1 → red (unclean) |
| mq_fs | `max by (n)(label_replace(cluster_resource_owner{resource="mq_fs"}, "n","$1","holder","(.*)"))` | 1 → green ● (owner) · absent → grey ○ |
| mq_vip | same, `resource="mq_vip"` | 1 → green ● · absent → grey ○ |
| mq_vip_ext | same, `resource="mq_vip_ext"` | 1 → green ● · absent → grey ○ |
| mq_qm | same, `resource="mq_qm"` | 1 → green ● · absent → grey ○ |

- **★ owner row:** the row where the four `mq_*` cells light is the active node. The four
  lining up on one row is the "who's active" read; a DR cutover = those four jumping from a
  Site-A row to a Site-B row.
- **online/unclean/fence fold into the node's overall health** for the row label tint.

## Section ③ Storage — rows = san-a, san-b

| Column | Query | value → colour |
|---|---|---|
| role | `cluster_drbd_role{node=~"san-.*"}` (label `role` = Primary/Secondary) | Primary → blue · Secondary → grey |
| disk | `cluster_drbd_disk` (label `disk`) | UpToDate → green · Outdated → amber · Inconsistent/Diskless → **red** |
| conn | `cluster_drbd_conn` (label `conn`) | Connected/SyncSource/SyncTarget → green/amber · **StandAlone/WFConnection → red (split-brain hazard)** |
| resync % | `cluster_drbd_resync_pct` | 100 → green · <100 → amber (resyncing) |
| out-of-sync | `cluster_drbd_out_of_sync_bytes` | 0 → green (in-sync) · >0 → amber (backlog, shown in bytes) |
| iSCSI target | `cluster_iscsi_sessions{node=~"san-.*"}` | ≥1 → green · 0 → red |
| drbd svc | `cluster_daemon_up{unit="drbd"}` | 1 → green · 0 → red |

## Section ① Cluster status (per-site cards + cross-site DRBD card)

- **Quorum (n/3):** `sum by (site)(max by (n,site)(label_replace(cluster_node_online,…)))` —
  needs a `site` derivation from the node name (a → Site A, b → Site B). Quorate when ≥2/3.
- **Active site:** which site holds the `mq_*` owners (`active_side`, §6.8).
- **Cross-site DRBD card:** direction (which san is Primary), `resync_pct`, `out_of_sync_bytes`.
- **Integrity light:** trips on `conn` StandAlone / dual-Primary `role` / `disk` Diskless /
  Outdated-being-promoted. Distinct loud treatment, not a cell.

## STALE (fail-loud)

`now - cluster_state_last_write_timestamp{node,source} > ~3×tick` → that node/source is
silent → the affected cells render **hatched STALE** (distinct from red). Precedence
STALE > red > amber > green (§6.8 / 2026-06-14 §4.3).

## Hero tiles (stock Stat)

- Cluster health = `fold_side` over both sides → green/amber/red/STALE.
- Replication = `max(cluster_drbd_out_of_sync_bytes)` (bytes) + `min(cluster_drbd_resync_pct)`.
- Active site = `active_side`.
- Quorum = the per-site quorum expr above.

## Engine implications for the spike judgment

- **Table:** the normalized per-column queries above are literally Table's natural shape —
  one query per column, `Merge`/`Labels to fields` transform pivots to rows×columns, cell
  colour via per-field value mappings. The recipe maps 1:1 onto Table with no contortion.
- **Canvas:** needs ~48 cell elements, each bound to one of these queries' results. The
  recipe is still the source, but each cell becomes a placed element — assess whether that
  binding is a clean projection (the §8.1 hard gate) or bespoke hand-placement.
