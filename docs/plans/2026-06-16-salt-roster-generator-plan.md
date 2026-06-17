# salt-ssh roster generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `src/mqlab/roster.py` (a pure `topology.yaml`→salt-ssh roster generator, sibling to `inventory.py`) and a `mqlab vm roster` CLI command, per `docs/specs/2026-06-16-salt-roster-generator-design.md`.

**Architecture:** Mirror `inventory.py`'s trio (`render_roster`/`roster_path`/`lab_roster`) + a `RosterError`. The roster covers exactly the group-reachable hosts (parity with the inventory), with group/setup membership as grains under `minion_opts: grains:` so `salt-ssh -G 'roster_groups:<grp>'` can target them. `priv` is an `expanduser()`'d absolute path; `sudo: true` globally. Output is `yaml.safe_dump(..., sort_keys=False, default_flow_style=False)` over an insertion-ordered dict, with a leading comment line.

**Tech Stack:** Python 3.12, PyYAML, Typer (CLI), pytest. Branch `feature/206-salt-roster-gen` in worktree `.worktrees/issue-206-salt-roster-gen`. Git via `vrg-git`/`vrg-commit`; validation via `vrg-container-run -- vrg-validate` (the only gate; includes 100% branch coverage).

---

> **⏸ PARKED 2026-06-17 — resume here.** Design (`docs/specs/2026-06-16-salt-roster-generator-design.md`) and this plan are **complete and pushback-reviewed**; **implementation has not started** (no `roster.py`, no `tests/test_roster.py`, no CLI command yet). To resume: execute this plan from **Task 1** (inline or subagent-driven). The Task 1 exact-string assertion was captured from a real `yaml.safe_dump` run, so it should go green first try. Part of the Ansible→Salt migration groundwork (evaluation #205, merged). Sibling parked threads: HADR investigations #213/#239/#240 and DR brainstorm #235 — all capture-only, not blocking #206.

---

**Conventions for every task:**
- All commands run from inside the worktree: `cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-cluster-tooling/.worktrees/issue-206-salt-roster-gen` first.
- Run tests with `uv run pytest` (dev loop only). Commit with `vrg-commit --type <type> --scope salt --message <msg>`.
- `MQLAB_REPO_ROOT` env var overrides `repo_root()` (used by CLI tests).

## File Structure

- **Create** `src/mqlab/roster.py` — the generator (`RosterError`, `_mgmt_ip`, `render_roster`, `roster_path`, `lab_roster`). One responsibility: project topology → roster text.
- **Create** `tests/test_roster.py` — pure-function tests (exact-string + fail-loud + parity), mirroring `tests/test_inventory.py`.
- **Modify** `src/mqlab/cli.py` — add `from mqlab.roster import lab_roster, roster_path` and a `vm_roster` command after `vm_inventory` (~line 727).
- **Modify** `tests/test_cli_vm.py` — add `test_vm_roster_writes_and_echoes`, mirroring `test_vm_inventory_writes_and_echoes` (line 269).

---

### Task 1: `render_roster` happy path

**Files:**
- Create: `src/mqlab/roster.py`
- Test: `tests/test_roster.py`

- [ ] **Step 1: Write the failing exact-string test**

Create `tests/test_roster.py`:

```python
from __future__ import annotations

import pytest

from mqlab.roster import RosterError, render_roster

TOPO = {
    "nodes": {
        "san-a": {"nics": {"net-mgmt": "10.50.0.5"}},
        "pcmk-a1": {"nics": {"net-mgmt": "10.50.0.51"}},
    },
    "groups": {
        "san_a": ["san-a"],
        "pcmk_a": ["pcmk-a1"],
        "site_a": ["san-a", "pcmk-a1"],
    },
    "setups": {"pcmk_san_ha": {"groups": ["san_a", "pcmk_a"]}},
}


def test_render_emits_targets_grains_in_first_appearance_order(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")  # pin expanduser for a deterministic priv
    out = render_roster(TOPO)
    assert out == (
        "# salt-ssh roster — generated from lab/topology.yaml. Do not edit by hand.\n"
        "san-a:\n"
        "  host: 10.50.0.5\n"
        "  user: vagrant\n"
        "  priv: /home/tester/.vagrant.d/insecure_private_key\n"
        "  sudo: true\n"
        "  minion_opts:\n"
        "    grains:\n"
        "      roster_groups:\n"
        "      - san_a\n"
        "      - site_a\n"
        "      roster_setups:\n"
        "      - pcmk_san_ha\n"
        "pcmk-a1:\n"
        "  host: 10.50.0.51\n"
        "  user: vagrant\n"
        "  priv: /home/tester/.vagrant.d/insecure_private_key\n"
        "  sudo: true\n"
        "  minion_opts:\n"
        "    grains:\n"
        "      roster_groups:\n"
        "      - pcmk_a\n"
        "      - site_a\n"
        "      roster_setups:\n"
        "      - pcmk_san_ha\n"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_roster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.roster'`.

- [ ] **Step 3: Write `src/mqlab/roster.py`**

```python
"""Render a salt-ssh roster as a pure function of lab/topology.yaml (#206).

Sibling to inventory.py: one source of truth (topology), the same group-reachable
host set, fail-loud on the same integrity problems. Group/setup membership rides
as grains under `minion_opts` so `salt-ssh -G 'roster_groups:<grp>'` can target it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mqlab.inventory import INSECURE_KEY
from mqlab.paths import repo_root

_HEADER = "# salt-ssh roster — generated from lab/topology.yaml. Do not edit by hand.\n"


class RosterError(RuntimeError):
    """topology.yaml cannot be rendered to a valid salt-ssh roster."""


def _mgmt_ip(nodes: dict[str, Any], host: str) -> str:
    spec = nodes.get(host)
    if spec is None:
        raise RosterError(f"group references undefined host: {host}")
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise RosterError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def render_roster(topo: dict[str, Any]) -> str:
    """Project parsed topology -> salt-ssh roster YAML. Raises RosterError on any
    integrity problem (missing mgmt IP, undefined host or group) — never silently.

    Covers exactly the hosts reachable through `groups` (parity with
    render_inventory), in first-appearance order across the groups iteration.
    """
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    setups = topo.get("setups", {})

    for setup, cfg in setups.items():
        for g in (cfg or {}).get("groups", []):
            if g not in groups:
                raise RosterError(f"setup {setup} references undefined group: {g}")

    host_groups: dict[str, list[str]] = {}
    for group, hosts in groups.items():
        for host in hosts:
            host_groups.setdefault(host, []).append(group)

    host_setups: dict[str, list[str]] = {host: [] for host in host_groups}
    for setup, cfg in setups.items():
        members = set((cfg or {}).get("groups", []))
        for host, hgroups in host_groups.items():
            if members.intersection(hgroups):
                host_setups[host].append(setup)

    priv = str(Path(INSECURE_KEY).expanduser())
    roster: dict[str, Any] = {}
    for host in host_groups:
        roster[host] = {
            "host": _mgmt_ip(nodes, host),
            "user": "vagrant",
            "priv": priv,
            "sudo": True,
            "minion_opts": {
                "grains": {
                    "roster_groups": host_groups[host],
                    "roster_setups": host_setups[host],
                }
            },
        }
    return _HEADER + yaml.safe_dump(roster, sort_keys=False, default_flow_style=False)


def roster_path() -> Path:
    """Where the rendered roster is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "salt" / "roster"


def lab_roster() -> str:
    """Render the real lab/topology.yaml to salt-ssh roster text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_roster(topo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/roster.py tests/test_roster.py
vrg-commit --type feat --scope salt --message "render_roster: topology -> salt-ssh roster with grains (#206)"
```

---

### Task 2: Fail-loud + parity + empty-setup branches

These lock the `RosterError` contract, the group-reachable host set, and the "host in no setup" branch (needed for the 100% branch-coverage gate). They exercise existing behavior from Task 1.

**Files:**
- Test: `tests/test_roster.py`

- [ ] **Step 1: Append the tests**

Add to `tests/test_roster.py`:

```python
def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"san-a": {"nics": {}}}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(RosterError, match="no net-mgmt IP: san-a"):
        render_roster(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(RosterError, match="undefined host: san-a"):
        render_roster(topo)


def test_setup_referencing_undefined_group_raises():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "setups": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(RosterError, match="undefined group: nope"):
        render_roster(topo)


def test_ungrouped_node_is_absent_and_uncovered_host_has_empty_setups(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    topo = {
        "nodes": {
            "a": {"nics": {"net-mgmt": "10.50.0.1"}},
            "b": {"nics": {"net-mgmt": "10.50.0.2"}},
            "lonely": {"nics": {"net-mgmt": "10.50.0.9"}},  # in no group
        },
        "groups": {"ga": ["a"], "gb": ["b"]},
        "setups": {"s1": {"groups": ["ga"]}},  # covers a, not b
    }
    out = render_roster(topo)
    assert "lonely:" not in out          # ungrouped node absent (parity with inventory)
    assert "a:\n" in out and "b:\n" in out
    # a is covered by s1; b is in a group no setup references -> empty list
    assert "      roster_setups:\n      - s1\n" in out
    assert "  minion_opts:\n    grains:\n      roster_groups:\n      - gb\n      roster_setups: []\n" in out
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_roster.py -v`
Expected: PASS (5 passed) — Task 1's implementation already satisfies these; they document and lock the contract and cover the remaining branches.

- [ ] **Step 3: Commit**

```bash
vrg-git add tests/test_roster.py
vrg-commit --type test --scope salt --message "lock roster fail-loud, parity, and empty-setup branches (#206)"
```

---

### Task 3: `lab_roster` against the real topology

Mirrors `tests/test_topology_integrity.py` — proves the real `lab/topology.yaml` renders without raising.

**Files:**
- Test: `tests/test_topology_integrity.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topology_integrity.py`:

```python
def test_lab_roster_renders_without_error():
    from mqlab.roster import lab_roster

    out = lab_roster()  # raises RosterError on any integrity problem
    assert out.startswith("# salt-ssh roster")
    assert "minion_opts:" in out
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_topology_integrity.py -v`
Expected: PASS — the real topology is well-formed, so `lab_roster()` renders. (If it raises `RosterError`, the real topology has an integrity gap — fix the topology, not the generator.)

- [ ] **Step 3: Commit**

```bash
vrg-git add tests/test_topology_integrity.py
vrg-commit --type test --scope salt --message "lab_roster renders the real topology (#206)"
```

---

### Task 4: `mqlab vm roster` CLI command

**Files:**
- Modify: `src/mqlab/cli.py` (import near the inventory import; command after `vm_inventory`, ~line 727)
- Test: `tests/test_cli_vm.py`

- [ ] **Step 1: Write the failing CLI test**

Append to `tests/test_cli_vm.py` (after `test_vm_inventory_writes_and_echoes`):

```python
def test_vm_roster_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "setups:\n  pcmk_san_ha: {groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "roster"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "salt" / "roster").read_text()
    assert "san-a:" in written
    assert "host: 10.50.0.5" in written
    assert "roster_groups:\n      - san_a" in written
    assert "roster_setups:\n      - pcmk_san_ha" in written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_vm.py::test_vm_roster_writes_and_echoes -v`
Expected: FAIL — no `vm roster` command (Typer exits non-zero / "No such command").

- [ ] **Step 3: Add the import to `src/mqlab/cli.py`**

Find the existing inventory import (e.g. `from mqlab.inventory import inventory_path, lab_inventory`) and add directly below it:

```python
from mqlab.roster import lab_roster, roster_path
```

- [ ] **Step 4: Add the `vm_roster` command after `vm_inventory`**

Insert immediately after the `vm_inventory` function (after its `finally: deps.transcript.close()`):

```python
@vm_app.command("roster")
def vm_roster() -> None:
    """Render build/salt/roster from topology and echo it (the salt-ssh map)."""
    deps = build_deps("vm-roster", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    try:
        text = lab_roster()
        path = roster_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        deps.renderer.command(f"render -> {path}")
        for line in text.splitlines():
            deps.renderer.output(line)
            deps.transcript.write(line)
    finally:
        deps.transcript.close()
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli_vm.py::test_vm_roster_writes_and_echoes -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/cli.py tests/test_cli_vm.py
vrg-commit --type feat --scope salt --message "mqlab vm roster — render build/salt/roster (#206)"
```

---

### Task 5: Full validation gate

**Files:** none (validation only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest -q`
Expected: all pass (no regressions in the existing suite).

- [ ] **Step 2: Run the only validation gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green. This enforces ruff, ty, and **100% branch coverage**. If coverage flags an uncovered branch in `roster.py`, every branch is intended to be covered by Tasks 1–2 (happy path; the three `RosterError` raises; the `members.intersection` True/False via `s1`/`gb`); add the missing case rather than lowering the gate. **Never** suppress a gate.

- [ ] **Step 3: Confirm nothing stray is staged**

Run: `vrg-git status --short`
Expected: clean (all work already committed in Tasks 1–4).

---

## Self-review (author's check against the spec)

- **Spec coverage:** §3 trio (`render_roster`/`roster_path`/`lab_roster`) + `RosterError` → Tasks 1/3; §3.1 grains-under-`minion_opts`, `expanduser` `priv`, global `sudo`, first-appearance order → Task 1 (verified by the captured exact-string test); §3.1 group-reachable host set → Task 2 parity test; §3.2 three fail-loud cases → Task 2; §4 `mqlab vm roster` → Task 4; §5 testing (exact-string, fail-loud, grain correctness, parity, CLI) → Tasks 1/2/4; validation → Task 5. No uncovered requirement.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code; the exact-string assertion was captured from a real run, not guessed.
- **Type/name consistency:** `RosterError`, `render_roster`, `roster_path`, `lab_roster`, `_mgmt_ip`, `INSECURE_KEY` (imported from `inventory`), the `roster_groups`/`roster_setups` grain keys, and the `build/salt/roster` path are used identically across all tasks and match the spec.
