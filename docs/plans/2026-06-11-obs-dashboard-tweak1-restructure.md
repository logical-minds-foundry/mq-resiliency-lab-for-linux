# Observability Dashboard — Tweak 1: Layered Restructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the observability dashboard into a layered, lab-shaped view — a reserved MQ row on top, VM groups in curated order (each with up/down tiles + a per-group CPU graph) in the middle, a placeholder network strip at the bottom — rendered from topology, not hand-authored.

**Architecture:** Replace the static `fleet-node.json` with a pure `render_dashboard()` function (sibling of `render_scrape_targets`): a curated `ROWS` list (lab order) × the topology `groups` namespace → Grafana dashboard JSON, `uid` preserved as `lab-fleet-node`. An `mqlab obs dashboard` verb renders it to `build/`; `mqlab obs up` renders it before provisioning; the grafana role deploys the rendered file (same pattern prometheus uses for targets). Uses only existing metrics (`up`, `node_cpu_seconds_total`) — no new telemetry.

**Tech Stack:** Python 3.12, Grafana dashboard JSON (schema v39), Ansible, pytest.

---

## Scope & boundaries

**In scope:** `render_dashboard()` + `mqlab obs dashboard` + wiring it through `obs up` and the grafana role; the layered/grouped layout; preserve `uid: lab-fleet-node`.

**Out of scope (Tweak 2, separate issue/PR):** the real network panel and its telemetry (host net-state collector, reachability collector, `render_scrape_targets` host target). Tweak 1 leaves a **placeholder** network row that Tweak 2 replaces. The **MQ row** stays a reserved placeholder (Plan B).

**Key facts (verified on this branch, off `develop` @ #107):**
- The `groups` file_sd label rides *every* series from a target (set in `render_scrape_targets`), so `up{groups=~"pcmk_a"}` and `node_cpu_seconds_total{groups=~"pcmk_a"}` both work.
- Every topology node is in exactly one atomic group, so a regex selector per row is unambiguous.
- The grafana role deploys a dashboard file to `/var/lib/grafana/dashboards/`; prometheus role already copies a *rendered* file from `build/` — Tweak 1 makes grafana do the same.

---

### Task 1: The `render_dashboard` renderer (pure, fail-loud)

**Files:**
- Create: `src/mqlab/dashboard.py`
- Test: `tests/test_dashboard.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard.py`:

```python
from __future__ import annotations

import pytest

from mqlab.dashboard import DASHBOARD_UID, DashboardError, ROWS, render_dashboard

TOPO = {
    "groups": {
        "san_a": ["san-a"], "san_b": ["san-b"],
        "pcmk_a": ["pcmk-a1"], "pcmk_b": ["pcmk-b1"],
        "rdqm_a": ["rdqm-a1"], "rdqm_b": ["rdqm-b1"],
        "qm": ["qm-main"], "svc": ["svc-sim"], "client": ["app-client"],
        "obs_box": ["obs"], "probe": ["mon-probe"],
    }
}


def test_uid_is_preserved():
    assert render_dashboard(TOPO)["uid"] == "lab-fleet-node" == DASHBOARD_UID


def test_has_a_row_header_per_curated_row_in_order():
    panels = render_dashboard(TOPO)["panels"]
    row_titles = [p["title"] for p in panels if p["type"] == "row"]
    assert row_titles == [
        "MQ Service — reserved · Layer 2",
        "VMs · SAN", "VMs · PCMK · A", "VMs · PCMK · B",
        "VMs · RDQM · A", "VMs · RDQM · B",
        "VMs · Standalone", "VMs · Observability",
        "Networks",
    ]


def test_group_rows_filter_by_their_groups_selector():
    panels = render_dashboard(TOPO)["panels"]
    exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
    # SAN row rolls up both san groups; PCMK-A rolls up just pcmk_a
    assert any('up{job="node", groups=~"san_a|san_b"}' == e for e in exprs)
    assert any('groups=~"pcmk_a"' in e and e.startswith("100 - ") for e in exprs)


def test_unknown_curated_group_fails_loud():
    topo = {"groups": {"san_a": ["san-a"]}}  # ROWS references many groups not here
    with pytest.raises(DashboardError, match="unknown group in ROWS"):
        render_dashboard(topo)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.dashboard'`.

- [ ] **Step 3: Write the implementation**

Create `src/mqlab/dashboard.py`:

```python
"""Render the Grafana dashboard as a pure function of the curated layout + topology (#108).

Sibling of scrape.py / inventory.py: one source of truth. A curated ROWS list
fixes the lab-shaped order (SAN, the PCMK/RDQM arms split A/B, standalone,
observability); the topology `groups` namespace validates it. Emits a layered
Grafana dashboard — reserved MQ row on top, grouped VM rows (up/down + per-group
CPU) in the middle, a placeholder network row at the bottom (Tweak 2 replaces
it). uid is pinned so mqlab obs open / the docs deep-links keep working.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_UID = "lab-fleet-node"  # pinned — referenced by mqlab obs open + docs

# Curated, lab-shaped order. Each row rolls up one-or-more atomic groups; SAN
# pairs both site SANs, the cluster arms split by site.
ROWS: list[tuple[str, list[str]]] = [
    ("SAN", ["san_a", "san_b"]),
    ("PCMK · A", ["pcmk_a"]),
    ("PCMK · B", ["pcmk_b"]),
    ("RDQM · A", ["rdqm_a"]),
    ("RDQM · B", ["rdqm_b"]),
    ("Standalone", ["qm", "svc", "client"]),
    ("Observability", ["obs_box", "probe"]),
]


class DashboardError(RuntimeError):
    """The curated layout cannot be rendered against the topology."""


def _row(title: str, y: int) -> dict[str, Any]:
    return {"type": "row", "title": title, "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []}


def _text(content: str, y: int) -> dict[str, Any]:
    return {
        "type": "text",
        "gridPos": {"h": 3, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def _up_panel(label: str, sel: str, y: int) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": f"{label} — up",
        "gridPos": {"h": 4, "w": 10, "x": 0, "y": y},
        "fieldConfig": {"defaults": {"mappings": [
            {"type": "value", "options": {
                "0": {"text": "DOWN", "color": "red"},
                "1": {"text": "UP", "color": "green"},
            }}
        ]}},
        "targets": [{"expr": f'up{{job="node", groups=~"{sel}"}}', "legendFormat": "{{host}}"}],
    }


def _cpu_panel(label: str, sel: str, y: int) -> dict[str, Any]:
    expr = (
        "100 - (avg by (host) "
        f'(rate(node_cpu_seconds_total{{mode="idle", groups=~"{sel}"}}[1m])) * 100)'
    )
    return {
        "type": "timeseries",
        "title": f"{label} — CPU busy %",
        "gridPos": {"h": 4, "w": 14, "x": 10, "y": y},
        "targets": [{"expr": expr, "legendFormat": "{{host}}"}],
    }


def render_dashboard(topo: dict[str, Any]) -> dict[str, Any]:
    """Project the curated ROWS + topology groups -> a Grafana dashboard dict."""
    known = set(topo.get("groups", {}))
    panels: list[dict[str, Any]] = []
    y = 0

    panels.append(_row("MQ Service — reserved · Layer 2", y)); y += 1
    panels.append(_text("Queue-manager owner · depth · channel status arrive in **Layer 2**.", y)); y += 3

    for label, groups in ROWS:
        for g in groups:
            if g not in known:
                raise DashboardError(f"unknown group in ROWS: {g}")
        sel = "|".join(groups)
        panels.append(_row(f"VMs · {label}", y)); y += 1
        panels.append(_up_panel(label, sel, y))
        panels.append(_cpu_panel(label, sel, y)); y += 4

    panels.append(_row("Networks", y)); y += 1
    panels.append(_text("Network status arrives in **Tweak 2** (#108 follow-up).", y)); y += 3

    return {
        "title": "Lab — Layered Status",
        "uid": DASHBOARD_UID,
        "schemaVersion": 39,
        "version": 1,
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "panels": panels,
    }


def dashboard_path() -> Path:
    """Where the rendered dashboard is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-status.json"


def lab_dashboard() -> str:
    """Render the real lab/topology.yaml to dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_dashboard(topo), indent=2) + "\n"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_dashboard.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "render the Grafana dashboard from curated layout + topology (#108)" --body "Pure render_dashboard(topo): curated ROWS (lab order) x topology groups -> layered Grafana JSON (reserved MQ row, grouped VM rows with up/down + per-group CPU, placeholder network row). uid pinned to lab-fleet-node. Fail-loud on an unknown group. Adds dashboard_path()/lab_dashboard()."
```

---

### Task 2: Guard the real topology renders a valid dashboard

**Files:**
- Modify: `tests/test_topology_integrity.py`

- [ ] **Step 1: Add the guard test**

Append to `tests/test_topology_integrity.py`:

```python
def test_real_topology_renders_a_valid_dashboard():
    import json

    from mqlab.dashboard import DASHBOARD_UID, lab_dashboard

    dash = json.loads(lab_dashboard())  # raises DashboardError on an unknown ROWS group
    assert dash["uid"] == DASHBOARD_UID
    row_titles = [p["title"] for p in dash["panels"] if p["type"] == "row"]
    # every curated VM row is present against the real groups
    assert "VMs · SAN" in row_titles and "VMs · RDQM · B" in row_titles
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_topology_integrity.py -v`
Expected: PASS — the real topology's groups satisfy every `ROWS` entry.

- [ ] **Step 3: Commit**

```bash
vrg-commit --type test --scope lab --message "guard the real topology renders a valid dashboard (#108)" --body "CI catches a ROWS group that no longer exists in topology (would raise DashboardError), mirroring the inventory/scrape guards."
```

---

### Task 3: `mqlab obs dashboard` — render the dashboard file (CLI, TDD)

**Files:**
- Modify: `src/mqlab/cli.py`
- Test: `tests/test_cli_obs.py`

This mirrors `obs targets` (cli.py): render to the gitignored `build/` tree and echo a confirmation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_obs.py`:

```python
def test_obs_dashboard_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)  # defined earlier in this file
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "grafana" / "dashboards" / "lab-status.json"
    assert json.loads(written.read_text())["uid"] == "lab-fleet-node"
```

The `_seed_monitoring` helper (already in `tests/test_cli_obs.py`) writes a topology with `obs_box`/`probe` groups; extend it to include the groups `ROWS` needs. **Update `_seed_monitoring`** so its `groups:` block covers every `ROWS` group:

```python
def _seed_monitoring(tmp_path):
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "  rdqm_a: [rdqm-a1]\n  rdqm_b: [rdqm-b1]\n"
        "  qm: [qm-main]\n  svc: [svc-sim]\n  client: [app-client]\n"
        "  obs_box: [obs]\n  probe: [mon-probe]\n"
        "setups:\n"
        "  monitoring:\n    groups: [obs_box, probe]\n"
    )
```

(The existing `obs targets`/`obs up`/`obs status` tests still pass against this richer topology — they only assert on `obs`/`mon-probe`.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli_obs.py::test_obs_dashboard_writes_file_from_topology -v`
Expected: FAIL — `No such command 'dashboard'`.

- [ ] **Step 3: Add the command**

In `src/mqlab/cli.py`, after the `obs_targets` command, add:

```python
@obs_app.command("dashboard")
def obs_dashboard() -> None:
    """Render build/grafana/dashboards/lab-status.json from topology and echo it."""
    from mqlab.dashboard import dashboard_path, lab_dashboard

    deps = build_deps("obs-dashboard", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_dashboard()
        path = dashboard_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        deps.transcript.write(f"render -> {path}")
    finally:
        deps.transcript.close()
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cli_obs.py -v`
Expected: PASS (all obs CLI tests, including the new one).

- [ ] **Step 5: Commit**

```bash
vrg-commit --type feat --scope mqlab --message "add mqlab obs dashboard (#108)" --body "Render build/grafana/dashboards/lab-status.json from topology and echo it, mirroring mqlab obs targets."
```

---

### Task 4: `obs up` renders the dashboard; grafana role deploys the rendered file

**Files:**
- Modify: `src/mqlab/cli.py` (`_obs_up_steps`)
- Test: `tests/test_cli_obs.py`
- Modify: `ansible/roles/grafana/tasks/main.yml`
- Delete: `ansible/roles/grafana/files/dashboards/fleet-node.json`

- [ ] **Step 1: Update the obs-up test to expect the dashboard render**

In `tests/test_cli_obs.py`, in `test_obs_up_renders_then_creates_then_provisions`, after the inventory-exists assertion add:

```python
    assert (tmp_path / "build" / "grafana" / "dashboards" / "lab-status.json").exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli_obs.py::test_obs_up_renders_then_creates_then_provisions -v`
Expected: FAIL — the dashboard file isn't rendered yet.

- [ ] **Step 3: Render the dashboard eagerly in `_obs_up_steps`**

In `src/mqlab/cli.py`, in `_obs_up_steps`, extend the eager-render block (which already renders targets + inventory) to also render the dashboard:

```python
    from mqlab.dashboard import dashboard_path, lab_dashboard

    dash = dashboard_path()
    dash.parent.mkdir(parents=True, exist_ok=True)
    dash.write_text(lab_dashboard())
```

and update the first step's echo to mention it:

```python
        CommandStep(
            "render targets + inventory + dashboard",
            Command(["echo", f"rendered -> {targets}, {inv}, {dash}"]),  # noqa: S607
        ),
```

- [ ] **Step 4: Point the grafana role at the rendered file + drop the static one**

In `ansible/roles/grafana/tasks/main.yml`, replace the "deploy the fleet dashboard" task with:

```yaml
- name: deploy the rendered lab dashboard
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../build/grafana/dashboards/lab-status.json"
    dest: /var/lib/grafana/dashboards/lab-status.json
    owner: grafana
    group: grafana
    mode: "0644"
  notify: restart grafana
```

Then remove the now-unused static dashboard:

```bash
vrg-git rm ansible/roles/grafana/files/dashboards/fleet-node.json
```

- [ ] **Step 5: Run the obs CLI tests**

Run: `uv run pytest tests/test_cli_obs.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope obs --message "obs up renders the dashboard; grafana deploys the rendered file (#108)" --body "obs up now renders build/grafana/dashboards/lab-status.json alongside targets + inventory; the grafana role copies that rendered file (same pattern as prometheus targets) and the static fleet-node.json is removed. uid stays lab-fleet-node so obs open / the docs deep-link keep working."
```

---

### Task 5: Full validation

**Files:** none.

- [ ] **Step 1: Run the only validation command**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS — ruff/format/mypy clean, pytest green, **100% branch coverage** (`dashboard.py` is fully exercised by `tests/test_dashboard.py`; the new CLI command by `tests/test_cli_obs.py`). If `dashboard.py` shows a missed branch, add the missing-branch test (e.g. the `DashboardError` path is covered by `test_unknown_curated_group_fails_loud`).

- [ ] **Step 2: Commit any coverage/lint fixes**

```bash
vrg-commit --type test --scope mqlab --message "cover dashboard renderer to 100% (#108)" --body "Close any remaining branch on render_dashboard / obs dashboard."
```

(Skip if Step 1 was already green.)

---

### Task 6: Live verification

**Files:** none (live). Requires the obs stack from Plan A.

- [ ] **Step 1: Re-render + redeploy the dashboard onto the running obs box**

```bash
mqlab obs targets          # render build/prometheus/targets/node.json
mqlab obs dashboard        # render build/grafana/dashboards/lab-status.json
mqlab vm inventory         # render build/inventory.ini
cd ansible && ansible-playbook site-obs.yml && cd ..
```
`site-obs.yml` runs all three roles (node-exporter, prometheus, grafana), so all
three rendered artifacts must exist first — a fresh worktree's `build/` has none.
These are exactly what `mqlab obs up` renders in one shot, but `obs up` is
off-limits from a worktree: its `vagrant up` has no `.vagrant` metadata and would
collide with the already-running `obs`/`mon-probe` domains. The domains persist
across worktrees, so Ansible re-provisions them over `net-mgmt` via the static
inventory — **no Vagrant needed**.

- [ ] **Step 2: Confirm the layered view**

Open `http://localhost:3000/d/lab-fleet-node` (the **same uid** — the deep-link from `mqlab obs open` is unchanged). Confirm:
- The dashboard is titled **Lab — Layered Status**.
- A reserved **MQ Service** row on top, then VM group rows in order (SAN, PCMK A/B, RDQM A/B, Standalone, Observability), each with up/down tiles + a per-group CPU graph, then a **Networks** placeholder at the bottom.
- With only `obs` + `mon-probe` running, the **Observability** row is green; the other group rows show their members DOWN (no node_exporter there yet) — the full-topology fail-loud behavior, now grouped.

- [ ] **Step 3: Commit the evidence**

```bash
vrg-commit --type docs --scope obs --message "Tweak 1 live: layered grouped dashboard renders + deploys (#108)" --body "Dashboard restructured in place (uid lab-fleet-node preserved); grouped VM rows + per-group CPU verified live; network row is a placeholder pending Tweak 2." --allow-empty
```

---

### Task 7: Open the PR

- [ ] **Step 1: Validate once more, then submit via the issue-implement flow**

Run: `vrg-container-run -- vrg-validate` (green), then drive the oracle:
```bash
vrg-pr-workflow next --issue 108 --no-audit
vrg-pr-workflow report-ready --title "feat(obs): layered, grouped dashboard (Tweak 1) (#108)" \
  --summary "Render the Grafana dashboard from a curated layout + topology — reserved MQ row, grouped VM rows with up/down + per-group CPU, placeholder network strip; uid preserved." \
  --notes "Tweak 1 of #108 (dashboard restructure, existing metrics only). Tweak 2 (network telemetry) is a follow-up issue. If the PR conflicts with develop: fetch, rebase origin/develop, re-validate, push --force-with-lease."
vrg-pr-workflow next   # -> DONE; then the human runs vrg-submit-pr
```

---

## Self-review notes

- **Spec coverage:** §4 layered layout (MQ reserved / grouped VM rows / network placeholder) → Tasks 1,4,6; per-group up/down + CPU → Task 1 (`_up_panel`/`_cpu_panel`); curated order → `ROWS`; §5 uid preserved → `DASHBOARD_UID` + Task 4; §6 Tweak 1 boundary (existing metrics only, placeholder network) → honored. Network telemetry / host target / tri-state → Tweak 2 (out of scope here).
- **Placeholder scan:** the "placeholder" MQ + network rows are intentional, named, scoped to later work — not plan placeholders. All code steps carry complete code.
- **Type consistency:** `render_dashboard(topo)`, `DASHBOARD_UID`, `ROWS`, `DashboardError`, `dashboard_path()`, `lab_dashboard()` used identically across Tasks 1–4. The dashboard filename is `lab-status.json` everywhere (build path, role copy, obs up render); the *uid* is `lab-fleet-node` everywhere (renderer, role unchanged on uid, live deep-link).
