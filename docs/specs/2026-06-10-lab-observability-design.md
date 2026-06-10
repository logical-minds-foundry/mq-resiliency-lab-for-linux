# Lab Observability — Prometheus + Grafana on a Dedicated `obs` VM — Design

- **Date:** 2026-06-10
- **Status:** Design — brainstormed 2026-06-10
- **Issue:** #103
- **Relationship:** a cross-cutting sub-project of
  `2026-06-03-mq-cluster-lab-design.md`. It feeds **Phase E** (the
  RDQM-vs-Pacemaker comparison gets one honest instrument to watch both arms
  through) and **Phase F** (operational standards / health checks). It
  **complements** the DR **Watcher** of
  `2026-06-08-dr-ha-validation-framework-design.md`: the Watcher *validates* DR
  programmatically; this *shows* it to a human. It surfaces — does not replace —
  the metrics already published by MQ, Pacemaker/DRBD, and `pymqrest`.

## Table of contents

- [1. Problem & motivation](#1-problem--motivation)
- [2. Goals & non-goals](#2-goals--non-goals)
- [3. Architecture](#3-architecture)
  - [3.1 The `obs` VM](#31-the-obs-vm)
  - [3.2 The `net-mgmt` scrape plane](#32-the-net-mgmt-scrape-plane)
  - [3.3 Exporter map](#33-exporter-map)
  - [3.4 The custom QM-ownership collector](#34-the-custom-qm-ownership-collector)
  - [3.5 Scrape targets generated from `topology.yaml`](#35-scrape-targets-generated-from-topologyyaml)
- [4. The layered build order](#4-the-layered-build-order)
- [5. Access — "visit a URL"](#5-access--visit-a-url)
- [6. `mqlab obs` command surface](#6-mqlab-obs-command-surface)
- [7. Provisioned as code](#7-provisioned-as-code)
- [8. Cross-cutting concerns](#8-cross-cutting-concerns)
  - [8.1 Fail loud — no stale panels](#81-fail-loud--no-stale-panels)
  - [8.2 Secrets](#82-secrets)
  - [8.3 Testing & validation](#83-testing--validation)
- [9. Open questions](#9-open-questions)
- [10. Success criteria](#10-success-criteria)

---

## 1. Problem & motivation

The lab now produces real running stacks across two sites: networks, guests, a
standalone QM and message path, the RDQM HA/DR arm, the Pacemaker/SAN+DRBD arm,
and a continuous-flow DR/HA validation framework. Today the only way to know
what the lab is *doing* is to SSH into nodes and read tool output — `rdqmstatus`
here, `crm_mon` there, `dspmq`, `drbdsetup status`. There is no single place a
person can look and **watch the lab live**.

The whole point of this engagement is an objective, defensible comparison of two
HA/DR approaches, demonstrated through deliberate fault drills. Those drills are
the drama — a node dies, a QM moves, a site is lost, replication catches up. A
person should be able to **open a URL and see that drama unfold graphically**:
the fleet at a glance, whether the trade is flowing, and — the lab-specific
payoff — *which node owns the queue manager right now* and what the replication
lag / RPO window is as a failover happens.

This is also a client-facing artifact. When we show DTCC "here is what each arm
costs to operate," a shared Grafana dashboard both arms are watched through is
far more credible than a homegrown toy.

## 2. Goals & non-goals

**Goals**

- A dedicated, ephemeral observability VM running a **standard, free, OSS**
  metrics stack (Prometheus + Grafana) that scrapes the whole fleet.
- Three watchable layers, built in order: **fleet infra health → MQ internals +
  cluster health → the bespoke "who owns the QM" / DR-state view.**
- A scrape plane (`net-mgmt`) that **fault drills never sever**, so the monitor
  stays live exactly when the drama happens.
- Everything reproducible from scratch (provisioned as code), consistent with
  the repo's ephemeral-lab contract.
- A modest `mqlab obs` slice so a human drives it the same way they drive
  `mqlab net` / `mqlab vm`.

**Non-goals**

- **Timeline / event narration (Layer 4).** The "QM moved a1→a2 at 14:03:22"
  event feed — via Loki + Promtail or Grafana annotations driven by the Watcher
  — is named here and **deferred to a follow-up spec**. This design delivers the
  metric foundation it would sit on.
- **Long-term retention / alerting infrastructure.** Lab data is ephemeral;
  Prometheus runs short retention. Alertmanager and paging are out of scope.
- **Replacing the DR Watcher.** The Watcher validates; this visualizes.
- **Production hardening** (TLS on Grafana, RBAC, HA Prometheus). It is a lab.

## 3. Architecture

```
                    net-mgmt 10.50.0.0/24  (NEW — out-of-band scrape plane;
                                            never severed by fault drills)
   +----------+         |
   |   obs    | .2  ----+   every node gets ONE extra NIC here
   |  (VM)    |         |   (10.50.0.<id>), e.g. rdqm-a1 -> 10.50.0.31
   |          |         +-- qm-main, dtcc-sim, app-client
   | Prometheus         +-- rdqm-a1..b3        (RDQM arm)
   | + Grafana|         +-- san-a/b, pcmk-a1..b3 (Pacemaker/DRBD arm)
   +----------+         |
        ^
        |  "visit a URL" -> Grafana :3000 (via mqlab obs open)
```

### 3.1 The `obs` VM

A new node in `lab/topology.yaml`:

- **Platform:** `ubuntu2404-arm64` — light, KVM-accelerated under the lab's
  nested-virt model; Prometheus, Grafana, and all the exporters run cleanly on
  arm64.
- **Size:** ~2 CPU / 4 GB (Prometheus TSDB + Grafana are modest at lab scale).
- **NIC:** single interface on `net-mgmt` (`10.50.0.2`, a reserved low address
  clear of every node octet).
- **Runs:** Prometheus (scrape + short-retention TSDB) and Grafana (dashboards).

Because `obs` is a normal topology node, `mqlab vm up obs` already brings the VM
up; the `obs` slice (§6) adds the observability-specific glue.

### 3.2 The `net-mgmt` scrape plane

A new libvirt network, `lab/networks/net-mgmt.xml`, `10.50.0.0/24`. **Every
existing node gains one additional NIC on it.** This is the only network
Prometheus scrapes over.

This is the load-bearing architectural decision. The lab's purpose is fault
drills that **deliberately sever** heartbeat, SAN, data, and WAN networks. If the
monitor scraped over any of those, the dashboard would go blind at the exact
moment the drama happens. An out-of-band management plane keeps the monitor's
vantage stable — you watch the heartbeat net die without the monitor dying with
it — and mirrors how real shops keep monitoring alive through an incident.

Node IP convention on `net-mgmt`: reuse the node's **`net-wan` octet**, which is
already globally unique across both sites (e.g. `rdqm-a1` → `.31`, `pcmk-b1` →
`.61`). The data-NIC octet is *not* unique fleet-wide — `pcmk-a1` and `pcmk-b1`
are both `.51` on their respective data nets — so the WAN plane is the right
source for a flat mgmt `/24`. The three standalone nodes (not on `net-wan`) keep
their existing distinct octets (`qm-main` `.10`, `dtcc-sim` `.50`, `app-client`
`.60`); `obs` takes the reserved `.2`. The exact map is settled in the plan.

### 3.3 Exporter map

Exporters are deployed by Ansible roles and scraped by Prometheus on their
standard default ports:

| Exporter | Source | Runs on | Provides |
|---|---|---|---|
| `node_exporter` | [prometheus/node_exporter](https://github.com/prometheus/node_exporter) | **all nodes** | host health — up/down, CPU/mem/disk/net |
| `mq_prometheus` | [ibm-messaging/mq-metric-samples](https://github.com/ibm-messaging/mq-metric-samples) | QM hosts (`qm-main`, `rdqm-*`, `pcmk-*`) | queue depths, channel status, MQI call rates (port 9157) |
| `ha_cluster_exporter` | [ClusterLabs/ha_cluster_exporter](https://github.com/ClusterLabs/ha_cluster_exporter) | Pacemaker arm (`pcmk-a*/b*`) | Pacemaker/Corosync/**DRBD9**/SBD-fencing state |
| **QM-owner collector** | this repo (custom) | each QM host | `mqlab_qm_owner{qm,site,node,role}` — "who owns the QM right now" |

Notes from the research:

- IBM ships **example Grafana dashboards** with `mq_prometheus` (queue depths,
  MQI calls, channel status); ClusterLabs publishes a ready-made [HA cluster
  dashboard (#12229)](https://grafana.com/grafana/dashboards/12229-ha-cluster-details/).
  We import and adapt these rather than build from scratch.
- **DRBD9 requires `ha_cluster_exporter`, not `node_exporter`'s DRBD
  collector** — the latter only reads the legacy DRBD-8.4 `/proc/drbd`, which
  DRBD9 no longer populates.

### 3.4 The custom QM-ownership collector

"Which site/node owns the QM right now" is not a standard metric — it is the one
genuinely lab-specific signal, and the only bespoke code in this design. It is
small.

- **Source of truth per arm:** `rdqmstatus -m <QM>` (RDQM HA/DR role),
  `crm_mon -X` (Pacemaker resource location), and the QM running state via
  `pymqrest` (already a lab dependency).
- **Mechanism:** a small script deployed by Ansible on each QM host writes
  `node_exporter`'s **textfile collector** output — keeping the whole stack
  pull-model and uniform, with no extra listener to manage. It publishes
  `mqlab_qm_owner{qm,site,node,role}` and a companion freshness/error metric
  (§8.1).
- **Consumed by:** the Layer-3 Grafana dashboard, as the per-site/per-node state
  panels and the "QM owner" table.

### 3.5 Scrape targets generated from `topology.yaml`

Prometheus scrape targets are **generated from `topology.yaml`**, not
hand-maintained — the same single-source-of-truth pattern `mqlab` and the
Ansible inventory already follow. Add a node to the topology and it is scraped;
no second list to keep in sync. The generator is pure logic (topology → scrape
config), which makes it unit-testable to the repo's coverage bar (§8.3).

## 4. The layered build order

Each layer is a working, watchable thing on its own, so payoff comes early and
there is never a half-built dashboard.

- **Layer 0 — Spike: prove the plumbing (narrow).** `obs` VM + `net-mgmt` +
  Prometheus + Grafana + `node_exporter` on just two or three nodes. Goal:
  confirm the scrape plane works, Grafana comes up provisioned, and **the URL
  opens from the developer's Mac** (§5). De-risks the access path before wiring
  the fleet. Matches the repo's spike-first habit.
- **Layer 1 — Fleet at a glance (infra).** `node_exporter` fleet-wide + a
  fleet-health dashboard. Green/red node tiles, CPU/mem/disk/net. Now you can
  watch a VM drop during a drill.
- **Layer 2 — Is the trade flowing (MQ + cluster health).** `mq_prometheus` on
  QM hosts (import IBM's dashboard) + `ha_cluster_exporter` on the Pacemaker arm
  (import ClusterLabs #12229). Queue depths, channel status, DRBD replication
  state, Pacemaker resource location.
- **Layer 3 — Who owns the QM (the bespoke DR view).** The `mqlab_qm_owner`
  collector + a hand-built Grafana dashboard: per-site/per-node state panels, a
  "QM owner" table, replication-lag / RPO-window line. The failover-watching
  payoff, and the only panel that justifies any custom code.
- **Layer 4 — Deferred (own spec): timeline narration.** Loki + Promtail (or
  Grafana annotations driven by the Watcher) for the event feed. **Not in this
  design** — named and scoped out.

This spec delivers **Layers 0–3**.

## 5. Access — "visit a URL"

The lab runs inside the Vergil VM (the libvirt host). Grafana listens on
`obs:3000` on `net-mgmt`, which is host-routable from inside the Vergil VM. The
chain from the developer's Mac is: macOS → Vergil VM session → `10.50.0.10:3000`.

To collapse that to one step, `mqlab obs open` sets up the port-forward and
prints the URL. **The Layer-0 spike must exercise this end-to-end** — an
observability stack you cannot open is worthless, so access is proven before any
dashboards are built.

## 6. `mqlab obs` command surface

A new slice mirroring the existing `net` / `vm` slices:

| Command | Does |
|---|---|
| `mqlab obs up` | bring up the `obs` VM and (re)generate Prometheus scrape targets from the currently-running fleet |
| `mqlab obs status` | report whether Prometheus/Grafana are up, which targets are configured, and which are `up`/`down` |
| `mqlab obs open` | port-forward `obs:3000` and print the Grafana URL |

The `obs` VM lifecycle itself is plain `mqlab vm` (it is a topology node); the
`obs` slice adds only the observability-specific glue (target regeneration + the
open helper). Exact verb mechanics are a plan-time concern.

## 7. Provisioned as code

Because the lab is ephemeral and 100% reproducible, nothing is clicked in by
hand:

- **Grafana** datasource + dashboards via file-based provisioning (dashboards
  checked into the repo as JSON).
- **Prometheus** config generated from `topology.yaml` (§3.5).
- **Exporters** as Ansible roles (`node_exporter` fleet-wide; `mq_prometheus`
  and the owner collector on QM hosts; `ha_cluster_exporter` on the Pacemaker
  arm).

Rebuild the lab and the entire observability stack and every dashboard return
identical — the same reproducibility contract as the rest of the repo.

## 8. Cross-cutting concerns

### 8.1 Fail loud — no stale panels

A silently stale dashboard is worse than no dashboard: it lies. (This is the
repo/owner "no silent failures" rule applied to telemetry.)

- A failed scrape surfaces as Prometheus `up == 0` → a **red tile**, never an
  empty or last-known-good panel.
- The `mqlab_qm_owner` collector emits an **explicit error/staleness metric**
  whenever `rdqmstatus` / `crm_mon` / `pymqrest` cannot be reached, and **never
  re-publishes a stale owner**. A failover you cannot see because the collector
  quietly died is the precise failure mode this project exists to prevent.

### 8.2 Secrets

Secrets stay out of git (repo policy). `mq_prometheus` / `pymqrest` QM
credentials and the Grafana admin password are **runtime-injected** via the
existing lab-secret mechanism (`lab/scripts/lab-secret.sh`), never committed.
`.gitignore` already covers `*.env` / `secrets/`.

### 8.3 Testing & validation

- The pure logic — `topology.yaml` → Prometheus scrape-target generation, and
  the `mqlab obs` slice — is unit-tested with pytest to the repo's coverage bar
  (StrEnum/UP042, 100% branch coverage, `uv run pytest`).
- Grafana dashboards-as-code are JSON-lint validated.
- Exporter provisioning is checked by an Ansible smoke step.
- `vrg-container-run -- vrg-validate` remains the **only** validation command.

## 9. Open questions

- **RDQM's bundled cluster stack.** RDQM bundles its own Pacemaker/Corosync/DRBD
  under `/opt/mqm`. This design does **not** depend on `ha_cluster_exporter`
  reading that bundled stack — the RDQM arm relies on `mq_prometheus` + the
  `rdqmstatus`-based owner collector. Whether the exporter *can* be pointed at
  RDQM's internals (for richer DRBD/Pacemaker metrics) is worth a later spike,
  not a blocker.
- **`mq_prometheus` connection mode** (local bindings vs. client connection vs.
  container) on each arm — settle during the plan against how MQ is installed
  per arm.
- **Owner-collector cadence** — textfile refresh interval vs. Prometheus scrape
  interval; tune so a failover is visible within a few seconds without hammering
  `rdqmstatus`/`crm_mon`.

## 10. Success criteria

1. From a freshly rebuilt lab, `mqlab obs up` + `mqlab obs open` lands a human on
   a live Grafana URL with no hand-configuration.
2. **Layer 1:** every running node shows up/down + host health; killing a VM
   turns its tile red within the scrape interval.
3. **Layer 2:** queue depth and channel status are visible for the standalone
   and clustered QMs; the Pacemaker arm shows DRBD replication and resource
   location.
4. **Layer 3:** the DR dashboard names the current QM owner per arm, and a
   deliberate failover/cutover is **watchable** — owner flips, replication lag
   moves — without reading any node's shell.
5. A severed heartbeat/SAN/WAN net during a drill does **not** blind the monitor
   (the `net-mgmt` plane holds).
6. The whole stack is reproducible from scratch and passes `vrg-validate`.
