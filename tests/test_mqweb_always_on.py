"""mqweb runs on every QM node, ungated (epic .github#341 spec §2.1, #1502).

This replaces the #1171/#1188 guard that kept per-node mqweb off by default. The
spike (#1499, docs/reference/mqweb-spike-findings.md) measured per-node mqweb as
affordable, so the gate and its group_vars flag are gone. These guards keep it that
way:

- every play that carries the mqweb role carries it as a bare, ungated `- mqweb`;
- the mq-qmgr role (svc-sim's SVCQM) includes mqweb ungated;
- every stack's provision playbook reaches an mqweb play, so a new QM arm cannot
  silently ship without the Console;
- the old flag name appears nowhere in the provisioning tree.

Both invocation shapes are scanned, as the old guard did: top-level `roles:` entries
and `include_role`/`import_role` tasks inside roles or playbooks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
TOPOLOGY = REPO_ROOT / "lab" / "topology.yaml"
SHARED = ANSIBLE / "site-distributed-shared.yml"
MQ_QMGR_TASKS = ANSIBLE / "roles" / "mq-qmgr" / "tasks" / "main.yml"

# Every play file carrying the mqweb role as of #1502. Exact equality below: a new
# mqweb play must be added here, and so reviewed for the provision env it needs.
STACK_PLAYS = {
    "_nativeha-cluster-ha.yml",
    "_nativeha-dr-replication.yml",
    "_nativeha-ubuntu-cluster-ha.yml",
    "_nativeha-ubuntu-dr-replication.yml",
    "_pcmk-cluster-ha.yml",
    "_pcmk-dr-replication.yml",
    "_pdrbd-cluster-ha.yml",  # pcmk-drbd-ubuntu site A (#1427)
    "_pdrbd-dr-replication.yml",  # pcmk-drbd-ubuntu site B (#1428)
    "_rdqm-cluster-ha.yml",
    "_rdqm-dr-replication.yml",
}

# Stacks declared in the topology whose provision playbook has not landed yet. Such a
# stack is not bootstrappable, so it hosts no QM to guard. The entry is temporary on
# purpose: test_pending_provision_playbooks_are_still_absent fails the moment the
# playbook lands, which forces its removal here and puts it under the guards below
# (the same pattern as tests/test_mqweb_operator_authz.py).
# Empty since #1427 landed ansible/site-pdrbd.yml (pcmk-drbd-ubuntu).
PENDING_PROVISION: set[str] = set()

_ROLE_VERBS = (
    "ansible.builtin.include_role",
    "include_role",
    "ansible.builtin.import_role",
    "import_role",
)


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _plays(path: Path) -> list[dict[str, Any]]:
    doc = _load(path)
    return [p for p in doc if isinstance(p, dict)] if isinstance(doc, list) else []


def _mqweb_role_entries(path: Path) -> list[Any]:
    """Every `roles:` entry that pulls in the mqweb role, across all plays in a file."""
    return [
        entry
        for play in _plays(path)
        for entry in play.get("roles") or []
        if entry == "mqweb" or (isinstance(entry, dict) and entry.get("role") == "mqweb")
    ]


def _iter_tasks(node: Any) -> Any:
    """Every task dict, descending into block/rescue/always and play task lists."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _mqweb_role_tasks(path: Path) -> list[dict[str, Any]]:
    """Every include_role/import_role task in a file that pulls in the mqweb role."""
    return [
        task
        for task in _iter_tasks(_load(path))
        if any(
            isinstance(task.get(verb), dict) and task[verb].get("name") == "mqweb"
            for verb in _ROLE_VERBS
        )
    ]


def _playbooks() -> list[Path]:
    files = sorted(ANSIBLE.glob("*.yml"))
    assert files, f"no playbooks found under {ANSIBLE}"
    return files


def _imports(path: Path) -> set[Path]:
    """The playbooks a playbook pulls in, transitively, including itself."""
    seen: set[Path] = set()
    todo = [path]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        doc = _load(current)
        for play in doc if isinstance(doc, list) else []:
            if isinstance(play, dict) and "import_playbook" in play:
                todo.append(current.parent / play["import_playbook"])
    return seen


def _provision_playbooks() -> set[Path]:
    topo = _load(TOPOLOGY)
    declared = {s["provision"] for s in topo["stacks"].values()}
    return {REPO_ROOT / p for p in declared - PENDING_PROVISION}


def test_the_mqweb_plays_are_exactly_the_stack_plays() -> None:
    carrying = {pb.name for pb in _playbooks() if _mqweb_role_entries(pb)}
    assert carrying == STACK_PLAYS


def test_every_stack_play_runs_mqweb_ungated() -> None:
    for name in sorted(STACK_PLAYS):
        entries = _mqweb_role_entries(ANSIBLE / name)
        assert entries, f"{name}: mqweb role missing"
        for entry in entries:
            assert entry == "mqweb", f"{name}: mqweb must be a bare `- mqweb`, got {entry!r}"


def test_svc_sim_mq_qmgr_runs_mqweb_ungated() -> None:
    tasks = _mqweb_role_tasks(MQ_QMGR_TASKS)
    assert len(tasks) == 1, tasks
    assert "when" not in tasks[0], tasks[0]


def test_no_role_or_playbook_includes_mqweb_conditionally() -> None:
    files = sorted((ANSIBLE / "roles").rglob("tasks/*.yml")) + _playbooks()
    for path in files:
        for task in _mqweb_role_tasks(path):
            assert "when" not in task, f"{path.relative_to(REPO_ROOT)}: gated mqweb {task!r}"


def test_pending_provision_playbooks_are_still_absent() -> None:
    for rel in sorted(PENDING_PROVISION):
        assert not (REPO_ROOT / rel).exists(), (
            f"{rel} has landed: drop it from PENDING_PROVISION so the mqweb guards cover "
            "it (its QM nodes must run the mqweb role, ungated)"
        )


def test_every_stack_provision_reaches_an_mqweb_play() -> None:
    # Every bootstrappable stack runs mqweb on its QM nodes, and its svc-sim import
    # runs it on SVCQM through mq-qmgr.
    playbooks = _provision_playbooks()
    assert len(playbooks) >= 4, "stack discovery regressed"
    for pb in sorted(playbooks):
        reached = _imports(pb)
        assert {p.name for p in reached} & STACK_PLAYS, f"{pb.name} runs no mqweb play"
        assert SHARED in reached, f"{pb.name} does not import {SHARED.name}"
    assert any(play.get("roles") and "mq-qmgr" in play["roles"] for play in _plays(SHARED))


def test_the_old_gate_is_gone() -> None:
    flag = "mqweb" + "_enabled"
    hits = [
        str(path.relative_to(REPO_ROOT))
        for tree in ("ansible", "src", "lab")
        for path in sorted((REPO_ROOT / tree).rglob("*"))
        if path.is_file() and flag in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == []
