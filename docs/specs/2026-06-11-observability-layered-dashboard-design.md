# Observability Dashboard — Layered, Lab-Shaped View + Network Status — Design

- **Date:** 2026-06-11
- **Status:** Design — brainstormed 2026-06-11
- **Issue:** #108
- **Builds on #103 / #106** (observability foundation: `obs` + `mon-probe`,
  Prometheus + Grafana, `node_exporter` fleet-wide, `render_scrape_targets`) and
  **#104** (topology groups) / **#107** (the `mqlab net` lifecycle that mutates
  the libvirt network state this design observes).
- **Relationship:** evolves the Plan-A `Fleet — Node Health` dashboard into a
  layered operator view and adds a network-status layer. The reserved top
  (MQ-service) row is filled by **Layer 2 / Plan B**, out of scope here.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. Architecture — telemetry](#3-architecture--telemetry)
  - [3.1 Three sources, one `node` job](#31-three-sources-one-node-job)
  - [3.2 Network state — host collector](#32-network-state--host-collector)
  - [3.3 Network reachability — per-node peer-ping](#33-network-reachability--per-node-peer-ping)
  - [3.4 The host as a scrape target](#34-the-host-as-a-scrape-target)
- [4. The dashboard layout](#4-the-dashboard-layout)
- [5. Provisioning — all as code](#5-provisioning--all-as-code)
- [6. Build split — one spec, two plans](#6-build-split--one-spec-two-plans)
- [7. Cross-cutting concerns](#7-cross-cutting-concerns)
- [8. Open questions](#8-open-questions)
- [9. Success criteria](#9-success-criteria)

---

## 1. Problem & motivation

The Plan-A dashboard proved the stack works, but it is "quick and dirty": a flat,
roughly-alphabetical list of node-up tiles plus a couple of per-host CPU graphs.
Two problems:

- **It doesn't reflect the lab's shape.** The fleet has real structure — paired
  HA arms (PCMK A/B, RDQM A/B), their SAN nodes, a standalone message path, the
  observability pair — and a flat list hides it. An operator can't see at a
  glance "is site-A's PCMK arm healthy."
- **It doesn't show the networks.** Much of the testing *is* network
  manipulation — `mqlab net down net-hb-a` to break a heartbeat, severing data
  or WAN links during fault/DR drills. The dashboard is blind to the very thing
  being acted on.
- **Per-host CPU graphs don't scale.** Two hosts today; nineteen soon. One graph
  per host is unreadable at fleet scale.

The fix is to make the dashboard **lab-shaped and layered**: group the VMs the
way the lab is actually organized (which also rescues CPU — one graph *per
group* is a useful "who's busy" read), and add a **network-status layer** so the
plane being manipulated is visible. Read top-to-bottom, the operator sees the
most important thing first (MQ service, when it lands), then the VMs, then the
network foundation.

## 2. Goals & non-goals

**Goals**

- A **layered** dashboard: MQ-service (reserved) / VMs / networks, top to bottom.
- VMs **grouped** by the topology `groups` namespace, in a curated,
  lab-structured order; each group row shows member up/down tiles **and** a
  single per-group CPU-busy% graph.
- A **network-status layer**: a tile per libvirt network combining **libvirt
  active-state** and **reachability** (active+reachable=green,
  active-but-unreachable=amber, inactive=red).
- Everything provisioned as code; fail-loud; no new Prometheus jobs.

**Non-goals**

- **The MQ-service row** (top) — reserved; built in Layer 2 / Plan B.
- **Per-VM performance depth.** These are VMs; CPU is a rough "who's busy"
  indicator, not a tuning surface. No memory/disk/IO drill-downs.
- **A graphical topology diagram.** Grouping into logical rows is enough; no
  node-link graph.
- **Splitting the network strip into per-site sub-groups.** A flat tile strip is
  the v1; sub-grouping is a trivial later tweak if it feels cramped.

## 3. Architecture — telemetry

### 3.1 Three sources, one `node` job

All metrics reach Prometheus through the **existing `node` scrape job** — no new
jobs, no new exporters. The two new metric families ride `node_exporter`'s
**textfile collector**:

| Signal | Metric | Carrier |
|---|---|---|
| VM up/down | `up{job="node"}` | guest `node_exporter` (have it) |
| VM CPU | `node_cpu_seconds_total` | guest `node_exporter` (have it) |
| Network state | `lab_network_active{network}` 0/1 | **host** `node_exporter` textfile |
| Network reachability | `lab_net_reach{network,peer}` 0/1 | **each guest** `node_exporter` textfile |

### 3.2 Network state — host collector

Libvirt networks are host-side objects; no guest can see them. On the **Vergil
VM** (the libvirt host, where `mqlab net` and `virsh` already run), a systemd
timer runs `virsh net-list --all` and writes a `node_exporter` textfile
exposing `lab_network_active{network="net-hb-a"} 0/1` (1 = libvirt-active). This
is authoritative and matches the test action exactly: `mqlab net down net-hb-a`
→ `virsh net-destroy` → inactive → tile flips.

### 3.3 Network reachability — per-node peer-ping

State alone misses "active in libvirt but traffic isn't flowing" (a fenced node,
a NIC/firewall fault, a partial partition). So each guest runs a textfile timer
that pings its **same-network peers** and writes
`lab_net_reach{network="net-hb-a",peer="pcmk-a2"} 0/1`. The peer list per node
per network is **derived from topology** (a pure function: hosts sharing a
network, minus self). This reuses `node_exporter` — no new target — and surfaces
a heartbeat break or a fenced peer directly as "pcmk-a1 can't reach pcmk-a2 on
hb-a."

### 3.4 The host as a scrape target

The Vergil VM becomes the **first non-guest scrape target**, reachable on the
host-only `net-mgmt` plane at **`10.50.0.1:9100`**. `render_scrape_targets` gains
a synthetic entry for it (label `host="hypervisor"`, `groups="hypervisor"`) —
the only change to the renderer. Both new metric families flow through this and
the existing guest targets on the unchanged `node` job.

## 4. The dashboard layout

Top-to-bottom, so the operator's eye lands on the most important layer first:

```
╔═ Lab — Layered Status ═══════════════════════════════════════════════════╗
║ ▌MQ SERVICE                                          (reserved — Plan B/L2)║
║   ┌────────────────────────────────────────────────────────────────────┐ ║
║   │  QM owner · queue depth · channel status  →  lands in Layer 2       │ ║
║   └────────────────────────────────────────────────────────────────────┘ ║
╟──────────────────────────────────────────────────────────────────────────╢
║ ▌VMs  (one row per group: up/down tiles  +  that group's CPU)            ║
║  SAN     │ san-a │ san-b │            CPU% │ san-a, san-b                 ║
║  PCMK-A  │ a1 │ a2 │ a3 │            CPU% │ a1, a2, a3 (3 lines)          ║
║  PCMK-B  │ b1 │ b2 │ b3 │            CPU% │ …                            ║
║  RDQM-A  │ a1 │ a2 │ a3 │            CPU% │ …                            ║
║  RDQM-B  │ b1 │ b2 │ b3 │            CPU% │ …                            ║
║  STDALONE│ qm-main │ dtcc-sim │ app-client │   CPU% │ …                  ║
║  OBS     │ obs │ mon-probe │                   CPU% │ …                  ║
╟──────────────────────────────────────────────────────────────────────────╢
║ ▌NETWORKS  (green=active+reachable, amber=active-not-reachable, red=down) ║
║  data-a│data-b│ hb-a │ hb-b │san-a│san-b│ wan │client│ dtcc │ mgmt        ║
╚══════════════════════════════════════════════════════════════════════════╝
```

- **MQ service (top):** a reserved row with a placeholder note; Plan B fills it.
- **VMs (middle):** one **row per group**, each row = member up/down stat tiles
  (legend `{{host}}`) **+** a single CPU-busy% timeseries scoped to that group's
  members. Order is **curated** to read like the lab — SAN → PCMK A/B → RDQM A/B
  → standalone → observability — rather than alphabetical. Curated order lives in
  the dashboard JSON; adding a *new group* (rare) needs a JSON touch-up.
  CPU-busy% = `100 - avg by (host)(rate(node_cpu_seconds_total{mode="idle"}[1m]))*100`,
  filtered to the row's group.
- **Networks (bottom):** a flat tile strip, one tile per libvirt network. Tile
  color rolls up both signals — `inactive`→red, `active & any peer unreachable`
  →amber, `active & all reachable`→green.

## 5. Provisioning — all as code

- **Host collector** (§3.2): a **local Ansible play** (`connection: local`) that
  installs `node_exporter` + the `virsh net-list` textfile timer on the Vergil
  VM. `mqlab obs up` gains a step to run it (the host is part of the observability
  fabric, brought up with the pair). Kept out of `vergil.toml` so the dev-VM
  profile stays about the toolchain, not lab services.
- **Reachability collector** (§3.3): the `observability.yml` overlay gains the
  per-node peer-ping textfile timer; peer lists rendered from topology.
- **Dashboard:** the `grafana` role's `fleet-node.json` is replaced by the
  layered design (renamed e.g. `lab-status.json`, uid retained or redirected).

## 6. Build split — one spec, two plans

- **Tweak 1 — Dashboard restructure** *(uses only existing metrics)*: the layered
  shell (reserved MQ row / grouped VM rows / **placeholder** network strip),
  per-group up/down tiles + per-group CPU graphs, curated order. Independent and
  fast — no new telemetry. Delivers the grouped, lab-shaped view immediately.
- **Tweak 2 — Network telemetry + live panel**: the host net-state collector, the
  per-node reachability collector, the `render_scrape_targets` host target, and
  wiring the bottom strip to real `lab_network_active` / `lab_net_reach` data.

Tweak 1 stands alone; Tweak 2 lights up the network strip. Each is its own plan
and PR.

## 7. Cross-cutting concerns

- **Fail-loud.** Each textfile collector emits a `*_last_write_timestamp`; the
  dashboard flags stale data rather than showing a confident-but-stale tile. A
  dead host collector shows as the hypervisor target `up==0`. The amber network
  tile *is* the loud signal for "active but not passing traffic."
- **No new secrets / no new Prometheus jobs.** Pure additive textfile metrics on
  the existing job.
- **Testing.** Pure functions — the topology→peer-list derivation and the
  `render_scrape_targets` host target — are unit-tested to the repo's 100%
  branch-coverage bar. Collector scripts validated live; dashboard JSON
  lint-validated. `vrg-container-run -- vrg-validate` is the only gate.

## 8. Open questions

- **Curated-order maintenance.** The group-row order is hand-curated in the
  dashboard JSON. If group churn becomes common, revisit a topology-ordered
  template variable; for now groups are stable, so curation wins on clarity.
- **`mqlab net` peer semantics under #107.** Confirm the host collector reads the
  same `virsh net-list` view `mqlab net status` renders, so the tile and the CLI
  never disagree.
- **Reachability cadence.** Ping interval vs. ICMP load on the isolated nets —
  tune so a break shows within a scrape interval without flooding.

## 9. Success criteria

1. The dashboard reads top-to-bottom MQ(reserved) / VMs / networks; the VM
   section is grouped in lab-structured order with per-group up/down + CPU.
2. Killing a VM reddens its tile within a scrape interval; its group's CPU graph
   drops it — at a glance, "who's up and who's busy."
3. `mqlab net down net-hb-a` flips the `hb-a` network tile to red within a scrape
   interval; bringing it back returns it to green.
4. A network that is libvirt-active but has an unreachable peer shows **amber**,
   not green — the subtle fault is visible.
5. The whole dashboard + collectors are reproducible from scratch and pass
   `vrg-validate`, including a guard that the renderer still emits valid targets
   (now including the hypervisor host).
