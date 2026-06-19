# Native HA Cockpit — PR1: `nativehastate` collector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdlib-only `src/mqlab/nativehastate.py` collector that parses `dspmq -m QMNATIVE
-o nativeha -x` (per-instance HA) and `-g` (cross-region CRR group) into the **§3.2
`cluster_*`-shaped** metrics the existing `clusterboard.py` builders already consume, plus
Native-HA-specific `cluster_nha_*` facets — deployed verbatim to the nha nodes by a new
`nativeha-state` Ansible role on a 5s timer.

**Architecture:** Mirrors `clusterstate.py` exactly (the PCMK collector): pure parse functions
turn `dspmq` output into rows; `render_nativeha_state_prom(...)` projects rows to
node_exporter textfile lines; `probe()` runs each source bounded + non-blocking (timeout → no
fresh sample → the cell reads **STALE**). The module is both imported by the repo's unit tests
**and** copied verbatim to `/usr/local/bin/lab-nativeha-state` on each nha node. The `dspmq`
formats below are **provisional** (transcribed from #246's findings reports); **Task 1 Step 1
captures the real output from the live 3+3 and the parsers are written against that capture** —
exactly as #272 captured real `drbdsetup` output.

**Tech Stack:** Python 3.11+ (stdlib only — `argparse`, `re`, `subprocess`, `os`, `time`,
`pathlib`), Prometheus node_exporter textfile collector, Ansible `nativeha-state` role +
systemd service/timer, the live `distributed-nativeha-rhel` 3+3 (#246).

## Global Constraints

- **No new builders.** This PR adds **no** `clusterboard.py` code. It produces the metrics the
  existing builders read. The board render path is PR2.
- **`cluster_*`-shaped reuse:** the collector emits `cluster_quorate`, `cluster_node_online`,
  and `cluster_resource_owner{resource="QMNATIVE"}` so the existing `hero_tiles`,
  `active_side`, and `fold_side` work unchanged; Native-HA-only facets get `cluster_nha_*`
  names (spec §4).
- **Fail-loud / STALE:** a silent or timed-out probe → that source is **not** in
  `fresh_sources` → no `cluster_state_last_write_timestamp` for it → cells read STALE, never
  green. No swallowed errors, no green-on-no-data (spec §6, §9; repo "no silent failures").
- **Stdlib only.** `nativehastate.py` imports nothing outside the stdlib so it deploys verbatim
  (like `clusterstate.py`). It does **not** import `clusterstate` (that would couple two
  deployed files) — the small `probe()`/`_m()` helpers are replicated, matching the existing
  pattern.
- **No timing claims** (TCG): replication is reported as in-sync (yes/no) + backlog **count**,
  never RPO seconds.
- **Validation:** `vrg-container-run -- vrg-validate` is the only gate; **100% branch
  coverage** required (`uv run pytest` under the container).
- **#246 dependency:** Tasks 1 (fixture capture) and 5 (role + `observability.yml` wiring +
  live verify) need the `nativeha-rhel` arm — the topology groups `nha_rhel_a`/`nha_rhel_b` and
  the live 3+3. Build on `develop` **after #246 merges**, or branch this work off
  `feature/246-nativeha-vm-arms`. Tasks 2–4 (pure parse/render/runner + tests) are
  develop-safe and have no lab dependency.

---

## Task 1: Capture the real fixtures + `parse_nativeha_x()`

**Files:**
- Create: `tests/fixtures/nativehastate/dspmq_nativeha_x.txt` (real capture)
- Create: `src/mqlab/nativehastate.py`
- Test: `tests/test_nativehastate.py`

**Interfaces:**
- Produces: `parse_nativeha_x(text: str) -> dict[str, Any]` →
  `{"quorum_current": int|None, "quorum_total": int|None, "group_role": str|None,
  "instances": {name: {"role": str, "insync": bool, "hastatus": str}}}`.
- Helper: `_fields(line: str) -> dict[str, str]` — all `KEY(value)` tokens on a line.

- [ ] **Step 1: Capture the real `-x` fixture from the live lab.** On the obs/lab host, run the
  arm's own status verb against an nha node in `nha_rhel_a` and save it verbatim:

```bash
# from the lab host (the live 3+3 is up per the user); pick any site-A node
mqlab qm status nativeha   # arm verb = su - mqm -c 'dspmq -m QMNATIVE -o nativeha -x'
# or directly on nha-rhel-a1:
#   ssh nha-rhel-a1 "su - mqm -c '/opt/mqm/bin/dspmq -m QMNATIVE -o nativeha -x'"
```

  Save the exact stdout to `tests/fixtures/nativehastate/dspmq_nativeha_x.txt`. **The
  provisional shape below is from #246's findings — replace it with the real capture and adjust
  the parser/asserts to match the true field order, casing, and `QUORUM(x/y)` format.**

```text
QMNAME(QMNATIVE) ROLE(Active) INSTANCE(nha-rhel-a1) INSYNC(yes) QUORUM(3/3) HASTATUS(Normal) GRPROLE(Live)
 INSTANCE(nha-rhel-a1) ROLE(Active)  REPLADDR(172.16.1.71) INSYNC(yes) HASTATUS(Normal)
 INSTANCE(nha-rhel-a2) ROLE(Replica) REPLADDR(172.16.1.72) INSYNC(yes) HASTATUS(Normal)
 INSTANCE(nha-rhel-a3) ROLE(Replica) REPLADDR(172.16.1.73) INSYNC(yes) HASTATUS(Normal)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_nativehastate.py
from __future__ import annotations

from pathlib import Path

from mqlab import nativehastate

FIXTURES = Path(__file__).parent / "fixtures" / "nativehastate"


def test_parse_nativeha_x_extracts_quorum_group_role_and_per_instance_state():
    # real capture: site-A 3/3, a1 Active, a2/a3 Replica, all in-sync, group is Live
    out = nativehastate.parse_nativeha_x((FIXTURES / "dspmq_nativeha_x.txt").read_text())
    assert out["quorum_current"] == 3
    assert out["quorum_total"] == 3
    assert out["group_role"] == "Live"
    assert out["instances"]["nha-rhel-a1"] == {
        "role": "Active",
        "insync": True,
        "hastatus": "Normal",
    }
    assert out["instances"]["nha-rhel-a2"]["role"] == "Replica"
    assert set(out["instances"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}


def test_parse_nativeha_x_handles_degraded_unknown_and_not_insync():
    # a degraded snapshot: quorum lost (1/3), the leader Unknown, a replica not in-sync
    text = (
        "QMNAME(QMNATIVE) ROLE(Unknown) INSTANCE(nha-rhel-a1) INSYNC(no) QUORUM(1/3) "
        "HASTATUS(Abnormal) GRPROLE(Live)\n"
        " INSTANCE(nha-rhel-a1) ROLE(Unknown) INSYNC(no) HASTATUS(Abnormal)\n"
        " INSTANCE(nha-rhel-a2) ROLE(Replica) INSYNC(no) HASTATUS(Normal)\n"
    )
    out = nativehastate.parse_nativeha_x(text)
    assert out["quorum_current"] == 1
    assert out["instances"]["nha-rhel-a1"]["role"] == "Unknown"
    assert out["instances"]["nha-rhel-a1"]["insync"] is False
    assert out["instances"]["nha-rhel-a2"]["insync"] is False


def test_parse_nativeha_x_without_quorum_line_leaves_summary_none():
    # blank / unexpected output -> no quorum, no instances (fail-loud: nothing fabricated)
    out = nativehastate.parse_nativeha_x("\n  \n")
    assert out["quorum_current"] is None
    assert out["quorum_total"] is None
    assert out["group_role"] is None
    assert out["instances"] == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mqlab.nativehastate'`.

- [ ] **Step 4: Write minimal implementation**

```python
# src/mqlab/nativehastate.py
"""Native-HA cluster-state collector for the lab-nativeha-cluster cockpit (#279).

Stdlib-only so this exact file deploys verbatim to the nha nodes as
/usr/local/bin/lab-nativeha-state and runs on a 5s systemd timer, AND is imported by the
repo's unit tests. Pure parse functions turn `dspmq -o nativeha -x`/`-g` output into rows;
render_nativeha_state_prom turns rows into a node_exporter textfile; probe() runs each source
bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).

Emits the same cluster_* shape as clusterstate.py (so the clusterboard.py builders and the
overview roll-up work unchanged); Native-HA-only facets get cluster_nha_* names (spec §4).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIELD = re.compile(r"(\w+)\(([^)]*)\)")


def _fields(line: str) -> dict[str, str]:
    """All KEY(value) tokens on a line -> {KEY: value}. Values that themselves contain
    parens (GRPADDR) aren't read by any metric, so the naive scan is harmless."""
    return dict(_FIELD.findall(line))


def parse_nativeha_x(text: str) -> dict[str, Any]:
    """Parse `dspmq -m <qm> -o nativeha -x` into quorum + group role + per-instance state.

    The summary line carries QUORUM(x/y) and GRPROLE; the indented lines are one per
    instance (INSTANCE/ROLE/INSYNC/HASTATUS). INSYNC(yes) -> True.
    """
    summary: dict[str, Any] = {
        "quorum_current": None,
        "quorum_total": None,
        "group_role": None,
    }
    instances: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if not f:
            continue
        if "QUORUM" in f:
            cur, total = f["QUORUM"].split("/", 1)
            summary["quorum_current"] = int(cur)
            summary["quorum_total"] = int(total)
            summary["group_role"] = f.get("GRPROLE")
        if "INSTANCE" in f and "ROLE" in f:
            instances[f["INSTANCE"]] = {
                "role": f["ROLE"],
                "insync": f.get("INSYNC") == "yes",
                "hastatus": f.get("HASTATUS", "Unknown"),
            }
    return {**summary, "instances": instances}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/nativehastate.py tests/test_nativehastate.py tests/fixtures/nativehastate/dspmq_nativeha_x.txt
vrg-commit --type feat --scope obs --message "nativehastate.parse_nativeha_x — per-instance HA parse + real fixture (#279)"
```

---

## Task 2: `parse_nativeha_g()` — the CRR group view

**Files:**
- Create: `tests/fixtures/nativehastate/dspmq_nativeha_g.txt` (real capture)
- Modify: `src/mqlab/nativehastate.py`
- Test: `tests/test_nativehastate.py`

**Interfaces:**
- Consumes: `_fields()` (Task 1).
- Produces: `parse_nativeha_g(text: str) -> dict[str, dict[str, Any]]` keyed by group name →
  `{"role": str, "connected": bool, "insync": bool, "backlog": int}`.

- [ ] **Step 1: Capture the real `-g` fixture from the live lab.** Run the group view and save
  it verbatim (replace the provisional shape below + adjust asserts to the true output):

```bash
# on a site-A node; -g is the cross-region (CRR) group view
ssh nha-rhel-a1 "su - mqm -c '/opt/mqm/bin/dspmq -m QMNATIVE -o nativeha -g'"
```

  Save to `tests/fixtures/nativehastate/dspmq_nativeha_g.txt`. Provisional shape:

```text
GRPNAME(Live) GRPROLE(Live) GRSTATUS(Normal) GRPVER(9.4.5.0) CONNGRP(yes) INSYNC(yes) BACKLOG(0)
GRPNAME(Recovery) GRPROLE(Recovery) CONNGRP(yes) INSYNC(yes) BACKLOG(0)
```

> **Note:** if the real output includes `GRPADDR(10.99.0.71(9415),...)` (nested parens),
> leave it in the fixture — `_fields()` ignores it; only `GRPNAME/GRPROLE/CONNGRP/INSYNC/
> BACKLOG` are read.

- [ ] **Step 2: Write the failing test**

```python
def test_parse_nativeha_g_extracts_both_groups():
    out = nativehastate.parse_nativeha_g((FIXTURES / "dspmq_nativeha_g.txt").read_text())
    assert out["Live"] == {"role": "Live", "connected": True, "insync": True, "backlog": 0}
    assert out["Recovery"]["role"] == "Recovery"
    assert out["Recovery"]["connected"] is True


def test_parse_nativeha_g_flags_disconnected_recovery_with_backlog():
    text = (
        "GRPNAME(Live) GRPROLE(Live) CONNGRP(no) INSYNC(no) BACKLOG(0)\n"
        "GRPNAME(Recovery) GRPROLE(Recovery) CONNGRP(no) INSYNC(no) BACKLOG(4096)\n"
    )
    out = nativehastate.parse_nativeha_g(text)
    assert out["Live"]["connected"] is False
    assert out["Recovery"]["insync"] is False
    assert out["Recovery"]["backlog"] == 4096


def test_parse_nativeha_g_skips_lines_without_group_name():
    out = nativehastate.parse_nativeha_g("\nGRPROLE(Live) CONNGRP(yes)\n")  # no GRPNAME
    assert out == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -k nativeha_g -q`
Expected: FAIL — `AttributeError: module 'mqlab.nativehastate' has no attribute 'parse_nativeha_g'`.

- [ ] **Step 4: Write minimal implementation** (append to `nativehastate.py`)

```python
def parse_nativeha_g(text: str) -> dict[str, dict[str, Any]]:
    """Parse `dspmq -m <qm> -o nativeha -g` (CRR) into {group_name: {role,connected,insync,
    backlog}}. One line per group, keyed by GRPNAME. BACKLOG is a message count, never seconds.
    """
    groups: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if "GRPNAME" not in f:
            continue
        groups[f["GRPNAME"]] = {
            "role": f.get("GRPROLE", "Unknown"),
            "connected": f.get("CONNGRP") == "yes",
            "insync": f.get("INSYNC") == "yes",
            "backlog": int(f.get("BACKLOG", 0)),
        }
    return groups
```

- [ ] **Step 5: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -k nativeha_g -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/nativehastate.py tests/test_nativehastate.py tests/fixtures/nativehastate/dspmq_nativeha_g.txt
vrg-commit --type feat --scope obs --message "nativehastate.parse_nativeha_g — CRR group-view parse + fixture (#279)"
```

---

## Task 3: `render_nativeha_state_prom()` — project to `cluster_*` / `cluster_nha_*`

**Files:**
- Modify: `src/mqlab/nativehastate.py`
- Test: `tests/test_nativehastate.py`

**Interfaces:**
- Consumes: the dicts from `parse_nativeha_x`/`parse_nativeha_g`.
- Produces: `render_nativeha_state_prom(*, node: str, qm: str, hax: dict | None, grp: dict |
  None, now: int, fresh_sources: tuple[str, ...]) -> str` — node_exporter textfile lines
  labelled `node=<self>`. Helper `_m(name, labels, value) -> str` (same format as
  `clusterstate._m`).
- Metric contract (spec §4):
  - `cluster_quorate{node}` = 1 if `quorum_current ≥ majority(quorum_total)` else 0 (majority =
    `total // 2 + 1`); omitted when quorum is unknown.
  - `cluster_nha_quorum{node}` = `quorum_current` (gauge).
  - per instance: `cluster_node_online{node,member}` (1 if role ≠ `Unknown`),
    `cluster_nha_role{node,member,role}` = 1, `cluster_nha_insync{node,member}` (1|0),
    `cluster_nha_hastatus{node,member,status}` = 1; `cluster_resource_owner{node,
    resource=<qm>,holder=<member>}` = 1 for the `Active` instance.
  - per group: `cluster_nha_group_role{node,group,role}` = 1,
    `cluster_nha_connected{node,group}` (1|0), `cluster_nha_group_insync{node,group}` (1|0),
    `cluster_nha_group_backlog{node,group}` = count.
  - `cluster_state_last_write_timestamp{node,source}` for each fresh source.

- [ ] **Step 1: Write the failing test**

```python
def test_render_emits_quorum_owner_and_per_instance_metrics():
    hax = {
        "quorum_current": 3,
        "quorum_total": 3,
        "group_role": "Live",
        "instances": {
            "nha-rhel-a1": {"role": "Active", "insync": True, "hastatus": "Normal"},
            "nha-rhel-a2": {"role": "Replica", "insync": True, "hastatus": "Normal"},
        },
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1", qm="QMNATIVE", hax=hax, grp=None,
        now=1781455000, fresh_sources=("nativeha_x",),
    )
    assert 'cluster_quorate{node="nha-rhel-a1"} 1' in out
    assert 'cluster_nha_quorum{node="nha-rhel-a1"} 3' in out
    assert 'cluster_node_online{node="nha-rhel-a1",member="nha-rhel-a1"} 1' in out
    assert 'cluster_nha_role{node="nha-rhel-a1",member="nha-rhel-a1",role="Active"} 1' in out
    assert 'cluster_nha_insync{node="nha-rhel-a1",member="nha-rhel-a2"} 1' in out
    assert (
        'cluster_resource_owner{node="nha-rhel-a1",resource="QMNATIVE",holder="nha-rhel-a1"} 1'
        in out
    )
    assert (
        'cluster_state_last_write_timestamp{node="nha-rhel-a1",source="nativeha_x"} 1781455000'
        in out
    )


def test_render_quorum_lost_and_unknown_leader_branches():
    hax = {
        "quorum_current": 1,
        "quorum_total": 3,
        "group_role": "Live",
        "instances": {"nha-rhel-a1": {"role": "Unknown", "insync": False, "hastatus": "Abnormal"}},
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1", qm="QMNATIVE", hax=hax, grp=None, now=1, fresh_sources=(),
    )
    assert 'cluster_quorate{node="nha-rhel-a1"} 0' in out  # 1 < majority(2)
    assert 'cluster_node_online{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out  # Unknown
    assert 'cluster_nha_insync{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out
    assert "cluster_resource_owner" not in out  # no Active -> no owner line
    assert "last_write_timestamp" not in out  # empty fresh_sources


def test_render_unknown_quorum_omits_quorate():
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1", qm="QMNATIVE",
        hax={"quorum_current": None, "quorum_total": None, "group_role": None, "instances": {}},
        grp=None, now=1, fresh_sources=(),
    )
    assert "cluster_quorate" not in out  # unknown -> omitted, never a false green
    assert "cluster_nha_quorum" not in out


def test_render_emits_group_metrics():
    grp = {
        "Live": {"role": "Live", "connected": True, "insync": True, "backlog": 0},
        "Recovery": {"role": "Recovery", "connected": False, "insync": False, "backlog": 512},
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1", qm="QMNATIVE", hax=None, grp=grp,
        now=1, fresh_sources=("nativeha_g",),
    )
    assert 'cluster_nha_group_role{node="nha-rhel-a1",group="Live",role="Live"} 1' in out
    assert 'cluster_nha_connected{node="nha-rhel-a1",group="Recovery"} 0' in out
    assert 'cluster_nha_group_backlog{node="nha-rhel-a1",group="Recovery"} 512' in out
    assert 'cluster_nha_group_insync{node="nha-rhel-a1",group="Live"} 1' in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -k render -q`
Expected: FAIL — `AttributeError: ... has no attribute 'render_nativeha_state_prom'`.

- [ ] **Step 3: Write minimal implementation** (append to `nativehastate.py`)

```python
def _m(name: str, labels: Mapping[str, object], value: object) -> str:
    """Format one Prometheus sample line: name{k="v",...} value."""
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


def render_nativeha_state_prom(
    *,
    node: str,
    qm: str,
    hax: dict[str, Any] | None,
    grp: dict[str, dict[str, Any]] | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed dspmq results into node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if hax is not None:
        cur, total = hax["quorum_current"], hax["quorum_total"]
        if cur is not None and total is not None:
            majority = total // 2 + 1
            lines.append(_m("cluster_quorate", {"node": node}, 1 if cur >= majority else 0))
            lines.append(_m("cluster_nha_quorum", {"node": node}, cur))
        for member, st in hax["instances"].items():
            base = {"node": node, "member": member}
            lines.append(_m("cluster_node_online", base, 0 if st["role"] == "Unknown" else 1))
            lines.append(_m("cluster_nha_role", {**base, "role": st["role"]}, 1))
            lines.append(_m("cluster_nha_insync", base, 1 if st["insync"] else 0))
            lines.append(_m("cluster_nha_hastatus", {**base, "status": st["hastatus"]}, 1))
            if st["role"] == "Active":
                owner = {"node": node, "resource": qm, "holder": member}
                lines.append(_m("cluster_resource_owner", owner, 1))

    if grp is not None:
        for name, g in grp.items():
            gbase = {"node": node, "group": name}
            lines.append(_m("cluster_nha_group_role", {**gbase, "role": g["role"]}, 1))
            lines.append(_m("cluster_nha_connected", gbase, 1 if g["connected"] else 0))
            lines.append(_m("cluster_nha_group_insync", gbase, 1 if g["insync"] else 0))
            lines.append(_m("cluster_nha_group_backlog", gbase, g["backlog"]))

    for source in fresh_sources:
        lines.append(_m("cluster_state_last_write_timestamp", {"node": node, "source": source}, now))

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -k render -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
vrg-git add src/mqlab/nativehastate.py tests/test_nativehastate.py
vrg-commit --type feat --scope obs --message "nativehastate.render_nativeha_state_prom — cluster_*/cluster_nha_* projection (#279)"
```

---

## Task 4: `probe()` + `collect()` + `main()` — the deployed runner

**Files:**
- Modify: `src/mqlab/nativehastate.py`
- Test: `tests/test_nativehastate.py`

**Interfaces:**
- Consumes: `parse_nativeha_x`, `parse_nativeha_g`, `render_nativeha_state_prom`.
- Produces:
  - `probe(cmd: list[str], timeout: int) -> str | None` (stdout on success; None on
    timeout/nonzero/OSError).
  - `collect(node: str, qm: str, now: int) -> str` — runs both `dspmq` probes and renders.
  - `main(argv: list[str] | None = None) -> None` — `lab-nativeha-state --qm QMNATIVE [--node]
    [--out] [--now]`, atomic write (tmp + replace).
- `_COMMANDS(qm)` builds the two probe argv: `-o nativeha -x` (source `nativeha_x`) and `-g`
  (source `nativeha_g`).

- [ ] **Step 1: Write the failing test**

```python
import subprocess


def test_probe_returns_stdout_then_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        nativehastate.subprocess, "run",
        lambda c, **k: subprocess.CompletedProcess(c, 0, "out\n", ""),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) == "out\n"
    monkeypatch.setattr(
        nativehastate.subprocess, "run",
        lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None
    monkeypatch.setattr(
        nativehastate.subprocess, "run",
        lambda c, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, k["timeout"])),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None
    monkeypatch.setattr(
        nativehastate.subprocess, "run",
        lambda c, **k: (_ for _ in ()).throw(OSError("no dspmq")),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None


def test_main_writes_textfile_atomically(tmp_path, monkeypatch):
    xtext = (FIXTURES / "dspmq_nativeha_x.txt").read_text()
    gtext = (FIXTURES / "dspmq_nativeha_g.txt").read_text()

    def fake_probe(cmd, timeout):
        return gtext if "-g" in cmd else xtext

    monkeypatch.setattr(nativehastate, "probe", fake_probe)
    out = tmp_path / "lab_nativeha_state.prom"
    nativehastate.main(
        ["--qm", "QMNATIVE", "--node", "nha-rhel-a1", "--out", str(out), "--now", "1781455000"]
    )
    text = out.read_text()
    assert 'cluster_quorate{node="nha-rhel-a1"} 1' in text
    assert 'cluster_nha_group_role{node="nha-rhel-a1",group="Live",role="Live"} 1' in text
    assert 'source="nativeha_x"' in text and 'source="nativeha_g"' in text
    assert not (tmp_path / "lab_nativeha_state.prom.tmp").exists()  # atomic move cleaned up


def test_main_marks_sources_stale_when_probes_time_out(tmp_path, monkeypatch):
    monkeypatch.setattr(nativehastate, "probe", lambda cmd, timeout: None)
    out = tmp_path / "c.prom"
    nativehastate.main(["--qm", "QMNATIVE", "--node", "nha-rhel-a1", "--out", str(out)])
    text = out.read_text()
    assert "cluster_quorate" not in text  # -x stale -> omitted
    assert "last_write_timestamp" not in text  # nothing fresh


def test_main_defaults_node_to_hostname_and_now_to_clock(tmp_path, monkeypatch):
    xtext = (FIXTURES / "dspmq_nativeha_x.txt").read_text()
    monkeypatch.setattr(
        nativehastate, "probe", lambda cmd, timeout: xtext if "-x" in cmd else None
    )
    monkeypatch.setattr(
        nativehastate.os, "uname", lambda: type("U", (), {"nodename": "nha-rhel-a2"})()
    )
    monkeypatch.setattr(nativehastate.time, "time", lambda: 1781455999.0)
    out = tmp_path / "c.prom"
    nativehastate.main(["--qm", "QMNATIVE", "--out", str(out)])
    text = out.read_text()
    assert 'cluster_quorate{node="nha-rhel-a2"} 1' in text
    assert (
        'cluster_state_last_write_timestamp{node="nha-rhel-a2",source="nativeha_x"} 1781455999'
        in text
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -k "probe or main" -q`
Expected: FAIL — `AttributeError: ... has no attribute 'probe'`.

- [ ] **Step 3: Write minimal implementation** (append to `nativehastate.py`)

```python
def _commands(qm: str) -> dict[str, tuple[list[str], int]]:
    """source -> (dspmq argv, timeout). Timeouts are well under the 5s tick."""
    return {
        "nativeha_x": (["dspmq", "-m", qm, "-o", "nativeha", "-x"], 3),
        "nativeha_g": (["dspmq", "-m", qm, "-o", "nativeha", "-g"], 3),
    }


def probe(cmd: list[str], timeout: int) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE)."""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def collect(node: str, qm: str, now: int) -> str:
    """Run both dspmq probes and render the textfile body."""
    cmds = _commands(qm)
    fresh: list[str] = []
    raw_x = probe(*cmds["nativeha_x"])
    hax = None
    if raw_x is not None:
        hax = parse_nativeha_x(raw_x)
        fresh.append("nativeha_x")
    raw_g = probe(*cmds["nativeha_g"])
    grp = None
    if raw_g is not None:
        grp = parse_nativeha_g(raw_g)
        fresh.append("nativeha_g")
    return render_nativeha_state_prom(
        node=node, qm=qm, hax=hax, grp=grp, now=now, fresh_sources=tuple(fresh)
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the deployed collector: `lab-nativeha-state --qm QMNATIVE`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", required=True)
    ap.add_argument("--node", default=os.uname().nodename)
    ap.add_argument("--out", default="/var/lib/node_exporter/textfile/lab_nativeha_state.prom")
    ap.add_argument("--now", type=int, default=None)
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    body = collect(args.node, args.qm, now)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(body)
    tmp.replace(args.out)


if __name__ == "__main__":
    main()
```

> `probe(*cmds["nativeha_x"])` unpacks `(argv, timeout)` into `probe(cmd, timeout)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `vrg-container-run -- uv run pytest tests/test_nativehastate.py -q`
Expected: PASS (all parse/render/probe/main tests).

- [ ] **Step 5: Full validation gate**

Run: `vrg-container-run -- vrg-validate`
Expected: green (ruff + 100% branch coverage on `nativehastate.py`). Fix any lint (magic-comma
dict explosion, E501) or coverage gaps and re-run — never suppress a gate.

- [ ] **Step 6: Commit**

```bash
vrg-git add src/mqlab/nativehastate.py tests/test_nativehastate.py
vrg-commit --type feat --scope obs --message "nativehastate: probe/collect/main runner (#279)"
```

---

## Task 5: `nativeha-state` Ansible role + `observability.yml` wiring

> **#246-gated.** This task needs the `nha_rhel_a`/`nha_rhel_b` groups in `topology.yaml` and
> the live 3+3. Do it on a base that has #246 (after it merges, or branch off
> `feature/246-nativeha-vm-arms`). Tasks 1–4 do **not** depend on this.

**Files:**
- Create: `ansible/roles/nativeha-state/tasks/main.yml`
- Create: `ansible/roles/nativeha-state/templates/lab-nativeha-state.service.j2`
- Create: `ansible/roles/nativeha-state/templates/lab-nativeha-state.timer.j2`
- Modify: `ansible/observability.yml` (add the `nativeha-state` role block)

**Interfaces:**
- Consumes: `src/mqlab/nativehastate.py` (deployed verbatim, like `cluster-state`).
- Produces: `/usr/local/bin/lab-nativeha-state` + a 5s timer writing
  `/var/lib/node_exporter/textfile/lab_nativeha_state.prom` on every `nha_rhel_a`/`nha_rhel_b`
  node.

- [ ] **Step 1: Role tasks** (mirror `roles/cluster-state/tasks/main.yml`):

```yaml
---
# Deploy the nativeha-state collector (the tested mqlab.nativehastate module, verbatim) and
# run it on a 5s timer. Mirrors cluster-state: textfile dir -> deploy collector -> service +
# timer -> enable. The QM name comes from the observability overlay (nativeha_qm).
- name: textfile dir
  ansible.builtin.file:
    path: /var/lib/node_exporter/textfile
    state: directory
    mode: "0755"

- name: deploy the nativeha-state collector (the tested mqlab module, verbatim)
  ansible.builtin.copy:
    src: "{{ playbook_dir }}/../src/mqlab/nativehastate.py"
    dest: /usr/local/bin/lab-nativeha-state
    mode: "0755"

- name: install service + timer
  ansible.builtin.template:
    src: "{{ item }}"
    dest: "/etc/systemd/system/{{ item | basename | regex_replace('\\.j2$', '') }}"
    mode: "0644"
  loop: [lab-nativeha-state.service.j2, lab-nativeha-state.timer.j2]

- name: enable + start the timer
  ansible.builtin.systemd:
    name: lab-nativeha-state.timer
    enabled: true
    state: started
    daemon_reload: true
```

- [ ] **Step 2: service template** (`lab-nativeha-state.service.j2`) — run as `mqm` so `dspmq`
  works (the arm's `qm-status` verb runs `su - mqm -c '... dspmq ...'`):

```ini
[Unit]
Description=Lab Native-HA state collector (textfile)

[Service]
Type=oneshot
User=mqm
ExecStart=/usr/local/bin/lab-nativeha-state --qm {{ nativeha_qm | default('QMNATIVE') }}
```

> Confirm against the arm: `dspmq` must be on `mqm`'s PATH (the role may need an absolute
> `/opt/mqm/bin/dspmq` — set it via a `--` wrapper or PATH in the unit if the bare name fails;
> match how #246's `qm-status` verb invokes it).

- [ ] **Step 3: timer template** (`lab-nativeha-state.timer.j2`, 5s cadence — copy the
  `cluster-state` timer):

```ini
[Unit]
Description=Run the Native-HA state collector every 5s

[Timer]
OnBootSec=15
OnUnitActiveSec=5
AccuracySec=1s

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Wire it into `observability.yml`** — add a role block alongside the
  `cluster-state` one, gated on the nha groups (mirror the existing `when:` pattern):

```yaml
- role: nativeha-state
  vars:
    nativeha_qm: "{{ qm.name | default('QMNATIVE') }}"
  when: >-
    'nha_rhel_a' in group_names or 'nha_rhel_b' in group_names
```

- [ ] **Step 5: Offline gate.**

Run: `vrg-container-run -- vrg-validate`
Expected: green (yamllint + ansible-lint + 100% branch tests still pass).

- [ ] **Step 6: Headless live verify (load-bearing — needs the live 3+3).** Provision the obs
  overlay onto the nha nodes, then confirm the textfile + scrape:

```bash
mqlab obs up            # re-renders dashboards + re-runs observability.yml
# on nha-rhel-a1: the collector wrote a fresh textfile
ssh nha-rhel-a1 "cat /var/lib/node_exporter/textfile/lab_nativeha_state.prom"
#   expect: cluster_quorate{node="nha-rhel-a1"} 1
#           cluster_resource_owner{...,resource="QMNATIVE",holder="nha-rhel-a1"} 1
#           cluster_nha_group_role{...,group="Live",role="Live"} 1
# and Prometheus has scraped it:
#   curl -s 10.50.0.2:9090/api/v1/query?query=cluster_quorate | jq '.data.result'
```

  Confirms the collector runs as `mqm`, `dspmq` resolves, and the series land in Prometheus —
  the foundation PR2's board binds to.

- [ ] **Step 7: Commit**

```bash
vrg-git add ansible/roles/nativeha-state ansible/observability.yml
vrg-commit --type feat --scope obs --message "nativeha-state role + observability wiring for the 3+3 (#279)"
```

---

## What PR1 deliberately leaves to follow-on plans (spec §8)

Each gets its own plan when reached (the #219 pattern: one plan per PR, reviewed between):

- **PR2 — Board.** Native-HA input tables (instances matrix rows `a1..3`/`b1..3` banded by
  group; columns role · in-sync · HA-status · quorum-member) + the
  `render_cluster_dashboard(topo, arm="nativeha-rhel")` path → instances matrix + hero +
  integrity (the §6 reframing: quorum-lost / no-Active / not-in-sync, **not** split-brain).
  Parametrizes the hero's owner resource (`mq_qm` → `QMNATIVE`) per arm.
- **PR3 — CRR card + timeline CRR signal.** The `-g` group view (GRPROLE Live↔Recovery,
  CONNGRP, group in-sync/backlog) + the cross-region signal on the failover timeline.
- **PR4 — Logs + perf/network.** Loki selector retargeted to the `mqmonitor@QMNATIVE` / AMQERR
  units (off corosync/pacemaker); CPU + net-hb (intra-site raft) + net-wan (CRR) throughput.
- **PR5 — Overview roll-up + drill-link** on `lab-fleet-node` (per-group Native-HA tile →
  `/d/lab-nativeha-cluster`). The one shared-surface PR — sequence last, rebase-on-merge.
- **Cold-rebuild + real-failover acceptance** (spec §7): power-off the Active, watch the matrix
  re-elect, quorum dip to 2/3, the downed node rejoin as in-sync Replica, the timeline record
  it, integrity stay clean (quorum held); a CRR check shows GRPROLE/CONNGRP across the groups.

## Self-review

- **Spec coverage (PR1 scope):** implements §3 (`nativehastate.py` collector +
  `nativeha-state` role + `observability.yml` wiring), §4 (the full `dspmq` → `cluster_*` /
  `cluster_nha_*` mapping table — quorum, role/owner, insync, hastatus, group role/connected/
  insync/backlog, staleness), §6 fail-loud (STALE on no-data, `cluster_quorate` omitted when
  quorum unknown), §7 unit testing (100% branch over every field/state incl. Unknown/no/
  missing + the render projection; fixtures captured from real `dspmq` output). Board layout
  (§5), the §6 integrity reframing in panel form, and §7's cold-rebuild/failover acceptance are
  explicitly deferred to PR2–PR5 above — no silent gaps.
- **Placeholder scan:** none — every code step shows complete code; the only marked-provisional
  content is the two `dspmq` fixtures, which Task 1/2 Step 1 replace with real captures (by
  design, per the sources-are-load-bearing rule).
- **Type consistency:** `parse_nativeha_x -> {quorum_current,quorum_total,group_role,
  instances}`; `parse_nativeha_g -> {group: {role,connected,insync,backlog}}`;
  `render_nativeha_state_prom(*, node, qm, hax, grp, now, fresh_sources)`; `probe(cmd,
  timeout)`; `collect(node, qm, now)`; `_commands(qm) -> {source: (argv, timeout)}` — all used
  consistently across Tasks 1–5. `_fields`/`_m` helpers match `clusterstate`'s shapes.
- **Coverage note:** branches exercised — quorum present vs unknown (T3 tests), role
  Active/Replica/Unknown, insync yes/no, group connected yes/no, all four `probe` outcomes
  (T4), `main` fresh vs all-stale and default node/now (T4). 100% branch reachable.
