"""Pin rdqm-install's SELinux module for RDQM's DRBD handlers on RHEL 10 (#1486).

DRBD runs RDQM's handlers (``/var/mqm/rdqm/<hook>`` -> ``/opt/mqm/bin/rdqmhandler``) through
the kernel's usermode helper. RHEL 10's policy (SELinux enforcing, as the lab's kickstart has
always set) confines that chain: the kernel may not execute ``drbdadm`` or ``/bin/sh``, and
``bin_t`` programs land in ``kernel_generic_helper_t``. So every handler exits 255, RDQM never
records its initial sync (DR "Waiting for initial connection", HA suspend refused with
AMQ6526E), and a handler blocked part-way can hang and wedge DRBD.

``files/rdqm_drbd_helper.cil`` is a generic fix, keyed only on IBM's own handler. Proven
under full enforcing on rdqm-rhel@10 (validation #1475): every handler exits 0, HA failover
and DR cutover/failback work, and no AVC is logged. These checks pin the module's content,
where the role installs it, and the fail-loud label check.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE = REPO_ROOT / "ansible" / "roles" / "rdqm-install"
INSTALL = ROLE / "tasks" / "install.yml"
CIL = ROLE / "files" / "rdqm_drbd_helper.cil"
VARS = {
    "el9": ROLE / "vars" / "RedHat-9.yml",
    "el10": ROLE / "vars" / "RedHat-10.yml",
}

STEP2 = "install MQSeriesRDQM (step 2 of 2 - after the server files exist)"
STAGE_DIR = "the RDQM SELinux module staging dir"
STAGE = "stage the SELinux modules RDQM's DRBD handlers need"
LIST = "list the loaded SELinux modules"
LOAD = "load the SELinux modules RDQM's DRBD handlers need"
RELABEL = "label IBM's RDQM handler with its own entrypoint type"
READ_LABEL = "read the RDQM handler's SELinux label"
ASSERT_LABEL = "the RDQM handler carries rdqm_handler_exec_t (fail loud)"
SELINUX_TASKS = (STAGE_DIR, STAGE, LIST, LOAD, RELABEL, READ_LABEL, ASSERT_LABEL)
SELINUX_ON = "ansible_selinux.status | default('disabled') == 'enabled'"
HAS_MODULES = "rdqm_selinux_modules | length > 0"

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


def _modules(el: str) -> Any:
    data: dict[str, Any] = yaml.safe_load(VARS[el].read_text(encoding="utf-8"))
    return data["rdqm_selinux_modules"]


def _cil() -> str:
    # Drop ; comments so the assertions read the policy, not its explanation.
    return "\n".join(line.split(";", 1)[0] for line in CIL.read_text(encoding="utf-8").splitlines())


def test_rhel9_needs_no_selinux_module() -> None:
    assert _modules("el9") == []


def test_rhel10_loads_the_drbd_helper_module() -> None:
    assert _modules("el10") == ["rdqm_drbd_helper"]


@pytest.mark.parametrize("el", sorted(VARS))
def test_every_listed_module_ships_as_a_cil_file(el: str) -> None:
    for name in _modules(el):
        assert (ROLE / "files" / f"{name}.cil").is_file(), name


def test_cil_labels_only_ibms_handler_with_its_own_type() -> None:
    filecons = re.findall(r'\(filecon\s+"([^"]+)"\s+file\s+\((\w+)\s+(\w+)\s+(\w+)\s', _cil())
    assert filecons == [("/opt/mqm/bin/rdqmhandler", "system_u", "object_r", "rdqm_handler_exec_t")]


def test_cil_transitions_the_handler_into_unconfined_service() -> None:
    cil = _cil()
    transition = (
        r"\(typetransition\s+kernel_t\s+rdqm_handler_exec_t\s+process\s+unconfined_service_t\)"
    )
    entrypoint = (
        r"\(allow\s+unconfined_service_t\s+rdqm_handler_exec_t\s+\(file\s+\([^)]*\bentrypoint\b"
    )
    may_transition = r"\(allow\s+kernel_t\s+unconfined_service_t\s+\(process\s+\(transition\)\)\)"
    assert re.search(transition, cil)
    assert re.search(entrypoint, cil)
    assert re.search(may_transition, cil)


def test_cil_lets_the_kernel_run_drbdadm_and_its_shell_in_place() -> None:
    cil = _cil()
    for target in ("drbd_exec_t", "shell_exec_t"):
        rule = rf"\(allow\s+kernel_t\s+{target}\s+\(file\s+\([^)]*\bexecute_no_trans\b"
        assert re.search(rule, cil), target


def test_cil_never_widens_the_generic_kernel_helper_domain() -> None:
    # The point of the transition: every other kernel helper stays confined.
    assert "kernel_generic_helper_t" not in _cil()
    assert "permissive" not in _cil()


def test_selinux_tasks_run_after_the_rdqm_install_in_order() -> None:
    names = _names()
    positions = [names.index(n) for n in SELINUX_TASKS]
    assert names.index(STEP2) < positions[0]
    assert positions == sorted(positions)


@pytest.mark.parametrize("name", SELINUX_TASKS)
def test_selinux_tasks_only_run_with_selinux_on_and_modules_listed(name: str) -> None:
    when = _task(name)["when"]
    when = [when] if isinstance(when, str) else when
    assert SELINUX_ON in when
    assert HAS_MODULES in when
    assert not [k for k in SOFTENERS if k in _task(name)]


def test_modules_load_with_semodule_from_the_staged_files() -> None:
    # copy does not create the destination directory, so the role creates it first (live
    # check of #1486: without it the staging task failed on every node).
    assert _task(STAGE_DIR)["ansible.builtin.file"] == {
        "path": "/usr/share/selinux/packages/rdqm",
        "state": "directory",
        "mode": "0755",
    }
    stage = _task(STAGE)["ansible.builtin.copy"]
    assert stage["src"] == "{{ item }}.cil"
    assert stage["dest"] == "/usr/share/selinux/packages/rdqm/{{ item }}.cil"
    load = _task(LOAD)["ansible.builtin.command"]
    staged = "/usr/share/selinux/packages/rdqm/{{ item.item }}.cil"
    assert load["argv"] == ["semodule", "-i", staged]
    # Loads only a new or changed module, so it always reports changed when it runs.
    assert _task(LOAD)["changed_when"] is True
    only_when_needed = "item.changed or item.item not in rdqm_selinux_loaded.stdout_lines"
    assert only_when_needed in _task(LOAD)["when"]


def test_label_check_fails_loudly_on_the_wrong_type() -> None:
    assert _task(READ_LABEL)["ansible.builtin.command"]["argv"] == [
        "stat",
        "-c",
        "%C",
        "/opt/mqm/bin/rdqmhandler",
    ]
    assert _task(ASSERT_LABEL)["ansible.builtin.assert"]["that"] == [
        "':rdqm_handler_exec_t:' in rdqm_handler_label.stdout"
    ]
