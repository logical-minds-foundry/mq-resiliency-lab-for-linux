# The Watcher — lab-state dashboard (design)

- **Task:** `mq-resiliency-lab-for-linux#488`, under epic `logical-minds-foundry/.github#21`
- **Status:** design (brainstorm output), pending review
- **Date:** 2026-07-05

## Goal

A single Grafana board — **The Watcher** — that answers *"is the lab itself
healthy, and is something asking for my attention?"* at a glance. It surfaces two
things the existing boards don't:

1. **The instrumentation layer** — the lab's own supporting cast (DNS, observability,
   probe, the SVC counterparty, the app client): everything on the Watcher plane,
   *outside* the SUT we instrument. Nothing watches these today.
2. **A one-line rollup per runnable stack** — every stack's high-level state in one
   place, drilling down to the existing cluster/messaging cockpits for detail.

It is the modern successor to `lab-fleet-node` (the v0 first pass), which a
follow-on task retires once this covers its ground. It follows the model of the
newer boards (`clusterboard`, `messagingboard`): a pure builder, rendered from
`topology.yaml`, provisioned through Grafana.

## The board

Two sections, top-down. Both are built on a **shared column grid** — header and
rows share one `grid-template-columns`, and each metric's value pins to the right
of its cell — so values stack vertically and read as columns (scan *down*, not
across). Density is high on purpose; the encoding was validated against a mockup
with planted anomalies (a red/amber signal must jump out without reading).

### 1 · Support layer — instrument-strip rows

One row per support host: `infra-client`, `infra-svc` (DNS), `obs`, `mon-probe`,
`svc-sim`, `app-client`.

Columns: **status stripe** (= the row's worst signal) · **host** · **role** ·
**defining-service pill** (running/degraded/down) · **CPU** (sparkline + %) ·
**mem** (bar + %, with the **role's one domain metric tucked in the cell's left
slot**) · **uptime**.

The domain metric is role-specific and lives in a fixed position: DNS `⟲ q/s`,
`obs` scrape `targets N/M`, `mon-probe` exporters `N/M`, `svc-sim` queue `depth`,
`app-client` round-trip `rate @ latency`. (It can be promoted to its own aligned
column later if it ever crowds; tucked is the chosen default.)

### 2 · Stacks — triad rows carrying both sites

One row per stack: `pcmk`, `rdqm`, `nhar`, `nhau`.

Columns: **status stripe** · **stack** · **mech** · **Site A (live)** [active QM
host + per-node health dots + `n/N`] · **Site B (DR)** [standby + per-node health
dots + `n/N`] · **flow** (round-trip rate) · **drill ↗**.

Showing *both* sites is deliberate — for a resiliency lab, the live/DR posture is
the point (a bare "3/3" hides which site holds the QM and whether DR is ready).
**SAN folds into the pcmk row's node count** — its storage is part of "is pcmk
healthy"; the SAN nodes are individually inspectable via the future per-server page.

## Architecture

- **`src/mqlab/watcherboard.py`** — a new pure builder (data in → Grafana
  panel/dashboard dicts out, no I/O), mirroring `clusterboard.py`/`messagingboard.py`.
  Curated, lab-shaped order driven by `topology.yaml` (`groups`/`stacks`) — no
  hardcoded host or QM literals; support hosts come from the commons groups, stacks
  from the stack registry. `uid` pinned (`lab-watcher`) so `mqlab obs open` and docs
  can link it.
- **Render:** a `mqlab obs` step writes the board JSON to `build/work/...` (the
  existing render-in-mqlab / copy-in-role pattern used by the other boards).
- **Provision:** the Grafana role installs it alongside the other cockpits.
- **Object-driven, not metric-driven** (per the `dashboard.py` convention): every
  support host and stack renders a row at all times; a missing signal reads as
  "no status" / a coloured absence, the row stays put.

## Data sources & feasibility

| Signal | Source | Status |
|---|---|---|
| Support host up / CPU / mem | `node_exporter` (`up`, `node_cpu_seconds_total`, `node_memory_*`) — scrapes every node incl. infra | ✅ exists |
| Stack: active QM + per-node health, per site | the per-mechanism state collectors (`cluster-state` / `nativeha-state` / `rdqm-state`) — same `cluster_*` metrics the cockpits use | ✅ exists |
| Stack: flow (round-trip) | the app-requester round-trip textfile metric | ✅ exists |
| `mon-probe` exporters up | the MQ-exporter scrape targets (`up{job="ibmmq"}`) | ✅ exists |
| `svc-sim` SVCQM depth | the svc MQ exporter | ✅ exists |
| **Support: defining-service state** (`named`, `prometheus`, `grafana`, exporters running) | `node_exporter` **systemd collector** | ⚠ **needs enabling** |

**The one real addition:** the node-exporter service currently runs only the
textfile collector. The defining-service pills need `--collector.systemd` added to
`ansible/roles/node-exporter` — with a **`--collector.systemd.unit-include` filter**
(only the units we surface: `named`, `prometheus`, `grafana-server`, the
`node_exporter`/`mq_prometheus` units) to keep metric cardinality bounded. This is
a small, self-contained role change and part of this task.

## Drill-down

Each stack row's `↗` links to that stack's existing cockpit (`clusterboard` /
`messagingboard`). The **generic, hostname-parameterized per-server page** — where
a support row would eventually drill to a robust single-host health view with
per-host configurable "services of interest" — is a **follow-on task**, not this one.

## Out of scope (follow-on tasks, each brainstorm-first)

1. **Generic per-server drill-down page** — one reusable page taking a hostname,
   showing robust per-host health, with per-host configurable services of interest.
2. **Retire `lab-fleet-node`** — once the Watcher covers its ground.

## Testing

- `watcherboard.py` is a pure builder → unit-tested to the repo's 100%-branch gate,
  like `clusterboard`/`messagingboard`: assert the emitted panel/row structure,
  the curated order, and that it renders against the **real** `topology.yaml`
  (a `test_topology_integrity`-style smoke test — the board renders, `uid` pinned,
  every support host + stack row present).
- The node-exporter systemd-collector change is covered by `ansible-lint` +
  `vrg-validate`; live scrape of the new `node_systemd_unit_state` series is proven
  by the cold-reboot/observe pass.
