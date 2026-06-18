# Cluster Cockpit — PR1: matrix framework + board — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a dedicated, provisioned `lab-pcmk-cluster` Grafana board whose heart is
the node×component **Table** matrix (② Compute + ③ Storage), rendered from code by a new
`clusterboard.py`, reproducing the frozen spike contract.

**Architecture:** A new pure-function module `src/mqlab/clusterboard.py` builds Grafana
panel JSON (no I/O). `matrix(...)` emits a Table panel = one normalized query per column +
`joinByField` + `organize` + per-column colour overrides (data-driven rows). The shared
`fold_side`/`active_side` state machines (§6.8) land here for later PRs. `render_cluster_dashboard(topo, arm)`
assembles the ②③ matrices onto a board with stable `uid: lab-pcmk-cluster`; the CLI renders
it to `build/grafana/dashboards/lab-pcmk-cluster.json` and the `grafana` role deploys it.

**Tech Stack:** Python 3.11+ (Typer CLI, pure render functions), Grafana v13 Table panel +
transformations, Prometheus `cluster_*` series (#195 collector), Ansible `grafana` role.

## Global Constraints

- **Frozen contract (spike, §4.1):** engine = **Table**; the panel shape is
  `docs/specs/diagrams/cluster-matrix-table-contract.json`; the metric→cell recipe is
  `docs/specs/cluster-matrix-recipe.md`. `matrix()` must reproduce that shape.
- **Row-key normalization:** every column query ends `max by (n)(label_replace(<series>, "n","$1","<node|member|holder>","(.*)"))`.
- **Colour vocabulary (2026-06-14 §4.3):** green healthy · amber transitional · red down/failed · grey n/a · STALE hatched. Precedence STALE > red > amber > green.
- **No timing claims** (TCG): replication is out-of-sync **bytes** + resync %, never RPO seconds.
- **Validation:** `vrg-container-run -- vrg-validate` is the only gate; 100% branch coverage required.
- **Pure functions only** in `clusterboard.py` (data in → dict out, no I/O), like `dashboard.py`.
- **Stable datasource uid:** the board references Prometheus by a pinned uid (Task 5), not the
  per-instance `PBFA…` uid the spike used.

---

## Task 1: `clusterboard.py` — `matrix()` Table builder

**Files:**
- Create: `src/mqlab/clusterboard.py`
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Produces: `Column = tuple[str, str, str]` *(title, promql, mapping_kind)*;
  `matrix(title: str, columns: list[Column], ds_uid: str, y: int) -> dict[str, Any]` returns
  a Grafana Table panel dict.
- Mapping kinds: `"up"` (1→green/0→red), `"clean0"` (0→green/≥1→red), `"sessions"` (≥1→green/0→red).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clusterboard.py
from mqlab.clusterboard import matrix

DS = "promtest"

def test_matrix_is_a_joined_colourised_table():
    cols = [
        ("corosync", 'max by (n)(label_replace(cluster_daemon_up{unit="corosync"},"n","$1","node","(.*)"))', "up"),
        ("fence", 'max by (n)(label_replace(cluster_fence_count,"n","$1","member","(.*)"))', "clean0"),
    ]
    p = matrix("② Compute", cols, DS, y=0)
    assert p["type"] == "table"
    assert p["title"] == "② Compute"
    # one instant table-format target per column, all on the pinned datasource
    assert [t["refId"] for t in p["targets"]] == ["A", "B"]
    assert all(t["format"] == "table" and t["instant"] for t in p["targets"])
    assert all(t["datasource"] == {"type": "prometheus", "uid": DS} for t in p["targets"])
    assert p["targets"][0]["expr"].startswith("max by (n)(label_replace(cluster_daemon_up")
    # join on n, then rename Value #<ref> -> column title, n -> node, drop Time
    tids = [t["id"] for t in p["transformations"]]
    assert tids == ["joinByField", "organize"]
    org = p["transformations"][1]["options"]
    assert org["renameByName"] == {"Value #A": "corosync", "Value #B": "fence", "n": "node"}
    assert org["excludeByName"] == {"Time": True}
    # one colour-background override per column, with the right mapping family
    ov = {o["matcher"]["options"]: o for o in p["fieldConfig"]["overrides"]}
    assert set(ov) == {"corosync", "fence"}
    cell = next(pr for pr in ov["corosync"]["properties"] if pr["id"] == "custom.cellOptions")
    assert cell["value"] == {"type": "color-background", "mode": "basic"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.clusterboard'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/mqlab/clusterboard.py
"""Pure builders for the cluster-cockpit board (lab-pcmk-cluster). Data in → Grafana
panel/dashboard dicts out, no I/O — mirrors dashboard.py. Engine = Table (spike §4.1)."""

from __future__ import annotations

from typing import Any

Column = tuple[str, str, str]  # (title, promql, mapping_kind)

_GREEN, _RED = "green", "red"
_MAPPINGS: dict[str, list[dict[str, Any]]] = {
    "up": [{"type": "value", "options": {
        "0": {"color": _RED, "text": "down", "index": 0},
        "1": {"color": _GREEN, "text": "up", "index": 1}}}],
    "clean0": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {"type": "range", "options": {"from": 1, "to": 9999,
            "result": {"color": _RED, "text": "!", "index": 1}}}],
    "sessions": [
        {"type": "value", "options": {"0": {"color": _RED, "text": "none", "index": 0}}},
        {"type": "range", "options": {"from": 1, "to": 9999,
            "result": {"color": _GREEN, "text": "ok", "index": 1}}}],
}
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _ds(uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": uid}


def matrix(title: str, columns: list[Column], ds_uid: str, y: int) -> dict[str, Any]:
    targets, rename, overrides = [], {"n": "node"}, []
    for i, (col_title, expr, kind) in enumerate(columns):
        ref = _REFIDS[i]
        targets.append({"refId": ref, "expr": expr, "format": "table",
                        "instant": True, "datasource": _ds(ds_uid)})
        rename[f"Value #{ref}"] = col_title
        overrides.append({"matcher": {"id": "byName", "options": col_title}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
            {"id": "mappings", "value": _MAPPINGS[kind]},
            {"id": "color", "value": {"mode": "fixed"}}]})
    return {
        "type": "table", "title": title, "datasource": _ds(ds_uid),
        "gridPos": {"h": 9, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "n", "mode": "outer"}},
            {"id": "organize", "options": {"renameByName": rename, "excludeByName": {"Time": True}}}],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}}, "overrides": overrides},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py
vrg-commit --type feat --scope obs --message "clusterboard.matrix() — Table matrix builder (#219)"
```

---

## Task 2: `fold_side` + `active_side` state machines (§6.8)

**Files:**
- Modify: `src/mqlab/clusterboard.py`
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Produces: `fold_side(cells: list[str]) -> str` → one of `"green"|"amber"|"red"|"STALE"`,
  precedence STALE > red > amber > green. `active_side(owner_sites: list[str]) -> str` →
  `"A"|"B"|"none"|"split"` (input = the set of site letters currently holding `mq_*` owners).

- [ ] **Step 1: Write the failing test**

```python
from mqlab.clusterboard import fold_side, active_side

def test_fold_side_precedence():
    assert fold_side(["green", "green"]) == "green"
    assert fold_side(["green", "amber"]) == "amber"
    assert fold_side(["amber", "red"]) == "red"
    assert fold_side(["red", "STALE"]) == "STALE"      # STALE outranks red
    assert fold_side([]) == "STALE"                     # no cells = blind = STALE

def test_active_side_states():
    assert active_side(["A"]) == "A"
    assert active_side(["B"]) == "B"
    assert active_side([]) == "none"                    # nobody owns it mid-transition
    assert active_side(["A", "B"]) == "split"           # dual owner = hazard
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -k "fold_side or active_side" -q`
Expected: FAIL — `ImportError: cannot import name 'fold_side'`.

- [ ] **Step 3: Write minimal implementation** (append to `clusterboard.py`)

```python
_PRECEDENCE = ("STALE", "red", "amber", "green")


def fold_side(cells: list[str]) -> str:
    if not cells:
        return "STALE"
    for level in _PRECEDENCE:          # worst-wins, STALE first
        if level in cells:
            return level
    return "green"


def active_side(owner_sites: list[str]) -> str:
    sites = set(owner_sites)
    if len(sites) > 1:
        return "split"
    if not sites:
        return "none"
    return sites.pop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -k "fold_side or active_side" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py
vrg-commit --type feat --scope obs --message "clusterboard: fold_side/active_side state machines (#219)"
```

---

## Task 3: `render_cluster_dashboard(topo, arm)` — assemble the board

**Files:**
- Modify: `src/mqlab/clusterboard.py`
- Test: `tests/test_clusterboard.py`

**Interfaces:**
- Consumes: `matrix()` (Task 1).
- Produces: `render_cluster_dashboard(topo: dict, arm: str = "pcmk", ds_uid: str = "prometheus") -> dict`
  → a Grafana dashboard dict with `uid="lab-pcmk-cluster"` and panels = [② Compute matrix,
  ③ Storage matrix]. Compute columns: corosync, pacemaker, iSCSI, fence, online, unclean.
  Storage columns: resync %, out-of-sync (bytes). (① cluster-status cards, hero, timeline,
  logs are later PRs.)

- [ ] **Step 1: Write the failing test**

```python
from mqlab.clusterboard import render_cluster_dashboard

def test_board_has_uid_and_the_two_matrices():
    d = render_cluster_dashboard({}, arm="pcmk")
    assert d["uid"] == "lab-pcmk-cluster"
    titles = [p["title"] for p in d["panels"]]
    assert titles == ["② Compute — node × component", "③ Storage — DRBD / SAN"]
    compute = d["panels"][0]
    cols = compute["transformations"][1]["options"]["renameByName"]
    assert {"Value #A", "Value #D"} <= set(cols)            # corosync .. fence present
    # storage matrix sits below compute (no overlap)
    assert d["panels"][1]["gridPos"]["y"] > compute["gridPos"]["y"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -k board -q`
Expected: FAIL — `ImportError: cannot import name 'render_cluster_dashboard'`.

- [ ] **Step 3: Write minimal implementation** (append to `clusterboard.py`)

```python
def _norm(series: str, label: str) -> str:
    return f'max by (n)(label_replace({series},"n","$1","{label}","(.*)"))'


_COMPUTE_COLS: list[Column] = [
    ("corosync", _norm('cluster_daemon_up{unit="corosync"}', "node"), "up"),
    ("pacemaker", _norm('cluster_daemon_up{unit="pacemaker"}', "node"), "up"),
    ("iSCSI", _norm("cluster_iscsi_sessions", "node"), "sessions"),
    ("fence", _norm("cluster_fence_count", "member"), "clean0"),
    ("online", _norm("cluster_node_online", "member"), "up"),
    ("unclean", _norm("cluster_node_unclean", "member"), "clean0"),
]
_STORAGE_COLS: list[Column] = [
    ("resync %", _norm('cluster_drbd_resync_pct', "node"), "sessions"),
    ("out-of-sync", _norm("cluster_drbd_out_of_sync_bytes", "node"), "clean0"),
]


def render_cluster_dashboard(topo: dict[str, Any], arm: str = "pcmk", ds_uid: str = "prometheus") -> dict[str, Any]:
    panels = [
        matrix("② Compute — node × component", _COMPUTE_COLS, ds_uid, y=0),
        matrix("③ Storage — DRBD / SAN", _STORAGE_COLS, ds_uid, y=9),
    ]
    return {
        "uid": "lab-pcmk-cluster", "title": "PCMK Cluster · Infrastructure View",
        "schemaVersion": 39, "version": 0, "panels": panels,
        "time": {"from": "now-15m", "to": "now"}, "refresh": "10s",
        "tags": ["lab", "cockpit", arm],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_clusterboard.py -k board -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterboard.py tests/test_clusterboard.py
vrg-commit --type feat --scope obs --message "clusterboard.render_cluster_dashboard — lab-pcmk-cluster board (#219)"
```

---

## Task 4: CLI wiring — render the second board

**Files:**
- Modify: `src/mqlab/clusterboard.py` (add `cluster_dashboard_path()` + `lab_cluster_dashboard()`)
- Modify: `src/mqlab/cli.py` (the `obs dashboard` command + `_obs_up_steps` render block)
- Test: `tests/test_cli_obs.py`

**Interfaces:**
- Consumes: `render_cluster_dashboard` (Task 3), `repo_root` (`mqlab.paths`), `lab_topology`.
- Produces: `cluster_dashboard_path() -> Path` = `build/grafana/dashboards/lab-pcmk-cluster.json`;
  `lab_cluster_dashboard() -> str` (JSON text). `obs up` and `obs dashboard` write both
  `lab-status.json` (existing) and `lab-pcmk-cluster.json` (new).

- [ ] **Step 1: Write the failing test** (extend the existing obs-up test family)

```python
# tests/test_cli_obs.py — new test
def test_obs_up_also_renders_the_cockpit_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["ok"]) for _ in range(6)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["obs", "up"])
    assert result.exit_code == 0
    board = tmp_path / "build" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"
    assert board.exists()
    import json
    assert json.loads(board.read_text())["uid"] == "lab-pcmk-cluster"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_cli_obs.py -k cockpit_board -q`
Expected: FAIL — the file is not written.

- [ ] **Step 3: Write minimal implementation**

Append to `src/mqlab/clusterboard.py`:

```python
import json
from pathlib import Path

from mqlab.paths import repo_root


def cluster_dashboard_path() -> Path:
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"


def lab_cluster_dashboard() -> str:
    from mqlab.topology import lab_topology
    return json.dumps(render_cluster_dashboard(lab_topology()), indent=2) + "\n"
```

In `src/mqlab/cli.py` `_obs_up_steps()`, beside the existing dashboard render (after
`dash.write_text(lab_dashboard())`), add:

```python
    from mqlab.clusterboard import cluster_dashboard_path, lab_cluster_dashboard
    cockpit = cluster_dashboard_path()
    cockpit.parent.mkdir(parents=True, exist_ok=True)
    cockpit.write_text(lab_cluster_dashboard())
```

And in `obs_dashboard()` (the `obs dashboard` command), write the cockpit board too, the
same way it writes `lab-status.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_cli_obs.py -k cockpit_board -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/clusterboard.py src/mqlab/cli.py tests/test_cli_obs.py
vrg-commit --type feat --scope obs --message "obs: render lab-pcmk-cluster.json alongside lab-status (#219)"
```

---

## Task 5: Provisioning — deploy the board + pin the Prometheus datasource uid

**Files:**
- Modify: `ansible/roles/grafana/tasks/main.yml` (second dashboard copy)
- Modify: `ansible/roles/grafana/templates/datasource.yml.j2` (pin `uid: prometheus`)

**Interfaces:**
- Consumes: `build/grafana/dashboards/lab-pcmk-cluster.json` (Task 4).
- Produces: the board served at `/var/lib/grafana/dashboards/lab-pcmk-cluster.json`, resolving
  the `prometheus` datasource uid that `render_cluster_dashboard` references.

- [ ] **Step 1: Pin the Prometheus datasource uid.** In `datasource.yml.j2`, ensure the
  Prometheus datasource declares `uid: prometheus` (mirroring the pinned `uid: loki`), so the
  rendered board's `{type: prometheus, uid: prometheus}` references resolve. If it already has
  a stable uid, set `ds_uid` defaults to match instead.

- [ ] **Step 2: Add the deploy task.** In `grafana/tasks/main.yml`, after the existing
  "deploy the rendered lab dashboard" copy, add:

```yaml
- name: deploy the rendered cockpit dashboard
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/grafana/dashboards/lab-pcmk-cluster.json"
    dest: /var/lib/grafana/dashboards/lab-pcmk-cluster.json
    mode: "0644"
  notify: restart grafana
```

- [ ] **Step 3: Offline gate.**

Run: `cd .worktrees/issue-219-cluster-cockpit && vrg-container-run -- vrg-validate`
Expected: green (yamllint + 100% branch tests).

- [ ] **Step 4: Live acceptance (load-bearing — needs the live `pcmk_san_dr` cluster).**
  `mqlab obs up` (re-renders + deploys both boards). Browse `localhost:3000/d/lab-pcmk-cluster`:
  the ② Compute matrix shows all six nodes green (corosync/pacemaker up, fence clean, online),
  and ③ Storage populates once DRBD is up. Confirms the generated board renders the live series
  the spike validated.

- [ ] **Step 5: Commit**

```bash
vrg-git add ansible/roles/grafana/tasks/main.yml ansible/roles/grafana/templates/datasource.yml.j2
vrg-commit --type feat --scope obs --message "grafana: provision the lab-pcmk-cluster board + pin prometheus ds uid (#219)"
```

---

## What PR1 deliberately leaves to follow-on plans (spec §8)

- **PR2** Hero tiles + first-class integrity light (uses `fold_side`/`active_side` from Task 2).
- **PR3** Failover-story timeline band (State-timeline + holder-agnostic annotations, §6.4).
- **PR4** Embedded Loki log row.
- **PR5** Perf + network sections.
- **PR6** Overview roll-up + drill-link on `lab-fleet-node` (the one shared-surface PR — sequence last, rebase-on-merge).
- **① Cluster-status cards** (per-site quorum/owner cards + cross-site DRBD card) — folds into PR2/PR3 once the hero/integrity primitives exist.

## Self-review

- **Spec coverage:** PR1 implements §3 (`clusterboard.py` builders, `render_cluster_dashboard`),
  §3.1 (second provisioned board), §3.2-shape (`matrix()` is arm-agnostic — takes columns), §4
  (Table engine, reproduces the frozen contract), §5 (② + ③ of the layout), §6.2 (the two
  matrix sections), §6.8 (`fold_side`/`active_side`). Hero/timeline/logs/perf/roll-up are
  explicitly deferred to PR2–6 (listed above) — no silent gaps.
- **Placeholder scan:** none — every code step shows complete code; commands are exact.
- **Type consistency:** `Column = (title, promql, mapping_kind)` used consistently in Tasks 1
  and 3; `matrix(title, columns, ds_uid, y)`, `fold_side(list)->str`, `active_side(list)->str`,
  `render_cluster_dashboard(topo, arm, ds_uid)`, `cluster_dashboard_path`/`lab_cluster_dashboard`
  consistent across tasks. `ds_uid` default `"prometheus"` matches the Task 5 pin.
- **Coverage note:** the `_REFIDS`/range-mapping branches and all `fold_side`/`active_side`
  states (incl. `none`/`split`/`STALE`/empty) are exercised by Tasks 1–3 tests → 100% branch.
