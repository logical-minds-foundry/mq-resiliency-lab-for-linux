# Native HA Cluster Cockpit — Design

**Date:** 2026-06-18
**Issue:** #279
**Builds on:** `docs/specs/2026-06-18-cluster-cockpit-canvas-rebuild-design.md` — the
arm-agnostic cockpit framework (`src/mqlab/clusterboard.py`) and its **§3.2 arm-extensibility
contract**. This is the second arm (PCMK was the first); it adds **no new builders**.

---

## 1. Goal & scope

A code-generated `lab-nativeha-cluster` Grafana board that makes IBM MQ **Native HA** legible —
MQ's *built-in* raft HA with no Pacemaker/DRBD/iSCSI plumbing — plus its **CRR** cross-region
layer. Target arm: `nativeha-rhel`, the `distributed-nativeha-rhel` 3+3 keystone (#267):
`nha-rhel-a1/2/3` (site A, *Live* HA group `nha_rhel_a`) + `nha-rhel-b1/2/3` (site B, *Recovery*
CRR group `nha_rhel_b`), QM = **QMNATIVE**, RHEL 9.6 (x86_64, TCG).

**In scope:** the full 3+3 + CRR from the start. **Reuse** every `clusterboard.py` builder
(`matrix`, `hero_tiles`, `integrity_panel`, `timeline_band`, `log_row`, `perf_section`,
`net_section`, `fold_side`, `active_side`). **New** work is only a state collector + per-arm
input tables, exactly as §3.2 prescribes.

**Out of scope:** the RDQM cockpit (next, separate spec); changes to the PCMK board or the
builders themselves (they're already arm-agnostic).

## 2. Dependency

This depends on **#246** (the `nativeha-rhel` arm) for: the `topology.yaml` arm/setup/groups,
the `observability.yml` wiring target, and the **live 3+3+CRR cluster** for verification. The
collector + board *code* can be written against the known `dspmq -o nativeha` format now; the
**topology wiring + live acceptance** land once #246 is merged (build on develop after it
merges, or branch off #246). The live site-A 3+3 is already up for format capture + verification.

## 3. Architecture (mirrors the PCMK collector→board pattern)

- **`src/mqlab/nativehastate.py`** — a stdlib-only collector deployed verbatim to the nha nodes
  (like `clusterstate.py`), run by a 5s systemd timer. It probes:
  - `dspmq -m QMNATIVE -o nativeha -x` — per-instance HA state, and
  - `dspmq -m QMNATIVE -o nativeha -g` — the cross-region (CRR) group view.

  Pure parse functions turn the output into rows; a render function projects them to
  node_exporter textfile lines. Bounded/non-blocking probes; a silent/timed-out probe →
  no fresh sample → the cell reads **STALE** (the §4.3/§9 fail-loud discipline, verbatim
  from the PCMK collector).
- **`ansible/roles/nativeha-state/`** — mirrors `cluster-state`: deploys the collector +
  the systemd service/timer. `ansible/observability.yml` adds `nha_rhel_a`/`nha_rhel_b` to
  the collector's `when` clause (a `nativeha` role variant, alongside the PCMK `cluster`/
  `storage` roles).
- **`src/mqlab/clusterboard.py`** — add Native-HA input tables (rows/columns/Loki selector/
  metric exprs) and a `render_cluster_dashboard(topo, arm="nativeha-rhel")` path that emits
  the `lab-nativeha-cluster` board from the existing builders.

## 4. Metric mapping (`dspmq` → the §3.2 `cluster_*` shape)

The collector emits the same metric *shape* as PCMK so the builders and the overview
roll-up work unchanged; Native-HA-specific facets get `cluster_nha_*` names.

| `dspmq` field | Metric(s) | Drives |
|---|---|---|
| `QUORUM(x/3)` | `cluster_quorate{node}` (1 if x ≥ 2) · `cluster_node_online{node,member}` (per instance) | quorum hero · ② instance rows |
| `ROLE(Active\|Replica\|Unknown)` | `cluster_nha_role{node,member,role}` · `cluster_resource_owner{resource="QMNATIVE",holder=<Active>}` | active-instance hero · ★ active row · timeline |
| `INSYNC(yes\|no)` | `cluster_nha_insync{node,member}` (1\|0) | replication signal (the out-of-sync analog) · ② · timeline |
| `HASTATUS` | `cluster_nha_hastatus{node,member,status}` | ② health |
| `GRPROLE(Live\|Recovery)` | `cluster_nha_group_role{node,group,role}` | ③ CRR card |
| `CONNGRP(yes\|no)` | `cluster_nha_connected{node}` (1\|0) | ③ CRR card · timeline |
| (staleness) | `cluster_state_last_write_timestamp{node,source}` | STALE precedence |

`active_side(holder)` and `fold_side(cells)` (the shared §6.8 state machines) work as-is:
`active_side` over `cluster_resource_owner{resource="QMNATIVE"}` gives the active *instance*;
`fold_side` over the instance health cells feeds the overview roll-up.

## 5. Board layout — `lab-nativeha-cluster`

- **Title banner:** "Native HA · MQ raft HA + CRR cross-region · RHEL 9.6 (x86_64)".
- **① Cluster status (hero + integrity):** Active instance · Quorum (x/3) · In-sync replicas
  (n/3) · HA status; the first-class **integrity** light (§7).
- **② Instances matrix** (`matrix()`): rows `nha-rhel-a1..3` + `b1..3`, **banded by group**
  (Live A / Recovery B); columns **role · in-sync · HA-status · quorum-member**. No
  corosync/pacemaker/iSCSI/DRBD/fence columns — Native HA has none.
- **③ Cross-region (CRR) card:** per-group GRPROLE (Live ↔ Recovery), CONNGRP (connected),
  group in-sync — the DR layer (the `-g` data).
- **Failover timeline** (`timeline_band()`): active instance · quorum · in-sync · CRR-connected,
  with the always-on QM-failover annotation — the re-election + cross-region story.
- **Logs** (`log_row()`): the QMNATIVE / `mqmonitor@`/AMQERR units (the Loki `unit=~` selector
  retargeted off corosync/pacemaker), with the `$level` severity toggle.
- **Perf/network** (`perf_section`/`net_section`): CPU + **net-hb** (intra-site raft
  replication) + **net-wan** (CRR cross-region) throughput.
- **Overview roll-up + drill-link** on the main `lab-fleet-node` board: a per-group Native-HA
  tile (fold of the instances) drill-linking to `lab-nativeha-cluster`.

## 6. The conceptual change from PCMK — integrity

Native HA **cannot split-brain** (raft quorum forbids two Actives), so the integrity light is
*not* about dual-primary/corruption. It reframes around **availability + durability** and goes
loud on:
- **Quorum lost** — `QUORUM < 2/3`: no Active is electable → outage.
- **No Active** — `ROLE Unknown` across the group (e.g. an isolated minority refusing to lead).
- **Replica not in-sync** — `INSYNC(no)` on a replica: writes the Active has acked that a
  lagging replica is missing → data-at-risk if that replica is promoted.

No-data still reads **STALE** (never green), per fail-loud.

## 7. Testing & acceptance

- **Unit (100% branch):** the collector's `dspmq` parse functions (each field/state, incl.
  `Unknown`/`no`/missing) and the render projection; the Native-HA input tables and the
  `arm="nativeha-rhel"` render path produce the expected board (uid, panels, columns) — render
  tests mirroring the PCMK ones. The fixture is **captured from real `dspmq -o nativeha -x`/`-g`
  output** on a live nha node (as the DRBD fixture was for #272).
- **JSON validity:** the rendered board parses + has the expected panel set + uid.
- **Cold-rebuild acceptance (load-bearing):** a full rebuild of the `distributed-nativeha-rhel`
  cluster + a **real Native-HA failover** (power-off the Active instance) must drive the matrix
  (Active jumps to a survivor, quorum dips to 2/3, the downed node rejoins as Replica with
  in-sync catching up) and the timeline must record it; the integrity light stays clean
  (quorum held) — and a CRR check shows GRPROLE/CONNGRP across the two groups.
- `vrg-container-run -- vrg-validate` is the only gate.

## 8. Build split (each its own plan/PR, ordered)

1. **Collector** — `nativehastate.py` (parse + render) + `nativeha-state` role +
   `observability.yml` wiring. Headless-verifiable (`promtool`/textfile) + a captured fixture.
2. **Board** — Native-HA input tables + the `arm="nativeha-rhel"` render path → instances
   matrix + hero + integrity (the §6 reframing).
3. **CRR card + timeline CRR signal** — the `-g` group view (GRPROLE/CONNGRP), reading the
   #246 CRR code for the exact fields.
4. **Logs + perf/network** — retargeted Loki selector; CPU + net-hb/net-wan.
5. **Overview roll-up + drill-link** on `lab-fleet-node`.

## 9. Success criteria

1. `lab-nativeha-cluster` renders the full stack from `cluster_*`/`cluster_nha_*` metrics,
   generated by `render_cluster_dashboard(topo, arm="nativeha-rhel")`, provisioned from a
   versioned file — **no new builders**.
2. Mid-failover the board tells the story with no log line read: Active re-elects to a
   survivor, quorum shows 2/3, the recovered node rejoins as in-sync Replica; integrity stays
   green (quorum held), loud only on real quorum-loss / no-Active / not-in-sync.
3. The CRR card shows Live ↔ Recovery group roles + connected state.
4. Reuses the framework: the only Native-HA-specific code is the collector + the input tables.
5. Survives a cold rebuild + a real failover drill.
