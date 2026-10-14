"""Pin rdqm-ha's one-node-at-a-time ``rdqmadm -c`` and its STONITH-setup check (#1488).

RDQM's per-site cluster-wide setup (``stonith-enabled=false`` among it) is done by
whichever ``rdqmadm -c`` holds the ``rdqm-lock`` cluster attribute. That lock is a
query, an update and a read-back that checks only that the attribute exists, so two
nodes that start within milliseconds of each other both believe they hold it and both
skip the setup. The role used to run ``rdqmadm -c`` on all of a site's nodes at once:
in validation #1475 site B came up without ``stonith-enabled=false`` and ``crtmqm``
failed with AMQ3817E ("no STONITH resources have been defined").

The role now runs ``rdqmadm -c`` one node at a time, then proves every node sees
``stonith-enabled`` = ``false``, so a skipped setup fails here rather than at
``crtmqm``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN = REPO_ROOT / "ansible" / "roles" / "rdqm-ha" / "tasks" / "main.yml"

INIT = "initialize the replicated data subsystem (idempotent)"
READ = "read the site cluster's stonith-enabled property"
CHECK = "RDQM's cluster-wide setup ran: stonith-enabled is false (fail loud)"

# Keys that would let a failing task pass quietly.
SOFTENERS = ("ignore_errors", "failed_when", "rescue", "until")


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = yaml.safe_load(MAIN.read_text(encoding="utf-8"))
    return tasks


def _task(name: str) -> dict[str, Any]:
    (task,) = [t for t in _tasks() if t.get("name") == name]
    return task


def test_rdqmadm_runs_one_node_at_a_time() -> None:
    task = _task(INIT)
    assert "rdqmadm -c" in task["ansible.builtin.shell"]
    assert task.get("throttle") == 1


def test_stonith_check_follows_init() -> None:
    names = [t.get("name") for t in _tasks()]
    assert names.index(INIT) < names.index(READ) < names.index(CHECK)


def test_stonith_property_is_read_from_the_live_cluster() -> None:
    task = _task(READ)
    argv = task["ansible.builtin.command"]["argv"]
    assert argv[0] == "crm_attribute"
    assert argv[1:3] == ["--type", "crm_config"]
    assert argv[3:5] == ["--name", "stonith-enabled"]
    assert "--query" in argv
    # an absent property must reach the assert as a value, not fail the read
    assert argv[argv.index("--default") + 1] == "unset"
    assert task.get("changed_when") is False
    assert task.get("become") is True
    assert task.get("register")


def test_stonith_check_fails_loud() -> None:
    read = _task(READ)
    check = _task(CHECK)
    assert read["register"] in str(check["ansible.builtin.assert"]["that"])
    assert "false" in str(check["ansible.builtin.assert"]["that"])
    assert check["ansible.builtin.assert"].get("fail_msg")
    for task in (read, check):
        for key in SOFTENERS:
            assert key not in task, f"{task['name']!r} must not carry {key!r}"
