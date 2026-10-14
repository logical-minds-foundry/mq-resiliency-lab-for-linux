"""Pin rdqm-install's undeclared base-OS dependencies and its crmsh start check (#1484).

IBM's el10 RDQM prereqs (MQ 10.0.1, ``PreReqs/el10/pacemaker-3``) ship LINBIT's crmsh
4.6.2, which imports ``distutils`` (``crmsh/utils.py``, ``crmsh/ra.py``). RHEL 10's python3
is 3.12, which has no ``distutils``, and the rpm declares no dependency that supplies it.
So ``crm`` cannot start, ``rdqmadm -c`` polls ``crm status bynode`` until it gives up, and
the first ``rdqm-rhel@10`` provision failed with AMQ3890E/AMQ3875E (validation #1475).

``python3-setuptools`` (RHEL 10 BaseOS) provides the ``distutils`` shim. The role installs
a per-OS list of such packages from the offline DVD repo before the RDQM prereqs, then
proves ``crm`` starts, so the next missing dependency fails at install with crmsh's own
message rather than 30 s into ``rdqmadm -c``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE = REPO_ROOT / "ansible" / "roles" / "rdqm-install"
INSTALL = ROLE / "tasks" / "install.yml"
VARS = {
    "el9": ROLE / "vars" / "RedHat-9.yml",
    "el10": ROLE / "vars" / "RedHat-10.yml",
}

EXTRA = "base-OS packages the RDQM prereqs need but do not declare"
CRMSH = "crmsh starts (fail loud: an undeclared dependency surfaces here, not in rdqmadm -c)"
DVD_REPO = "dnf repos from the DVD"
STEP1 = "install MQ + cluster prereqs (step 1 of 2)"
STEP2 = "install MQSeriesRDQM (step 2 of 2 - after the server files exist)"

# Keys that would let a failing task pass quietly.
SOFTENERS = ("ignore_errors", "failed_when", "rescue", "until")


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = yaml.safe_load(INSTALL.read_text(encoding="utf-8"))
    return tasks


def _names() -> list[str]:
    return [t.get("name", "") for t in _tasks()]


def _task(name: str) -> dict[str, Any]:
    (task,) = [t for t in _tasks() if t.get("name") == name]
    return task


def _extra(el: str) -> Any:
    data: dict[str, Any] = yaml.safe_load(VARS[el].read_text(encoding="utf-8"))
    return data["rdqm_os_extra_packages"]


def test_rhel9_needs_no_extra_packages() -> None:
    assert _extra("el9") == []


def test_rhel10_installs_the_distutils_shim_for_crmsh() -> None:
    assert _extra("el10") == ["python3-setuptools"]


@pytest.mark.parametrize("el", sorted(VARS))
def test_extra_packages_are_bare_package_names(el: str) -> None:
    packages = _extra(el)
    assert isinstance(packages, list)
    assert all(isinstance(p, str) and p and " " not in p for p in packages)


def test_extra_packages_install_from_the_dvd_repo_through_dnf() -> None:
    task = _task(EXTRA)
    assert task["ansible.builtin.dnf"] == {
        "name": "{{ rdqm_os_extra_packages }}",
        "state": "present",
    }
    assert task["become"] is True
    assert not [k for k in SOFTENERS if k in task]


def test_extra_packages_install_after_the_dvd_repo_and_before_the_prereqs() -> None:
    names = _names()
    assert names.index(DVD_REPO) < names.index(EXTRA) < names.index(STEP1)


def test_crmsh_start_check_runs_crm_version_after_the_rdqm_install() -> None:
    task = _task(CRMSH)
    assert task["ansible.builtin.command"] == {"argv": ["/usr/sbin/crm", "--version"]}
    assert task["changed_when"] is False
    assert not [k for k in SOFTENERS if k in task]
    names = _names()
    assert names.index(STEP2) < names.index(CRMSH)
