# The Watcher dashboard — implementation plan

> **Execution:** built inline in the #488 session (single task/PR). TDD per task;
> `vrg-container-run -- vrg-validate` is the gate. Right-sized (not per-line
> transcription) because the author implements it directly — steps name the
> concrete files, interfaces, and test assertions.

**Goal:** A Grafana board (`uid: lab-watcher`) showing the lab's own
instrumentation layer (support hosts) and a both-sites rollup per stack.

**Architecture:** A pure builder `src/mqlab/watcherboard.py` (data → Grafana dicts,
no I/O), mirroring `clusterboard.py`/`messagingboard.py`; rendered from
`topology.yaml` via a `mqlab obs` step and provisioned by the Grafana role. One
enabling change: node-exporter gains a unit-filtered systemd collector.

**Tech Stack:** Python (mqlab, pytest, 100% branch gate), Grafana JSON model,
Prometheus (`node_exporter` + the per-mechanism state collectors), Ansible.

## Global Constraints

- **No hardcoded host/QM literals** — support hosts come from the commons groups,
  stacks from the stack registry; QM names derive from `short` (#351). (Mirrors
  `dashboard.py`/`clusterboard.py`.)
- **Object-driven, not metric-driven** — every support host + stack renders a row
  at all times; a missing signal reads as "no status", the row stays put.
- **`uid` pinned** = `lab-watcher`.
- **Pure builder** — `watcherboard.py` does no I/O; the CLI does the file write.
- **100% branch coverage** (repo gate); `vrg-validate` is the only validation.

---

### Task 1: node-exporter systemd collector (the service-pill metric source)

**Files:**
- Modify: `ansible/roles/node-exporter/templates/node_exporter.service.j2` — add
  `--collector.systemd` and `--collector.systemd.unit-include` limited to the units
  the board surfaces (`named`, `bind9`, `prometheus`, `grafana-server`,
  `node_exporter`, and the `mq_prometheus*`/exporter units), so cardinality stays
  bounded. Regex form, e.g.
  `--collector.systemd.unit-include=(named|bind9|prometheus|grafana-server|node_exporter|mq_prometheus.*)\.service`.

**Deliverable:** `node_systemd_unit_state{name="named.service",state="active"}`-style
series are exported (proven at the observe/cold-reboot pass — a live-scrape item).

**Test:** `ansible-lint`/`vrg-validate` green; no Python change, so no unit test.

---

### Task 2: `watcherboard.py` — the pure builder

**Files:**
- Create: `src/mqlab/watcherboard.py`
- Test: `tests/test_watcherboard.py`

**Interfaces (produces):**
- `DASHBOARD_UID = "lab-watcher"`
- `lab_watcher_dashboard() -> str` — the real-topology entry (reads
  `lab/topology.yaml`, returns Grafana JSON text), mirroring `lab_cluster_dashboard`.
- `build_watcher(topo: dict) -> dict` — pure: topology dict → Grafana dashboard dict.
- Internal builders: `_support_rows(topo) -> list[panel]` and
  `_stack_rows(topo) -> list[panel]`, plus small panel helpers mirroring
  `clusterboard.py`'s (`_row`, stat/table/timeseries panel builders).

**Design decisions (from the spec):**
- **Support section:** one row per commons support host (`infra-client`,
  `infra-svc`, `obs`, `mon-probe`, `svc-sim`, `app-client`) — driven off the
  `infra`/`obs_box`/`probe`/`svc`/`app` groups, not literals. Cells: up
  (`up{job="node"}`), defining-service (`node_systemd_unit_state`), CPU
  (`node_cpu_seconds_total` idle→busy), mem (`node_memory_*`), the role's domain
  metric, uptime (`node_time_seconds - node_boot_time_seconds`). Grafana engine:
  **Table** (like `clusterboard`, spike §4.1) — a table row per host reads as the
  aligned instrument strip.
- **Stacks section:** one row per stack from `lab_stacks()`. Site-A/Site-B active-QM
  + per-node health from the `cluster_*` metrics the cockpits emit; flow from the
  app round-trip metric. Drill link → the stack's existing cockpit `uid`.
- **SAN** folds into the pcmk row's node set (its `san_a`/`san_b` groups count
  toward pcmk health) — display grouping only, like `dashboard.py`'s ROWS note.

**Test approach (100% branch):**
- Fixture-topology tests: `build_watcher(fixture)` emits the expected panels —
  a support row per support host, a stack row per stack, `uid == "lab-watcher"`,
  the curated order, and the object-driven "row present even with no metric" rule.
- Error/edge branches (empty group, missing stack) covered by fixtures.
- **Real-topology smoke** (a `test_topology_integrity`-style test):
  `json.loads(lab_watcher_dashboard())` renders, `uid` pinned, and every real
  support host + stack (pcmk/rdqm/nhar/nhau) has a row.

---

### Task 3: render + provision + wire-in

**Files:**
- Modify: `src/mqlab/cli.py` — in the obs dashboard render (where
  `lab_cluster_dashboard()` etc. are written), add the Watcher: write
  `lab_watcher_dashboard()` to its `build/work` path (add a `watcher_dashboard_path()`
  next to the other `*_dashboard_path()` helpers).
- Modify: the Grafana provisioning (the role/tasks that copy the cockpit JSONs) —
  include the Watcher board alongside the others.
- Modify: `src/mqlab/cli.py` `obs open` and any docs default → point at
  `lab-watcher` (the Watcher becomes the front door).

**Test:** extend the existing `test_cli_obs` dashboard-render test to assert the
Watcher JSON is written (`uid == "lab-watcher"`); `vrg-validate` green.

**Cold-reboot / observe validation (operator):** the board renders in Grafana,
the support rows light up (incl. the new `node_systemd_unit_state` pills), and the
stack rows show the live/DR posture.

## Self-review

- **Spec coverage:** support section (T2) · stacks section (T2) · systemd collector
  (T1) · render+provision+drill (T3) · testing (each task). All spec sections map.
- **No placeholders**; interfaces named; `uid`/function names consistent across tasks.
- **Follow-ons** (#499 per-server page, #500 retire fleet-node) are out of scope here.
