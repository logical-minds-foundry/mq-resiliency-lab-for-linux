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
| Network state | `lab_network_state{network}` 0/1/2 | **host** `node_exporter` textfile |
| Network reachability | `lab_net_reach{network,peer}` 0/1 | **each guest** `node_exporter` textfile |

**Prerequisite (Tweak 2, first task):** the Plan-A `node-exporter` role does
**not** yet enable the textfile collector — its unit is just
`--web.listen-address=:9100`. Both new metric families are written as
`node_exporter` textfiles, so Tweak 2 must first add
`--collector.textfile.directory=/var/lib/node_exporter/textfile` to the role and
create that directory. (Plan C's QM-owner collector will rely on the same
capability.) Without it, the collectors write `.prom` files nothing ever scrapes
— an empty-dashboard failure the §7 fail-loud rule exists to prevent.

### 3.2 Network state — host collector

Libvirt networks are host-side objects; no guest can see them. On the **Vergil
VM** (the libvirt host, where `mqlab net` and `virsh` already run), a systemd
timer runs `virsh net-list --all` and writes a `node_exporter` textfile exposing
a **tri-state** metric — `lab_network_state{network}` = `0`/`1`/`2` for
`absent`/`inactive`/`active`, mirroring #107's `lifecycle.classify_net`
(`ABSENT`/`INACTIVE`/`ACTIVE`).

Crucially, the collector iterates the **topology-declared** network list (not
just what `virsh net-list` returns), so a *destroyed* (undefined) net — which
`net-list` omits entirely — still gets an explicit `state=0` tile instead of
showing Grafana "No data." This keeps the two operations distinct:
`mqlab net down net-hb-a` → `inactive` (1); `mqlab net destroy net-hb-a` →
`absent` (0). Authoritative, and matches exactly what `mqlab net status` shows.

**Folded health (recording rule).** The dashboard's health tile (§4) wants one
value combining state (host) and reachability (per-guest) — different scrape
targets, so a Prometheus **recording rule** `lab_network_health` joins them:
absent→0, inactive→1, active-but-a-peer-unreachable→2, active+reachable→3, with
missing reach data defaulting to reachable. It lives in the prometheus role's
rules file (`/etc/prometheus/rules/lab.rules.yml`).

**Per-network throughput.** No new collector: each libvirt net `net-X` has a host
bridge `virbr-X`, and the host node_exporter already exports
`node_network_{receive,transmit}_bytes_total{device="virbr-X"}`. The dashboard's
per-net rx/tx graphs (§4) query those directly.

### 3.3 Network reachability — per-node peer-ping

State alone misses "active in libvirt but traffic isn't flowing" (a fenced node,
a NIC/firewall fault, a partial partition). So each guest runs a textfile timer
that pings its **same-network peers** and writes
`lab_net_reach{network="net-hb-a",peer="pcmk-a2"} 0/1`. The peer list per node
per network is **derived from topology** (a pure function: hosts sharing a
network, minus self). This reuses `node_exporter` — no new target — and surfaces
a heartbeat break or a fenced peer directly as "pcmk-a1 can't reach pcmk-a2 on
hb-a."

**Privilege & fail-loud.** `ping` needs a raw ICMP socket, so the timer either
runs as root (it is a lab VM) or the node sets `net.ipv4.ping_group_range` so an
unprivileged ping works. The collector emits a `lab_net_reach_last_write_timestamp`
alongside the samples; a probe that can't run (permissions, crash) must read as
**stale**, never as green — a silently-blind reachability check that shows
"reachable" is exactly the lie §7 forbids.

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
║ ▌NETWORKS (green=active+reachable, amber=active-unreachable, red=down, grey=absent)║
║  data-a│data-b│ hb-a │ hb-b │san-a│san-b│ wan │client│ dtcc │ mgmt        ║
╚══════════════════════════════════════════════════════════════════════════╝
```

- **MQ service (top):** a reserved row with a placeholder note; Plan B fills it.
- **VMs (middle):** rows in **curated, lab-shaped order** — SAN → PCMK A/B →
  RDQM A/B → standalone → observability. **Each row composes one or more groups**
  (SAN pairs both site SANs; the cluster arms split A/B; standalone and
  observability each fold their members together), and shows member up/down stat
  tiles (legend `{{host}}`) **+** a single CPU-busy% timeseries scoped to the
  row's group(s). CPU-busy% =
  `100 - avg by (host)(rate(node_cpu_seconds_total{mode="idle"}[1m]))*100`,
  filtered to the row's group selector.
  The curated order is **rendered from code, not hand-edited JSON**: a pure
  `render_dashboard(topo)` (sibling of `render_inventory`/`render_scrape_targets`)
  projects a curated `ROWS` list × the topology `groups` namespace into the
  Grafana JSON. This keeps it DRY, unit-testable to the 100% bar, fail-loud on an
  unknown group, and extendable by Tweak 2 / Plan B rather than hand-edited.
  Adding/reordering a row is a one-line change to `ROWS`.
- **Networks (bottom):** **one row per network**, grouped into three
  **collapsible section rows** in curated order — **Message path**
  (client, dtcc, data-a, data-b) → **Cluster + storage** (hb-a, san-a, hb-b,
  san-b) → **Cross-site + mgmt** (wan, mgmt). Names are shorthand (`data-a`, not
  `net-data-a`). Each net row = a **folded-health tile** + its own **receive**
  and **transmit** throughput graphs:
  - **Health tile** — `lab_network_health` → `0/1/2/3` = grey (absent) / red
    (down) / amber (active-but-unreachable) / green (up+reachable). The fold of
    state + reachability is a Prometheus **recording rule** (§3.2), since state is
    host-side and reachability per-guest; amber only appears during a live drill.
    Grey vs. red still distinguishes `mqlab net destroy` from `mqlab net down`.
  - **rx + tx graphs** — `rate(node_network_{receive,transmit}_bytes_total{device="virbr-<x>"}[1m])`
    from the host node_exporter (each libvirt net `net-X` has host bridge
    `virbr-X`; **already scraped — no new telemetry**). Separate graphs give each
    net its own Y-scale, so the heartbeat net's tiny traffic stays visible
    instead of being flattened against the data net.

## 5. Provisioning — all as code

- **Host collector** (§3.2): a **local Ansible play** (`connection: local`) that
  installs `node_exporter` + the `virsh net-list` textfile timer on the Vergil
  VM. `mqlab obs up` gains a step to run it (the host is part of the observability
  fabric, brought up with the pair). Kept out of `vergil.toml` so the dev-VM
  profile stays about the toolchain, not lab services.
- **Textfile collector** (§3.1, Tweak 2 first task): the `node-exporter` role
  gains `--collector.textfile.directory=/var/lib/node_exporter/textfile` and
  creates the directory — the carrier both new collectors write to.
- **Reachability collector** (§3.3): the `observability.yml` overlay gains the
  per-node peer-ping textfile timer; peer lists rendered from topology.
- **Dashboard:** `render_dashboard(topo)` (§4) emits the JSON; the `grafana` role
  **deploys the rendered file** from `build/` — the same pattern the `prometheus`
  role uses for its rendered targets, replacing the static `fleet-node.json`. The
  dashboard **evolves in place**: title becomes "Lab — Layered Status" but the
  **`uid` stays `lab-fleet-node`**, which is hard-referenced in `mqlab obs open`
  (`cli.py`), `lab/scripts/obs-open.sh`, and `getting-started.md` — pinning it
  keeps those deep-links working untouched.
- **CLI verbs:** `mqlab obs` gains `dashboard` (render the dashboard JSON,
  mirroring `obs targets`), and — for Tweak 2 — `net-state` (emit
  `lab_network_state` from `virsh net-list`, invoked by the host timer) and
  `reach-peers` (render the per-host peer map for the reachability collector).
  `mqlab obs up` renders the dashboard + runs the host play alongside targets and
  inventory.

## 6. Build split — one spec, two plans

- **Tweak 1 — Dashboard restructure** *(uses only existing metrics)*: the layered
  shell (reserved MQ row / grouped VM rows / **placeholder** network strip),
  per-group up/down tiles + per-group CPU graphs, curated order. Independent and
  fast — no new telemetry. Delivers the grouped, lab-shaped view immediately.
- **Tweak 2 — Network telemetry + live panel**: **first** enable the
  `node-exporter` textfile collector (§3.1 prerequisite), then the host
  tri-state net-state collector, the per-node reachability collector, the
  `render_scrape_targets` host target, and wiring the bottom strip to real
  `lab_network_state` / `lab_net_reach` data.

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
- **Reuse `lifecycle.classify_net` in the host collector** rather than
  re-parsing `virsh net-list`, so the tile's state and `mqlab net status` can
  never disagree (both go through the same #107 classifier). Confirm the
  collector can import/shell that path cleanly from the host.
- **Reachability cadence.** Ping interval vs. ICMP load on the isolated nets —
  tune so a break shows within a scrape interval without flooding.

## 9. Success criteria

1. The dashboard reads top-to-bottom MQ(reserved) / VMs / networks; the VM
   section is grouped in lab-structured order with per-group up/down + CPU.
2. Killing a VM reddens its tile within a scrape interval; its group's CPU graph
   drops it — at a glance, "who's up and who's busy."
3. `mqlab net down net-hb-a` flips the `hb-a` tile to red within a scrape
   interval; `mqlab net destroy net-hb-a` flips it to **grey** (absent) — visibly
   distinct from down; bringing it back up returns it to green.
4. A network that is libvirt-active but has an unreachable peer shows **amber**,
   not green — the subtle fault is visible; an unrunnable reachability probe
   reads as stale, never green.
5. The whole dashboard + collectors are reproducible from scratch and pass
   `vrg-validate`, including a guard that the renderer still emits valid targets
   (now including the hypervisor host).
