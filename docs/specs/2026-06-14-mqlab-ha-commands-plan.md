# `mqlab ha` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `mqlab ha` command group that drives intra-site Pacemaker HA from the CLI — `status`, `failover [--to <node>]`, `standby <node>`, `unstandby <node>`, `recover <node>` — plus the shared topology cluster-resolver both `ha` and `dr` depend on.

**Architecture:** Pure functions resolve *which* cluster a setup's HA ops target (`hacluster.py`) and parse `pcs`/`drbdadm` output (`pcsstatus.py`); thin Typer commands in `cli.py` orchestrate 1–3 `pcs` ops on the resolved cluster's first inventory node via a generalized `_pcs` runner. No Ansible playbooks (these are simple direct-command orchestrations). Spec: `docs/specs/2026-06-14-mqlab-ha-commands-design.md`.

**Tech Stack:** Python 3.12 (Typer, dataclasses, pure-function modules), `ansible` ad-hoc `-m shell` to reach `pcs` on the cluster, pytest + ruff + mypy under `vrg-validate`.

**Conventions (every task):**
- Work in the worktree `.worktrees/issue-175-mqlab-ha-commands/` on branch `feature/175-mqlab-ha-commands`. Use `vrg-git` / `vrg-commit` (raw git/gh blocked).
- Validation is **only** `vrg-container-run -- vrg-validate` (ruff, mypy strict, pytest, **100% branch coverage** on `src/mqlab`). Don't run linters individually.
- **No `uv run`** in any command vector (#164); invoke `ansible` by bare name. **No `community.general`** (#156). **Fail-loud** — never `|| true` / swallow exit codes.
- Pacemaker/Ubuntu only. The live-validation tasks (against the running cluster) are **human-operated** — do not run them; hand them off.

**File structure:**
- Create `src/mqlab/hacluster.py` — cluster resolution (pure) + the DRBD-primary parse.
- Create `src/mqlab/pcsstatus.py` — `pcs status` / constraint parsing (pure) + a render helper.
- Modify `src/mqlab/cli.py` — generalize the pcs runner; add the `ha_app` Typer group + 5 commands.
- Create `tests/test_hacluster.py`, `tests/test_pcsstatus.py`, `tests/test_cli_ha.py`.

---

## Task 1: `pcmk_groups` + `site_to_cluster` (resolver primitives)

**Files:** Create `src/mqlab/hacluster.py`; Test `tests/test_hacluster.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_hacluster.py
from __future__ import annotations

import pytest

from mqlab.hacluster import HaClusterError, pcmk_groups, site_to_cluster


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1, pcmk-a2, pcmk-a3]\n  pcmk_b: [pcmk-b1, pcmk-b2, pcmk-b3]\n"
        "setups:\n"
        "  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
        "  pcmk_san_dr:\n    groups: [san_a, san_b, pcmk_a, pcmk_b]\n"
    )


def test_pcmk_groups_single_cluster(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert pcmk_groups("pcmk_san_ha") == ["pcmk_a"]


def test_pcmk_groups_dr_has_both_in_order(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    assert pcmk_groups("pcmk_san_dr") == ["pcmk_a", "pcmk_b"]


def test_pcmk_groups_unknown_setup_fails_loud(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    with pytest.raises(HaClusterError, match="no lab setup named"):
        pcmk_groups("nope")


def test_site_to_cluster(monkeypatch, tmp_path):
    assert site_to_cluster("a") == "pcmk_a"
    assert site_to_cluster("b") == "pcmk_b"
    with pytest.raises(HaClusterError, match="unknown site"):
        site_to_cluster("c")
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: mqlab.hacluster`)

Run: `vrg-container-run -- uv run pytest tests/test_hacluster.py -v`

- [ ] **Step 3: Implement**

```python
# src/mqlab/hacluster.py
"""Resolve which Pacemaker cluster a setup's HA ops target, from topology.

Single-cluster setups (pcmk_san_ha) have one pcmk_* group. Cross-site DR setups
(pcmk_san_dr) have two; the live one is the DRBD Primary site (the CLI probes it),
overridable with --site. Decisions here are pure; the CLI does the probing.
"""

from __future__ import annotations

from mqlab.setups import lab_setups

_SITE_CLUSTER = {"a": "pcmk_a", "b": "pcmk_b"}


class HaClusterError(RuntimeError):
    """The HA target cluster cannot be resolved."""


def pcmk_groups(setup_name: str) -> list[str]:
    """The pcmk_* inventory groups in a setup, in declared order."""
    setup = lab_setups().get(setup_name)
    if setup is None:
        raise HaClusterError(f"no lab setup named {setup_name!r}")
    return [g for g in setup.groups if g.startswith("pcmk_")]


def site_to_cluster(site: str) -> str:
    """Map a site letter (a|b) to its pcmk cluster group."""
    cluster = _SITE_CLUSTER.get(site)
    if cluster is None:
        raise HaClusterError(f"unknown site {site!r} (expected a or b)")
    return cluster
```

- [ ] **Step 4: Run — expect PASS.** `vrg-container-run -- uv run pytest tests/test_hacluster.py -v`
- [ ] **Step 5: Commit** — `vrg-git add src/mqlab/hacluster.py tests/test_hacluster.py && vrg-commit --type feat --scope ha --message "hacluster: pcmk_groups + site_to_cluster primitives (#175)"`

---

## Task 2: `resolve_cluster` (pure decision: single / site / primary / ambiguous)

**Files:** Modify `src/mqlab/hacluster.py`; Test `tests/test_hacluster.py`

- [ ] **Step 1: Failing tests** (append)

```python
from mqlab.hacluster import resolve_cluster  # add to imports


def test_resolve_single_group_ignores_site_and_primary():
    assert resolve_cluster(["pcmk_a"], site=None, primary_site=None) == "pcmk_a"


def test_resolve_dr_with_explicit_site():
    assert resolve_cluster(["pcmk_a", "pcmk_b"], site="b", primary_site=None) == "pcmk_b"


def test_resolve_dr_falls_back_to_detected_primary():
    assert resolve_cluster(["pcmk_a", "pcmk_b"], site=None, primary_site="a") == "pcmk_a"


def test_resolve_dr_site_overrides_primary():
    assert resolve_cluster(["pcmk_a", "pcmk_b"], site="a", primary_site="b") == "pcmk_a"


def test_resolve_dr_ambiguous_fails_loud():
    with pytest.raises(HaClusterError, match="ambiguous"):
        resolve_cluster(["pcmk_a", "pcmk_b"], site=None, primary_site=None)


def test_resolve_site_not_in_setup_fails_loud():
    with pytest.raises(HaClusterError, match="not in setup"):
        resolve_cluster(["pcmk_a"], site="b", primary_site=None)  # single-group path returns pcmk_a first
```

Note: the last test documents that a single-group setup short-circuits to its one group; rewrite it to exercise the guard on a two-group setup missing the requested site:

```python
def test_resolve_requested_site_absent_fails_loud():
    with pytest.raises(HaClusterError, match="not in setup"):
        resolve_cluster(["pcmk_a", "pcmk_b"], site="c", primary_site=None)
```

(Delete `test_resolve_site_not_in_setup_fails_loud`; keep `test_resolve_requested_site_absent_fails_loud`.)

- [ ] **Step 2: Run — expect FAIL** (`ImportError: resolve_cluster`)

- [ ] **Step 3: Implement** (append to `hacluster.py`)

```python
def resolve_cluster(groups: list[str], site: str | None, primary_site: str | None) -> str:
    """Pick the target cluster group (pure).

    - exactly one pcmk group -> it (site/primary irrelevant);
    - else an explicit `site` wins, then a detected `primary_site`;
    - else ambiguous -> raise (the caller tells the operator to pass --site).
    """
    if not groups:
        raise HaClusterError("setup has no pcmk_* cluster group")
    if len(groups) == 1:
        return groups[0]
    chosen = site or primary_site
    if chosen is None:
        raise HaClusterError("ambiguous DR setup — specify --site {a|b}")
    cluster = site_to_cluster(chosen)
    if cluster not in groups:
        raise HaClusterError(f"site {chosen!r} cluster {cluster!r} not in setup groups {groups}")
    return cluster
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "hacluster: resolve_cluster decision (single/site/primary/ambiguous) (#175)"`

---

## Task 3: `parse_drbd_primary_site` (DRBD-role → live site, pure)

**Files:** Modify `src/mqlab/hacluster.py`; Test `tests/test_hacluster.py`

- [ ] **Step 1: Failing tests** (append)

```python
from mqlab.hacluster import parse_drbd_primary_site  # add to imports


def test_drbd_primary_site_a():
    roles = {"san-a": "Primary/Secondary", "san-b": "Secondary/Primary"}
    assert parse_drbd_primary_site(roles) == "a"


def test_drbd_primary_site_b():
    roles = {"san-a": "Secondary", "san-b": "Primary"}
    assert parse_drbd_primary_site(roles) == "b"


def test_drbd_no_single_primary_is_none():
    assert parse_drbd_primary_site({"san-a": "Secondary", "san-b": "Secondary"}) is None
    assert parse_drbd_primary_site({"san-a": "Primary", "san-b": "Primary"}) is None
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** (append)

```python
_SAN_SITE = {"san-a": "a", "san-b": "b"}


def parse_drbd_primary_site(role_by_san: dict[str, str]) -> str | None:
    """Given {san-host: `drbdadm role` output}, the site (a|b) whose SAN is the
    sole DRBD Primary, or None if not exactly one (ambiguous → caller wants --site).
    """
    primary = [
        _SAN_SITE[h]
        for h, out in role_by_san.items()
        if h in _SAN_SITE and out.strip().startswith("Primary")
    ]
    return primary[0] if len(primary) == 1 else None
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "hacluster: parse DRBD Primary -> live site (#175)"`

---

## Task 4: `pcs status` parser + move-ban detector

**Files:** Create `src/mqlab/pcsstatus.py`; Test `tests/test_pcsstatus.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_pcsstatus.py
from __future__ import annotations

from mqlab.pcsstatus import ClusterStatus, has_move_ban, parse_pcs_status

_STATUS = """\
Cluster name: mqpcmk
Node List:
  * Online: [ pcmk-a1 pcmk-a2 ]
  * Standby: [ pcmk-a3 ]

Full List of Resources:
  * Resource Group: mq_group:
    * mq_fs\t(ocf:heartbeat:Filesystem):\tStarted pcmk-a1
    * mq_vip\t(ocf:heartbeat:IPaddr2):\tStarted pcmk-a1
    * mq_qm\t(systemd:mq-QMPCMK):\tStarted pcmk-a1
"""


def test_parse_node_states():
    st = parse_pcs_status(_STATUS)
    assert st.nodes == {"pcmk-a1": "online", "pcmk-a2": "online", "pcmk-a3": "standby"}


def test_parse_group_node_from_mq_qm():
    assert parse_pcs_status(_STATUS).group_node == "pcmk-a1"


def test_parse_group_node_none_when_stopped():
    txt = _STATUS.replace("Started pcmk-a1", "Stopped").replace(
        "Started pcmk-a1", "Stopped"
    )
    assert parse_pcs_status(txt).group_node is None


def test_has_move_ban_detects_cli_ban():
    constraints = "Location Constraints:\n  cli-ban-mq_group-on-pcmk-a1 ... -INFINITY\n"
    assert has_move_ban(constraints, "mq_group") is True


def test_has_move_ban_absent():
    assert has_move_ban("Location Constraints:\n", "mq_group") is False
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: mqlab.pcsstatus`).

- [ ] **Step 3: Implement**

```python
# src/mqlab/pcsstatus.py
"""Pure parsing of `pcs status` / `pcs constraint` text into structured state."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClusterStatus:
    nodes: dict[str, str]  # node -> online|standby|offline
    group_node: str | None  # node running the QM (mq_qm), or None


_NODE_LINE = {
    "online": re.compile(r"Online:\s*\[([^\]]*)\]"),
    "standby": re.compile(r"Standby:\s*\[([^\]]*)\]"),
    "offline": re.compile(r"OFFLINE:\s*\[([^\]]*)\]", re.IGNORECASE),
}
_MQ_QM = re.compile(r"mq_qm\b.*\bStarted\s+(\S+)")


def parse_pcs_status(text: str) -> ClusterStatus:
    nodes: dict[str, str] = {}
    for state, pat in _NODE_LINE.items():
        m = pat.search(text)
        if m:
            for name in m.group(1).split():
                nodes[name] = state
    qm = _MQ_QM.search(text)
    return ClusterStatus(nodes=nodes, group_node=qm.group(1) if qm else None)


def has_move_ban(constraint_text: str, resource: str) -> bool:
    """True if a `pcs resource move` location ban (cli-ban-<resource>-on-…) exists."""
    return f"cli-ban-{resource}-on-" in constraint_text
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "pcsstatus: parse pcs status + detect move-ban (#175)"`

---

## Task 5: Generalize the pcs runner in `cli.py`

Today `_qm_pcs` hardcodes `_PCMK_CLUSTER_GROUP[0]`. Extract a `_pcs` helper that takes the cluster group, and capture-mode for reads. `_qm_pcs` keeps working unchanged.

**Files:** Modify `src/mqlab/cli.py`; Test: existing `tests/test_cli_qm.py` must stay green.

- [ ] **Step 1: Add `_pcs` and refactor `_qm_pcs`** (replace the body of `_qm_pcs`, keep the hardcoded group there)

```python
def _pcs(cluster_group: str, pcs_cmd: str, verb: str, *, capture: bool = False) -> str:
    """Run one `pcs` op on the cluster's first inventory node, fail-loud.

    Returns captured stdout when capture=True (for status/verify), else "".
    """
    deps = build_deps(verb, datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
    out: list[str] = []
    sink = out.append if capture else None
    try:
        _render_inventory(deps)
        cmd = Command(
            ["ansible", f"{cluster_group}[0]", "-b", "-m", "shell", "-a", pcs_cmd],  # noqa: S607
            cwd=repo_root() / "ansible",
        )
        if capture:
            deps.runner.run(cmd, out.append)
        else:
            run_steps(
                [CommandStep(f"{verb}", cmd)],
                runner=deps.runner,
                renderer=deps.renderer,
                transcript=deps.transcript,
                step_mode=False,
                pauser=deps.pauser,
            )
    except StepFailedError as exc:
        raise typer.Exit(code=exc.exit_code) from exc
    finally:
        deps.transcript.close()
    return "\n".join(out)


def _qm_pcs(setup_name: str, pcs_cmd: str, verb: str) -> None:
    _setup_qm_or_exit(setup_name)
    _pcs(_PCMK_CLUSTER_GROUP, pcs_cmd, verb)
```

- [ ] **Step 2: Run — expect existing qm tests still PASS.** `vrg-container-run -- uv run pytest tests/test_cli_qm.py -v`
  Expected: green (the qm verbs still issue the same `ansible pcmk_a[0] … pcs …`).
- [ ] **Step 3: Commit** — `vrg-commit --type refactor --scope ha --message "cli: extract _pcs(cluster_group, …) runner from _qm_pcs (#175)"`

---

## Task 6: `_ha_cluster` CLI resolver (probes DRBD for DR setups)

**Files:** Modify `src/mqlab/cli.py`; Test `tests/test_cli_ha.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_cli_ha.py
from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_HA_TOPO = (
    "nodes:\n"
    "  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
    "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
)


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("ha", "20260614T000000Z")),
        pauser=_NoPause(),
    )


def _seed(monkeypatch, tmp_path, topo=_HA_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def test_ha_standby_single_cluster_runs_pcs_on_pcmk_a(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "standby", "pcmk_san_ha", "pcmk-a1"])
    assert result.exit_code == 0
    argv = runner.recorded[-1].argv
    assert argv[:2] == ["ansible", "pcmk_a[0]"]
    assert argv[-1] == "pcs node standby pcmk-a1"
```

- [ ] **Step 2: Run — expect FAIL** (no `ha` command yet).

- [ ] **Step 3: Implement the resolver + group skeleton** (in `cli.py`)

```python
ha_app = typer.Typer(help="intra-site Pacemaker HA operations", no_args_is_help=True)
app.add_typer(ha_app, name="ha")

_SAN_GROUP_HOSTS = {"a": "san-a", "b": "san-b"}


def _ha_cluster(setup_name: str, site: str | None) -> str:
    """Resolve the target pcmk cluster group for an HA op (fail-loud via typer.Exit)."""
    from mqlab.hacluster import (
        HaClusterError,
        parse_drbd_primary_site,
        pcmk_groups,
        resolve_cluster,
    )

    try:
        groups = pcmk_groups(setup_name)
        primary = None
        if len(groups) > 1 and site is None:
            # DR setup, no explicit site: probe DRBD role on both SANs.
            deps = build_deps("ha-detect", datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ"))
            roles: dict[str, str] = {}
            try:
                _render_inventory(deps)
                for letter, san in _SAN_GROUP_HOSTS.items():
                    out: list[str] = []
                    deps.runner.run(
                        Command(
                            ["ansible", san, "-b", "-m", "shell", "-a", "drbdadm role mqlun"],  # noqa: S607
                            cwd=repo_root() / "ansible",
                        ),
                        out.append,
                    )
                    roles[san] = "\n".join(out)
            finally:
                deps.transcript.close()
            primary = parse_drbd_primary_site(roles)
        return resolve_cluster(groups, site, primary)
    except HaClusterError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
```

(`standby` itself is Task 7; this task delivers the resolver + the group, validated through the standby test above — so implement a minimal `standby` here to satisfy the test, then flesh siblings in Task 7. To keep tasks atomic, fold the minimal `standby` command in now:)

```python
_HA_RESOURCE = "mq_group"


@ha_app.command("standby")
def ha_standby(setup: str, node: str, site: _SiteOpt = None) -> None:
    """Drain a node for maintenance (pcs node standby)."""
    _pcs(_ha_cluster(setup, site), f"pcs node standby {node}", "ha-standby")
```

Add the shared option type near the other Typer option aliases:

```python
_SiteOpt = Annotated[str | None, typer.Option("--site", help="DR setup: site a|b")]
```

- [ ] **Step 4: Run — expect PASS.** `vrg-container-run -- uv run pytest tests/test_cli_ha.py -v`
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "ha: cluster resolver (+DRBD probe) and standby (#175)"`

---

## Task 7: `unstandby` + node validation

**Files:** Modify `src/mqlab/cli.py`; Test `tests/test_cli_ha.py`

- [ ] **Step 1: Failing tests** (append)

```python
def test_ha_unstandby_runs_pcs_unstandby(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "unstandby", "pcmk_san_ha", "pcmk-a1"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs node unstandby pcmk-a1"


def test_ha_unknown_node_exits_2_without_pcs(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "standby", "pcmk_san_ha", "pcmk-zz"])
    assert result.exit_code == 2
    assert "unknown node" in result.output
    assert runner.recorded == []  # validated before any pcs call
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** node validation + `unstandby`

Add a node guard and call it from `standby`/`unstandby`:

```python
def _require_cluster_node(cluster_group: str, node: str) -> None:
    from mqlab.setups import lab_groups

    members = lab_groups().get(cluster_group, [])
    if node not in members:
        typer.echo(f"unknown node {node!r} in cluster {cluster_group} (have: {members})", err=True)
        raise typer.Exit(code=2)


@ha_app.command("unstandby")
def ha_unstandby(setup: str, node: str, site: _SiteOpt = None) -> None:
    """Return a node to service (pcs node unstandby)."""
    cluster = _ha_cluster(setup, site)
    _require_cluster_node(cluster, node)
    _pcs(cluster, f"pcs node unstandby {node}", "ha-unstandby")
```

Update `ha_standby` to validate too:

```python
@ha_app.command("standby")
def ha_standby(setup: str, node: str, site: _SiteOpt = None) -> None:
    """Drain a node for maintenance (pcs node standby)."""
    cluster = _ha_cluster(setup, site)
    _require_cluster_node(cluster, node)
    _pcs(cluster, f"pcs node standby {node}", "ha-standby")
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "ha: unstandby + node validation (#175)"`

---

## Task 8: `failover` — move (`--wait`) then clear

**Files:** Modify `src/mqlab/cli.py`; Test `tests/test_cli_ha.py`

- [ ] **Step 1: Failing tests** (append)

```python
def test_ha_failover_no_target_moves_off_then_clears(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "failover", "pcmk_san_ha"])
    assert result.exit_code == 0
    cmds = [c.argv[-1] for c in runner.recorded]
    assert cmds == ["pcs resource move mq_group --wait=60", "pcs resource clear mq_group"]


def test_ha_failover_to_node_targets_that_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "failover", "pcmk_san_ha", "--to", "pcmk-a1"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[-1] == "pcs resource move mq_group pcmk-a1 --wait=60"


def test_ha_failover_clears_even_after_move_then_reports(monkeypatch, tmp_path):
    # move succeeds, clear runs; clear failure propagates loud
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "failover", "pcmk_san_ha"])
    assert result.exit_code == 1
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
@ha_app.command("failover")
def ha_failover(setup: str, to: _ToOpt = None, site: _SiteOpt = None) -> None:
    """Planned move of the QM group to another node, then clear the move-ban.

    `pcs resource move` leaves a -INFINITY ban that must be cleared or future
    automatic HA breaks (#175). move --wait blocks until relocation completes.
    """
    cluster = _ha_cluster(setup, site)
    if to is not None:
        _require_cluster_node(cluster, to)
    target = f" {to}" if to else ""
    _pcs(cluster, f"pcs resource move {_HA_RESOURCE}{target} --wait=60", "ha-failover-move")
    _pcs(cluster, f"pcs resource clear {_HA_RESOURCE}", "ha-failover-clear")
```

Add the option alias near `_SiteOpt`:

```python
_ToOpt = Annotated[str | None, typer.Option("--to", help="move the QM group to this node")]
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "ha: failover = move --wait then clear the ban (#175)"`

---

## Task 9: `recover` — cleanup failcounts + stonith

**Files:** Modify `src/mqlab/cli.py`; Test `tests/test_cli_ha.py`

- [ ] **Step 1: Failing tests** (append)

```python
def test_ha_recover_cleans_resource_and_stonith(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "recover", "pcmk_san_ha", "pcmk-a1"])
    assert result.exit_code == 0
    cmds = [c.argv[-1] for c in runner.recorded]
    assert cmds == [
        "pcs resource cleanup --node pcmk-a1",
        "pcs stonith cleanup",
    ]
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement**

```python
@ha_app.command("recover")
def ha_recover(setup: str, node: str, site: _SiteOpt = None) -> None:
    """Bring a fenced/failed node back: clear failcounts + stonith history."""
    cluster = _ha_cluster(setup, site)
    _require_cluster_node(cluster, node)
    _pcs(cluster, f"pcs resource cleanup --node {node}", "ha-recover-cleanup")
    _pcs(cluster, "pcs stonith cleanup", "ha-recover-stonith")
```

> **Live-validation note (human-operated, §10 of the spec):** the real fenced-node
> sequence may also need `pcs cluster start <node>`. Confirm against a genuinely
> fenced node; if so, prepend it (and this verb may graduate to a small playbook).
> Do NOT add it speculatively — that's the iterate-once-operated boundary.

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "ha: recover (resource + stonith cleanup) (#175)"`

---

## Task 10: `status` — probe, parse, render (+ stale-ban warning)

**Files:** Modify `src/mqlab/cli.py`, `src/mqlab/pcsstatus.py`; Test `tests/test_cli_ha.py`, `tests/test_pcsstatus.py`

- [ ] **Step 1: Failing test** (append to `tests/test_cli_ha.py`)

```python
def test_ha_status_probes_status_and_constraints_and_renders(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    status = (
        "Node List:\n  * Online: [ pcmk-a1 ]\n\n"
        "Full List of Resources:\n  * Resource Group: mq_group:\n"
        "    * mq_qm\t(systemd:mq-QMPCMK):\tStarted pcmk-a1\n"
    )
    runner = RecordingRunner(results=[ScriptedResult(status.splitlines()), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["ha", "status", "pcmk_san_ha"])
    assert result.exit_code == 0
    cmds = [c.argv[-1] for c in runner.recorded]
    assert cmds[0] == "pcs status"
    assert cmds[1].startswith("pcs constraint")
    assert "pcmk-a1" in result.stdout  # rendered the QM's node
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement** a render helper in `pcsstatus.py`:

```python
def render_status(st: ClusterStatus, move_ban: bool) -> str:
    lines = [f"QM running on: {st.group_node or 'STOPPED'}"]
    for node, state in sorted(st.nodes.items()):
        lines.append(f"  {node}: {state}")
    if move_ban:
        lines.append("WARNING: a stale `pcs resource move` ban is present — run `mqlab ha failover` to clear, or `pcs resource clear mq_group`.")
    return "\n".join(lines)
```

and the command in `cli.py`:

```python
@ha_app.command("status")
def ha_status(setup: str, site: _SiteOpt = None) -> None:
    """Show which node runs the QM, node states, and any stale move-ban."""
    from mqlab.pcsstatus import has_move_ban, parse_pcs_status, render_status

    cluster = _ha_cluster(setup, site)
    status_txt = _pcs(cluster, "pcs status", "ha-status", capture=True)
    constraint_txt = _pcs(cluster, "pcs constraint --full", "ha-status", capture=True)
    st = parse_pcs_status(status_txt)
    typer.echo(render_status(st, has_move_ban(constraint_txt, _HA_RESOURCE)))
```

Add a `render_status` test to `tests/test_pcsstatus.py`:

```python
def test_render_status_includes_node_and_ban_warning():
    st = ClusterStatus(nodes={"pcmk-a1": "online"}, group_node="pcmk-a1")
    out = render_status(st, move_ban=True)
    assert "pcmk-a1" in out and "WARNING" in out
```
(import `render_status` at the top of `tests/test_pcsstatus.py`.)

- [ ] **Step 4: Run — expect PASS.** `vrg-container-run -- uv run pytest tests/test_cli_ha.py tests/test_pcsstatus.py -v`
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope ha --message "ha: status (probe + parse + render, stale-ban warning) (#175)"`

---

## Task 11: Full validation + PR

- [ ] **Step 1: Full gate** — `vrg-container-run -- vrg-validate`
  Expected: ruff/mypy clean, all tests pass, **100% branch coverage** on the new modules. If `_ha_cluster`'s DR-probe branch is uncovered, add a `tests/test_cli_ha.py` case on a two-cluster `pcmk_san_dr` topology that scripts the two `drbdadm role` results (san-a Primary) and asserts the op runs on `pcmk_a[0]`; plus a `--site b` case that skips the probe and targets `pcmk_b[0]`; plus an ambiguous case (both Secondary) that exits 2.
- [ ] **Step 2: Confirm no `uv run` / no `community.general` introduced** — `grep -rn 'uv run\|community.general' src/mqlab/cli.py src/mqlab/hacluster.py src/mqlab/pcsstatus.py` → nothing.
- [ ] **Step 3: Push + hand off PR** — `vrg-git push -u origin feature/175-mqlab-ha-commands`; then the human runs `vrg-submit-pr` (agents can't submit).

---

## Task 12: Live validation (human-operated)

Hand this to the operator — it drives the real `pcmk_san_ha` (or live `pcmk_san_dr`) cluster:

- [ ] `mqlab ha status pcmk_san_ha` — shows the QM's node + node states; no stale-ban warning on a clean cluster.
- [ ] `mqlab ha failover pcmk_san_ha` — QM relocates; **re-run `ha status` and confirm no lingering move-ban** (the clear worked).
- [ ] `mqlab ha standby pcmk_san_ha <active-node>` → QM drains off it; `ha unstandby` returns it.
- [ ] On the live `pcmk_san_dr`: `mqlab ha status pcmk_san_dr` auto-detects the live site (DRBD Primary); `--site b` targets the standby cluster explicitly.
- [ ] `mqlab ha recover` against a genuinely fenced node — confirm whether `pcs cluster start <node>` is also needed (Task 9 note) and feed any refinement back into the spec (§10).

---

## Self-review

**Spec coverage:** §2 surface → Tasks 6–10 (all five verbs). §3 CLI-direct/`_pcs` → Task 5. §4 cluster resolver (single/site/primary, `--site`, DRBD detect) → Tasks 1–3, 6, 11. §5 failover move→verify(`--wait`)→clear + ban warning → Tasks 8, 10. §6 standby/unstandby/recover → Tasks 7, 9. §7 status parse/render + stale-ban → Tasks 4, 10. §8 testing (pure + RecordingRunner + live) → throughout + Task 12. §9 scope (no maintenance-mode, no failure-injection) → honored. §10 iteration items → flagged in Tasks 9, 12. ✓

**Placeholders:** none — every step has real code/commands. The Task 6 note about folding `standby` in early is explicit, not a TODO.

**Type/name consistency:** `pcmk_groups`/`site_to_cluster`/`resolve_cluster`/`parse_drbd_primary_site` (hacluster); `parse_pcs_status`/`has_move_ban`/`render_status`/`ClusterStatus` (pcsstatus); `_pcs(cluster_group, pcs_cmd, verb, *, capture)`, `_ha_cluster(setup, site)`, `_require_cluster_node`, `_HA_RESOURCE="mq_group"`, `_SiteOpt`/`_ToOpt` (cli) — used consistently across tasks. `_pcs` capture-mode uses `deps.runner.run(cmd, sink)`; confirm against `tests/fakes.RecordingRunner.run(cmd, sink)` signature when implementing Task 5 (mirror how `obs net-state` captures).
