"""Guard: Native HA provision changes follow the Active, never a pinned host (#891).

runmqsc and setmqaut only work on a Native HA group's Active instance, and raft can
re-elect at any moment. The provision plays used to resolve the Active once, record it
as ``active_host``, and delegate every later change to it; when leadership moved in
between, every retry hit the now-Replica (AMQ8478E) and the bring-up failed with a
healthy queue manager. The fix routes every change through
``ansible/tasks/nativeha-on-active.yml``, which re-detects the Active on each attempt.

What this pins (structure only; the behaviour is proven by the cold-rebuild gate):

- The two Native HA provision playbooks never name ``active_host`` themselves: no
  ``delegate_to`` or ``when`` pinned to a detection made earlier in the play.
- Each of them applies its MQSC and the event-monitor role through the runner, and the
  Ubuntu arm also its authorization posture (authz MQSC, setmqaut grants, REFRESH).
- Active detection lives in ONE task file: no other playbook parses ``ROLE(Active)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ANSIBLE = Path(__file__).resolve().parents[1] / "ansible"
RUNNER = "tasks/nativeha-on-active.yml"
DETECT = "tasks/nativeha-find-active.yml"
INCLUDE_TASKS = ("ansible.builtin.include_tasks", "include_tasks")

PROVISION = {
    "site-nativeha.yml": ("nha_rhel_crr_a", {"our-side + app MQSC", "event monitoring"}),
    "site-nativeha-ubuntu.yml": (
        "nha_ubuntu_a",
        {"our-side + app MQSC", "authorization posture", "event monitoring"},
    ),
}


def _tasks(playbook: str, group: str) -> list[dict[str, Any]]:
    plays = yaml.safe_load((ANSIBLE / playbook).read_text(encoding="utf-8"))
    for play in plays:
        if play.get("hosts") == group and play.get("tasks"):
            return play["tasks"]
    raise AssertionError(f"{playbook}: no task play on {group}")


def _include_target(task: dict[str, Any]) -> str | None:
    for key in INCLUDE_TASKS:
        if key in task:
            spec = task[key]
            return spec["file"] if isinstance(spec, dict) else spec
    return None


@pytest.mark.parametrize("playbook", sorted(PROVISION))
def test_provision_play_never_pins_the_active(playbook: str) -> None:
    group, _ = PROVISION[playbook]
    text = yaml.safe_dump(_tasks(playbook, group))
    assert "active_host" not in text, f"{playbook}: a task pins the Active (#891)"


@pytest.mark.parametrize("playbook", sorted(PROVISION))
def test_provision_changes_go_through_the_runner(playbook: str) -> None:
    group, labels = PROVISION[playbook]
    runs = {
        t["vars"]["nha_label"]: t["vars"]
        for t in _tasks(playbook, group)
        if _include_target(t) == RUNNER
    }
    assert set(runs) == labels
    for label, unit in runs.items():
        assert unit["nha_group"] == group, f"{playbook}: {label} targets the wrong group"


def test_active_detection_lives_in_one_task_file() -> None:
    patterns = ("ROLE[(]Active[)]", r"ROLE\(Active\)")
    parsers = sorted(
        str(p.relative_to(ANSIBLE))
        for p in ANSIBLE.rglob("*.yml")
        if any(pattern in p.read_text(encoding="utf-8") for pattern in patterns)
    )
    assert parsers == [DETECT]


@pytest.mark.parametrize(
    "playbook", ["validate-nativeha-log-lifecycle.yml", "site-nativeha-ubuntu-authz-validate.yml"]
)
def test_validation_plays_use_the_shared_detection(playbook: str) -> None:
    plays = yaml.safe_load((ANSIBLE / playbook).read_text(encoding="utf-8"))
    targets = [_include_target(t) for play in plays for t in play.get("tasks") or []]
    assert DETECT in targets
