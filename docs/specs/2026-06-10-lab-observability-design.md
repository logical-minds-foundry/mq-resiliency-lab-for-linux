# Lab Observability — Prometheus + Grafana on a Dedicated `obs` VM — Design

- **Date:** 2026-06-10
- **Status:** Design — brainstormed 2026-06-10; pushback applied (6 issues
  resolved); rebased onto **#104** (static topology-derived inventory + unified
  grouping namespace + the `net-mgmt` plane).
- **Issue:** #103
- **Builds on #104 / #101.** The dedicated **`net-mgmt`** management plane,
  the **unified group namespace** (atomic role×site groups + setups as group
  compositions), and the **pure topology→inventory renderer** (`render_inventory`,
  `mqlab vm inventory`) already exist. This design *consumes* them — it does not
  re-create the network or invent a parallel grouping/target mechanism.
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
  - [3.1 The `obs` and `mon-probe` nodes](#31-the-obs-and-mon-probe-nodes)
  - [3.2 The `net-mgmt` scrape plane (consumed from #104)](#32-the-net-mgmt-scrape-plane-consumed-from-104)
  - [3.3 Exporter map (targeted by the unified group namespace)](#33-exporter-map-targeted-by-the-unified-group-namespace)
  - [3.4 MQ metrics for a floating QM — client-mode on `mon-probe`](#34-mq-metrics-for-a-floating-qm--client-mode-on-mon-probe)
  - [3.5 The custom QM-ownership collector](#35-the-custom-qm-ownership-collector)
  - [3.6 Scrape targets rendered from `topology.yaml`](#36-scrape-targets-rendered-from-topologyyaml)
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
  metrics stack (Prometheus + Grafana) that scrapes the whole fleet over the
  existing `net-mgmt` plane.
- Three watchable layers, built in order: **fleet infra health → MQ internals +
  cluster health → the bespoke "who owns the QM" / DR-state view.**
- MQ metrics that **survive failover** — exporters that follow the floating QM
  rather than being pinned to a node that may not own it.
- Everything reproducible from scratch (provisioned as code), rendered from
  `topology.yaml` like the inventory, consistent with the repo's ephemeral-lab
  contract.
- A modest `mqlab obs` slice so a human drives it the way they drive `mqlab vm`.

**Non-goals**

- **Timeline / event narration (Layer 4).** The "QM moved a1→a2 at 14:03:22"
  event feed — via Loki + Promtail or Grafana annotations driven by the Watcher
  — is named here and **deferred to a follow-up spec**. This design delivers the
  metric foundation it would sit on.
- **Long-term retention / alerting infrastructure.** Lab data is ephemeral;
  Prometheus runs short retention. Alertmanager and paging are out of scope.
- **Re-creating `net-mgmt` or the grouping/inventory machinery.** Those landed
  in #104; this design consumes them.
- **Replacing the DR Watcher.** The Watcher validates; this visualizes.
- **Production hardening** (Grafana TLS/RBAC, HA Prometheus). It is a lab.
- **Recommending Prometheus/Grafana for the client's production.** That depends
  on the client's existing infrastructure and is deferred to discovery; here it
  is simply the right free instrument for *our* lab.

## 3. Architecture

```
                    net-mgmt 10.50.0.0/24  (host-only; landed in #104; the host
                                            is .1; drills never sever it)
   +-----------+        |
   |   obs     | .2 ----+   every node already carries a net-mgmt NIC (#104)
   | Prometheus|        +-- qm-main(.10) dtcc-sim(.50) app-client(.60)
   | + Grafana |        +-- rdqm-a/b(.31-33/.41-43)   (RDQM arm)
   +-----------+        +-- san-a/b(.5/.6) pcmk-a/b(.51-53/.61-63)
        ^               +-- mon-probe(.3)
        | scrape (mgmt) |
        +------ mon-probe .3 --- net-data-a/b ---> floating QM VIPs (10.10.1/2.x)
                (client-mode mq_prometheus, one per clustered QM)
```

### 3.1 The `obs` and `mon-probe` nodes

Two new nodes in `lab/topology.yaml`, in the #104 schema (every node carries a
`net-mgmt` NIC; membership is expressed through `groups`):

- **`obs`** — `ubuntu2404-arm64`, ~2 CPU / 4 GB. Single NIC on `net-mgmt`
  (`10.50.0.2`, a reserved low address clear of every node octet; the host holds
  `.1`). Runs **Prometheus** (scrape + short-retention TSDB) and **Grafana**.
  Stays **mgmt-only** so a fault drill can never blind it.
- **`mon-probe`** — `ubuntu2404-arm64`, ~1 CPU / 1 GB. NICs on `net-mgmt`
  (`10.50.0.3`) **and** `net-data-a` (`10.10.1.7`) + `net-data-b` (`10.10.2.7`).
  Hosts the **client-mode `mq_prometheus` instances** for the *floating*
  clustered QMs (§3.4). It carries the data-plane reach so `obs` does not have
  to — Prometheus scrapes `mon-probe` over mgmt; `mon-probe` reaches the QM VIPs
  over data.

Splitting observer (`obs`) from data-plane probe (`mon-probe`) keeps the
monitor's vantage stable *and* keeps the workload host (`app-client`, the trade
client under test) separate from the thing watching it — the same
"separate the observer" principle that motivated the mgmt plane.

New atomic groups and a setup carry them (underscore = group, hyphen = host, per
#101):

```yaml
groups:
  obs:   [obs]
  probe: [mon-probe]
setups:
  monitoring:
    description: Observability pair — Prometheus/Grafana + the MQ client probe
    groups: [obs, probe]
    provision: ansible/site-obs.yml
```

`mqlab obs up` brings the `monitoring` setup up **independently of whichever arm
is running**, so the observer pair is always available (§6). Exact node sizes,
IPs, and the `site-obs.yml` role split are settled in the plan.

### 3.2 The `net-mgmt` scrape plane (consumed from #104)

`net-mgmt` already exists: `lab/networks/net-mgmt.xml`, **host-only**
(`10.50.0.1/24`, no `<forward>`, no DHCP), with **every node already assigned a
static `net-mgmt` IP** in `topology.yaml`. It is the dedicated management plane
Ansible connects over, deliberately kept off the modeled data/heartbeat/SAN
planes. This design simply **scrapes over it**.

Two properties this gives the monitor for free:

- **Drills never sever it.** The fault suite severs heartbeat/SAN/data/WAN; it
  does not touch `net-mgmt`. So the dashboard stays live exactly when the drama
  happens — you watch the heartbeat net die without the monitor dying with it.
- **No route hijack, already validated.** Because `net-mgmt` is host-only with
  **no gateway/DHCP**, adding it as a NIC cannot introduce a competing default
  route — the perturbation risk of multi-homing the cluster nodes was taken on
  and **live-validated in #104**, not by this work. (Re-running the §3.1 fault
  drills with mgmt attached is #104's acceptance gate, not ours.)

`obs` takes `10.50.0.2` and `mon-probe` `10.50.0.3`; `.4` remains free. The host
is reachable at `10.50.0.1`, which makes the access path trivial (§5).

### 3.3 Exporter map (targeted by the unified group namespace)

Exporters are deployed by Ansible roles **targeting the #104 group namespace**,
and scraped by Prometheus on their standard ports:

| Exporter | Source | Ansible target (groups) | Provides |
|---|---|---|---|
| `node_exporter` | [prometheus/node_exporter](https://github.com/prometheus/node_exporter) | **`all`** | host health — up/down, CPU/mem/disk/net |
| `mq_prometheus` (local) | [ibm-messaging/mq-metric-samples](https://github.com/ibm-messaging/mq-metric-samples) | `qm` | standalone QM metrics (local bindings) |
| `mq_prometheus` (client) | same | runs on `probe`, targets clustered QMs | floating-QM metrics over the VIP (§3.4) |
| `ha_cluster_exporter` | [ClusterLabs/ha_cluster_exporter](https://github.com/ClusterLabs/ha_cluster_exporter) | `pcmk_a`, `pcmk_b` | Pacemaker/Corosync/**DRBD9**/SBD-fencing state |
| **QM-owner collector** | this repo (custom) | `qm`, `rdqm_a/b`, `pcmk_a/b` | `mqlab_qm_owner{qm,site,node,role}` (§3.5) |

Notes from the research:

- IBM ships **example Grafana dashboards** with `mq_prometheus`; ClusterLabs
  publishes a ready-made [HA cluster dashboard (#12229)](https://grafana.com/grafana/dashboards/12229-ha-cluster-details/).
  We import and adapt these — "adapt" includes a panel-type pass, since the IBM
  dashboard was authored on Grafana v5.3.1 and deprecates `graph`→`timeseries`.
- **DRBD9 requires `ha_cluster_exporter`, not `node_exporter`'s DRBD
  collector** — the latter only reads the legacy DRBD-8.4 `/proc/drbd`.
- **RDQM bundles its own Pacemaker/Corosync/DRBD under `/opt/mqm`.** This design
  does **not** depend on `ha_cluster_exporter` reading that bundled stack — the
  RDQM arm relies on `mq_prometheus` + the `rdqmstatus`-based owner collector.

### 3.4 MQ metrics for a floating QM — client-mode on `mon-probe`

On the HA/DR arms the queue manager **floats**: it runs on exactly one of three
nodes at a time and moves on failover; for DR the recovery QM is a separate
running instance at the site-B VIP. A per-node local-bindings exporter would be
attached to a QM that isn't running on two of three nodes, and the "is the trade
flowing" view would fragment and hop as the QM moves.

**Resolution (pushback Issue 1):** for the clustered QMs, run `mq_prometheus`
in **client mode on `mon-probe`**, connecting to each QM over its **floating
VIP** (`10.10.1.x` site-A, `10.10.2.x` site-B) on a fixed local port — one
instance per QM. Prometheus scrapes those fixed ports over mgmt; the exporter
chases the QM via the VIP, so **Prometheus never has to chase a moving agent IP**
(the exact failure mode of the off-the-shelf integration). After a failover the
exporter reconnects to the new owner through the VIP; the brief unreachable
window *is* the outage, and reads as a truthful gap.

The standalone `qm-main` does **not** float, so it keeps a simpler
**local-bindings** exporter on the node itself, scraped over mgmt.

This is acknowledged as **non-trivial integration** and is the primary **spike**
of Layer 2 — to be experimented on directly before dashboards are committed to
it. (In production we would prefer the exporter tightly coupled to the QM as a
queue-manager service; that is explicitly *not* the lab choice here.)

### 3.5 The custom QM-ownership collector

"Which site/node owns the QM right now" is not a standard metric, and under the
client-mode decision (§3.4) it no longer falls out of the MQ exporter — so this
collector is the **sole source** of the headline DR-state view. It is the only
bespoke code in the design, and it is small.

- **Source of truth per arm:** `rdqmstatus -m <QM>` (RDQM HA/DR role),
  `crm_mon -X` (Pacemaker resource location), and QM running state via
  `pymqrest` (already a lab dependency).
- **Mechanism (pushback Issue 5):** a **privileged systemd timer** on each
  QM-hosting node runs the source command and **atomically** (temp-file +
  rename) writes a `node_exporter` **textfile** `.prom`. node_exporter only
  *reads* the file, so the privileged producer and the unprivileged exporter
  stay cleanly separated. Cadence ~5 s — fast enough to see a failover promptly,
  slow enough not to hammer `rdqmstatus`/`crm_mon`.
- **Fail-loud:** it emits `mqlab_qm_owner{qm,site,node,role}` **and** a
  `mqlab_qm_owner_last_write_timestamp` the dashboard alerts stale on. A failed
  or stale write never re-publishes a last-known owner (§8.1).
- **Consumed by:** the Layer-3 Grafana dashboard — per-site/per-node state
  panels and the "QM owner" table.

### 3.6 Scrape targets rendered from `topology.yaml`

Prometheus targets are **rendered from `topology.yaml`**, mirroring the #104
inventory renderer (`src/mqlab/inventory.py`): a pure
`render_scrape_targets(topo)` sibling that projects the topology to Prometheus
file-SD / scrape config, **keyed on each host's `net-mgmt` IP** (reusing the
`_mgmt_ip` accessor) and **fail-loud** on integrity problems (the same
`InventoryError` contract — a host with no mgmt IP is an error, never a silent
skip). One source of truth, one address plan; add a node to the topology and it
is scraped.

Jobs map to the group namespace:

- **`node`** — every host, `<mgmt-ip>:9100`.
- **`mq`** — `qm-main` at `<qm-main mgmt-ip>:9157` (local), plus the
  `mon-probe`-hosted client exporters at `<mon-probe mgmt-ip>:<per-QM port>`.
  This is the one job that is *not* 1:1 with hosts (multiple QMs behind one probe
  host on distinct ports) — the renderer encodes the QM→port map.
- **`ha_cluster`** — `pcmk_a` + `pcmk_b` hosts at `<mgmt-ip>:9664`.
- The **QM-owner** signal rides the `node` job's textfile collector — no
  separate target.

## 4. The layered build order

Each layer is a working, watchable thing on its own, so payoff comes early and
there is never a half-built dashboard.

- **Layer 0 — Spike: prove the plumbing (narrow).** `obs` + `mon-probe` brought
  up on the existing `net-mgmt` plane + Prometheus + Grafana + `node_exporter`
  on a couple of nodes. Goal: confirm Prometheus scrapes over mgmt, Grafana
  comes up provisioned, and **the URL opens from the developer's Mac** (§5).
  De-risks access before wiring the fleet. (net-mgmt itself is *already* proven —
  #104 — so the spike is purely the observability stack.)
- **Layer 1 — Fleet at a glance (infra).** `node_exporter` on `all` + a
  fleet-health dashboard. Green/red node tiles, CPU/mem/disk/net. Watch a VM
  drop during a drill.
- **Layer 2 — Is the trade flowing (MQ + cluster health).** The §3.4 client-mode
  MQ spike: `mq_prometheus` on `mon-probe` for the clustered QMs + local on
  `qm-main`; `ha_cluster_exporter` on the Pacemaker arm. **Plus the QM-side
  enablement** (pushback Issue 3): an Ansible + `pymqrest` step that turns on QM
  monitoring/statistics and lays down the exporter's **SVRCONN channel + auth**.
  Per the design's §11, DTCC mandates **TLS** on MQ channels, so the exporter
  channel is TLS — a faithful dry-run of the onboarding config. (Allowed
  sequencing tweak: get metrics flowing on a plaintext channel first, add TLS as
  a Layer-2 follow-step.) Import IBM's and ClusterLabs' dashboards.
- **Layer 3 — Who owns the QM (the bespoke DR view).** The §3.5 owner collector
  + a hand-built Grafana dashboard: per-site/per-node state panels, a "QM owner"
  table, replication-lag / RPO-window line. The failover-watching payoff.
- **Layer 4 — Deferred (own spec): timeline narration.** Loki + Promtail (or
  Grafana annotations driven by the Watcher). **Not in this design.**

This spec delivers **Layers 0–3**.

## 5. Access — "visit a URL"

The lab runs inside the Vergil VM (the libvirt host), which holds **`10.50.0.1`
on `virbr-mgmt`**. So from inside the Vergil VM, Grafana at `obs` (`10.50.0.2:3000`)
is **directly reachable** — no extra routing. From the developer's Mac the chain
is macOS → Vergil VM session → `10.50.0.2:3000`; `mqlab obs open` collapses that
to one step (port-forward + print the URL). **The Layer-0 spike must exercise
this end-to-end** — an observability stack you cannot open is worthless.

## 6. `mqlab obs` command surface

A new slice alongside `mqlab vm` (which now includes `vm inventory`):

| Command | Does |
|---|---|
| `mqlab obs up` | bring up the `monitoring` setup (`obs` + `mon-probe`) and render Prometheus targets from topology |
| `mqlab obs status` | report Prometheus/Grafana up, configured targets, and which are `up`/`down` |
| `mqlab obs open` | port-forward `obs:3000` and print the Grafana URL |

The node lifecycle itself is plain `mqlab vm` (both are topology nodes); the
`obs` slice adds only observability glue — target rendering (§3.6) and the open
helper. The `monitoring` setup is brought up **independently of the arm under
test**, so the observer pair is always available. Exact verb mechanics are a
plan-time concern.

## 7. Provisioned as code

Because the lab is ephemeral and 100% reproducible, nothing is clicked in by
hand:

- **Grafana** datasource + dashboards via file-based provisioning (dashboards
  checked in as JSON).
- **Prometheus** config rendered from `topology.yaml` (§3.6).
- **Exporters** as Ansible roles in `ansible/site-obs.yml` and overlay roles
  applied to the arm setups, targeting the #104 group namespace (`all`, `qm`,
  `pcmk_a/b`, `probe`).
- **QM-side enablement** (monitoring + SVRCONN + TLS) via Ansible + `pymqrest`.

Rebuild the lab and the entire observability stack and every dashboard return
identical — the same reproducibility contract as the rest of the repo.

## 8. Cross-cutting concerns

### 8.1 Fail loud — no stale panels

A silently stale dashboard is worse than no dashboard: it lies.

- A failed scrape surfaces as Prometheus `up == 0` → a **red tile**, never an
  empty or last-known-good panel.
- The owner collector emits an explicit `*_last_write_timestamp`; the dashboard
  alerts stale on it and **never** shows a held last-known owner (§3.5).
- The client-mode MQ exporters must **fail loud, not crash-loop**, when a VIP is
  unreachable mid-failover — the gap is the signal.

### 8.2 Secrets

Secrets stay out of git (repo policy). The exporter's SVRCONN credentials / TLS
material and the Grafana admin password are **runtime-injected** via the
existing lab-secret mechanism (`lab/scripts/lab-secret.sh`), never committed.
`.gitignore` already covers `*.env` / `secrets/`.

### 8.3 Testing & validation

- The pure logic — `render_scrape_targets(topo)` (§3.6) and the `mqlab obs`
  slice — is unit-tested with pytest to the repo's coverage bar (StrEnum/UP042,
  100% branch coverage, `uv run pytest`), with a guard test that the **real**
  `lab/topology.yaml` renders valid targets (mirroring #104's real-topology
  inventory guard).
- Grafana dashboards-as-code are JSON-lint validated.
- Exporter provisioning is checked by an Ansible smoke step.
- `vrg-container-run -- vrg-validate` remains the **only** validation command.

## 9. Open questions

- **Client-mode `mq_prometheus` integration (the Layer-2 spike).** Connecting
  the exporter to a floating QM over the VIP with a TLS SVRCONN channel — auth,
  reconnect behaviour on cutover, per-QM port map — is the known-hard part and
  will be experimented on directly. Settle the QM→port convention in the plan.
- **`mq_prometheus` arch coverage.** Confirm an arm64 build/run for the
  `mon-probe` (ubuntu-arm64) connecting to both the arm64 Pacemaker QMs and the
  x86 RDQM QMs over the network.
- **RDQM's bundled cluster stack.** Whether `ha_cluster_exporter` can be pointed
  at RDQM's `/opt/mqm` Pacemaker/DRBD for richer metrics is a later spike — not a
  dependency (the RDQM arm is covered by `mq_prometheus` + the owner collector).
- **`mon-probe` data-net addressing.** `10.10.1.7` / `10.10.2.7` are proposed as
  free; confirm against the VIP/portal plan in the plan step.

## 10. Success criteria

1. From a freshly rebuilt lab, `mqlab obs up` + `mqlab obs open` lands a human on
   a live Grafana URL with no hand-configuration.
2. **Layer 1:** every running node shows up/down + host health; killing a VM
   turns its tile red within the scrape interval.
3. **Layer 2:** queue depth and channel status are visible for the standalone
   *and* clustered QMs, and **a failover does not lose the MQ view** — the
   client-mode exporter reconnects to the new owner via the VIP. The Pacemaker
   arm shows DRBD replication and resource location. Empty panels (monitoring not
   enabled) are treated as a **failure**, not a pass.
4. **Layer 3:** the DR dashboard names the current QM owner per arm, and a
   deliberate failover/cutover is **watchable** — owner flips, replication lag
   moves — without reading any node's shell.
5. A severed heartbeat/SAN/WAN net during a drill does **not** blind the monitor
   (the `net-mgmt` plane holds).
6. The whole stack is reproducible from scratch and passes `vrg-validate`,
   including a guard that the real `topology.yaml` renders valid scrape targets.
