"""The MQ event collector's host prep reaches every QM node, HA and DR (#1624).

The collector is an MQ SERVICE object that lives in the queue manager and travels with
it, so after an HA or DR move it STARTCMDs the run.sh wrapper on whichever node now
hosts the QM. That wrapper comes from the mq-event-monitor role's host prep (its
main tasks, not `tasks_from: service`), which therefore has to run on every node of
every QM group. #1624 found two stacks where it skipped the DR site (rdqm_b, pcmk_b):
after a DR cutover the collector could not start and no MQ event reached Loki.

The guard: for every stack in the topology, every QM group (all of its groups except
the SAN groups, which host no QM) is targeted by some play in its provision tree that
runs the host prep, directly or through a role that includes it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
TOPOLOGY = REPO_ROOT / "lab" / "topology.yaml"

# QM groups whose site the provision playbook does not build yet, so no play targets
# them at all. Temporary on purpose: test_pending_qm_groups_are_still_unprovisioned
# fails once a play targets the group, which forces its removal here and puts it under
# the guard below.
PENDING_QM_GROUPS = {"pdrbd_b"}  # pcmk-drbd-ubuntu's DR site (epic .github#310)

_ROLE_VERBS = (
    "ansible.builtin.include_role",
    "include_role",
    "ansible.builtin.import_role",
    "import_role",
)


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _iter_tasks(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _is_host_prep(task: dict[str, Any]) -> bool:
    """An ungated include of mq-event-monitor's main tasks (its host prep)."""
    for verb in _ROLE_VERBS:
        spec = task.get(verb)
        if isinstance(spec, dict) and spec.get("name") == "mq-event-monitor":
            return spec.get("tasks_from", "main") == "main" and "when" not in task
    return False


def _role_runs_host_prep(role: str) -> bool:
    main = ROLES / role / "tasks" / "main.yml"
    return main.exists() and any(_is_host_prep(t) for t in _iter_tasks(_load(main)))


def _play_runs_host_prep(play: dict[str, Any]) -> bool:
    for entry in play.get("roles") or []:
        name = entry.get("role") if isinstance(entry, dict) else entry
        if name == "mq-event-monitor" or _role_runs_host_prep(str(name)):
            return True
    return any(_is_host_prep(t) for t in _iter_tasks(play.get("tasks") or []))


def _plays(provision: Path) -> list[dict[str, Any]]:
    """Every play in a provision playbook and the playbooks it imports, transitively."""
    plays: list[dict[str, Any]] = []
    seen: set[Path] = set()
    todo = [provision]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        for play in _load(current) or []:
            if not isinstance(play, dict):
                continue
            if "import_playbook" in play:
                todo.append(current.parent / play["import_playbook"])
            else:
                plays.append(play)
    return plays


def _targets(play: dict[str, Any], groups: set[str]) -> set[str]:
    """The stack groups a play's `hosts:` names, with dr_enabled at its default (true)."""
    return set(re.findall(r"[a-z][a-z0-9_]*", str(play.get("hosts", "")))) & groups


def _stacks() -> dict[str, dict[str, Any]]:
    stacks = _load(TOPOLOGY)["stacks"]
    assert len(stacks) >= 4, "stack discovery regressed"
    return stacks


def _qm_groups(stack: dict[str, Any]) -> set[str]:
    return {g for g in stack["groups"] if not g.startswith("san_")}


def test_every_qm_group_gets_the_event_collector_host_prep() -> None:
    missing = []
    for name, stack in sorted(_stacks().items()):
        groups = _qm_groups(stack)
        covered: set[str] = set()
        for play in _plays(REPO_ROOT / stack["provision"]):
            if _play_runs_host_prep(play):
                covered |= _targets(play, groups)
        missing += [f"{name}: {g}" for g in sorted(groups - covered - PENDING_QM_GROUPS)]
    assert missing == [], f"QM groups without the mq-event-monitor host prep: {missing}"


def test_every_stack_dr_group_is_a_qm_group() -> None:
    # The DR groups are the ones #1624 found skipped; keep them inside the guard.
    for name, stack in _stacks().items():
        assert set(stack["dr_groups"]) <= _qm_groups(stack), name


def test_pending_qm_groups_are_still_unprovisioned() -> None:
    for name, stack in sorted(_stacks().items()):
        groups = _qm_groups(stack) & PENDING_QM_GROUPS
        for play in _plays(REPO_ROOT / stack["provision"]):
            landed = _targets(play, groups)
            assert not landed, (
                f"{name}: a play now targets {sorted(landed)}; drop it from "
                "PENDING_QM_GROUPS so the host-prep guard covers it"
            )
