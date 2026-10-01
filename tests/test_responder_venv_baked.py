"""Guard the baked svc-responder pymqi venv (#1227, epic .github#275).

The provision play used to build the responder's pymqi venv on svc-sim every bootstrap
(~9s venv create + ~39s PyPI fetch and C build). pymqi changes rarely, so the venv is
now baked into the mq-ubuntu2404 box via mq-inter-qm's install half, and the per-run
role re-imports that half as a near no-op. These guards fail loudly if the bake stops
building the venv, builds it before MQ is installed (pymqi compiles against the MQ SDK),
or if the per-run half stops being idempotent against a baked venv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "mq-inter-qm"
BAKE = ANSIBLE / "bake-mq-ubuntu.yml"
VENV_VAR = "mq_inter_qm_responder_venv"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _bake_tasks() -> list[dict[str, Any]]:
    plays = _load(BAKE)
    assert isinstance(plays, list) and len(plays) == 1, f"{BAKE.name}: expected one play"
    tasks = plays[0]["tasks"]
    assert isinstance(tasks, list)
    return tasks


def _role_include_index(tasks: list[dict[str, Any]], role: str, tasks_from: str | None) -> int:
    for i, task in enumerate(tasks):
        spec = task.get("ansible.builtin.include_role")
        if not isinstance(spec, dict):
            continue
        if spec.get("name") == role and spec.get("tasks_from") == tasks_from:
            return i
    raise AssertionError(f"{BAKE.name} has no include_role {role} (tasks_from={tasks_from})")


def _install_tasks() -> list[dict[str, Any]]:
    tasks = _load(ROLE / "tasks" / "install.yml")
    assert isinstance(tasks, list)
    return tasks


def _module_index(tasks: list[dict[str, Any]], module: str) -> int:
    return next(i for i, t in enumerate(tasks) if module in t)


def test_bake_builds_the_responder_venv_after_mq_install() -> None:
    """mq-ubuntu2404 bakes the venv, and only AFTER mq-install lands the MQ SDK."""
    tasks = _bake_tasks()
    mq_install = _role_include_index(tasks, "mq-install", None)
    venv = _role_include_index(tasks, "mq-inter-qm", "install")
    assert mq_install < venv, "bake the responder venv after mq-install (pymqi needs the SDK)"


def test_per_run_role_reuses_the_install_half() -> None:
    """main.yml imports install.yml and carries no venv/pip work of its own, so the bake
    and the per-run path build the venv with exactly the same tasks."""
    main = _load(ROLE / "tasks" / "main.yml")
    assert any(t.get("ansible.builtin.import_tasks") == "install.yml" for t in main)
    assert not any("ansible.builtin.pip" in t for t in main), "pip install belongs in install.yml"


def test_install_half_asserts_mq_sdk_before_building() -> None:
    tasks = _install_tasks()
    stat = _module_index(tasks, "ansible.builtin.stat")
    assert tasks[stat]["ansible.builtin.stat"]["path"] == "/opt/mqm/inc/cmqc.h"
    guard = _module_index(tasks, "ansible.builtin.assert")
    assert guard < _module_index(tasks, "ansible.builtin.pip")


def test_install_half_is_idempotent_against_a_baked_venv() -> None:
    """Per-run on a baked box: venv create is skipped by `creates:`, and pip (state
    present, no version spec) is satisfied locally without a PyPI fetch."""
    tasks = _install_tasks()
    commands = [t["ansible.builtin.command"] for t in tasks if "ansible.builtin.command" in t]
    create = next(c for c in commands if "-m venv" in c["cmd"])
    assert create["creates"] == "{{ " + VENV_VAR + " }}/bin/python"
    pip = tasks[_module_index(tasks, "ansible.builtin.pip")]["ansible.builtin.pip"]
    assert pip["name"] == "pymqi"
    assert pip.get("state", "present") == "present", "state latest would hit PyPI every run"
    assert pip["virtualenv"] == "{{ " + VENV_VAR + " }}"


def test_venv_path_has_one_source() -> None:
    """The baked path and the responder unit's interpreter come from one default."""
    defaults = _load(ROLE / "defaults" / "main.yml")
    assert defaults[VENV_VAR] == "/var/mqm/rvenv"
    unit = (ROLE / "templates" / "mq-svc-responder@.service.j2").read_text(encoding="utf-8")
    assert "ExecStart={{ " + VENV_VAR + " }}/bin/python " in unit
    assert "/var/mqm/rvenv" not in unit
