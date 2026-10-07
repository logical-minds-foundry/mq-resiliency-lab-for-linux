"""Regression guard for the node-exporter textfile drop zone (#449/#458/#493/#1378).

The drop zone must stay a group-writable, setgid shared dir: the unprivileged
app-requester (User=vagrant, in the node_exporter group) publishes its round-trip
metrics there, and a silent regression to a non-group-writable mode (e.g. 0755)
breaks that with EACCES. The systemd-tmpfiles rule is the single declarative owner
of the mode; this test fails loudly if it ever drifts.

#1378: the per-run collector roles (net-reach, cluster-state, nativeha-state,
rdqm-state) each carried a `file: mode=0755` task on the drop zone, and net-reach runs
AFTER node-exporter in observability.yml, so it reset the dir to 0755 mid-run. Only
the node-exporter role (and the app-requester role, which must create it group-writable
when it runs first) may manage the dir, and only as 02775. node-exporter reads every
writer's .prom through a default ACL, which must grant it read and keep world access off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_boot_trim_baked import _iter_tasks

_REPO = Path(__file__).resolve().parents[1]
_ANSIBLE = _REPO / "ansible"
_ROLES = _ANSIBLE / "roles"
_TMPFILES = _ROLES / "node-exporter/files/node-exporter-textfile.conf"
_DROPZONE = "/var/lib/node_exporter/textfile"
# The only roles allowed to manage the drop zone dir (each only as 02775).
_DIR_MANAGERS = {"node-exporter", "app-requester"}
_COLLECTOR_ROLES = ("net-reach", "cluster-state", "nativeha-state", "rdqm-state")


def _rule() -> list[str]:
    line = next(
        ln for ln in _TMPFILES.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    )
    return line.split()


def test_dropzone_tmpfiles_rule_is_group_writable_setgid():
    typ, path, mode, owner, group, *_ = _rule()
    assert typ == "d"  # a directory
    assert path == _DROPZONE
    # setgid (2) + rwxr-x with group-WRITE — NOT a non-group-writable 0755 (#449)
    assert mode == "2775"
    assert owner == "node_exporter"
    assert group == "node_exporter"


def _role_tasks(role: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted((role / "tasks").glob("*.yml")):
        tasks.extend(_iter_tasks(yaml.safe_load(path.read_text(encoding="utf-8")) or []))
    return tasks


def _resolve(role: Path, value: str) -> str:
    """Resolve a bare `{{ var }}` path against the role defaults (one level); a var the
    defaults do not define (e.g. a loop `item`) is returned unresolved."""
    stripped = value.strip()
    defaults_file = role / "defaults/main.yml"
    if stripped.startswith("{{") and stripped.endswith("}}") and defaults_file.is_file():
        defaults = yaml.safe_load(defaults_file.read_text(encoding="utf-8")) or {}
        return str(defaults.get(stripped[2:-2].strip(), value))
    return value


def _dropzone_file_tasks() -> list[tuple[str, dict[str, Any]]]:
    """Every (role, ansible.builtin.file spec) that targets the drop zone dir."""
    found = []
    for role in sorted(p for p in _ROLES.iterdir() if (p / "tasks").is_dir()):
        for task in _role_tasks(role):
            spec = task.get("ansible.builtin.file")
            if isinstance(spec, dict) and _resolve(role, str(spec.get("path", ""))) == _DROPZONE:
                found.append((role.name, spec))
    return found


def test_only_the_owner_roles_manage_the_dropzone_dir():
    managers = {role for role, _spec in _dropzone_file_tasks()}
    assert managers == _DIR_MANAGERS, (
        f"only {sorted(_DIR_MANAGERS)} may manage {_DROPZONE} (#1378); got {sorted(managers)}"
    )


def test_every_dropzone_dir_task_keeps_02775():
    for role, spec in _dropzone_file_tasks():
        assert spec.get("state") == "directory", role
        assert spec.get("mode") == "02775", f"{role} sets the drop zone to {spec.get('mode')}"


@pytest.mark.parametrize("role", _COLLECTOR_ROLES)
def test_collector_role_asserts_the_dropzone_instead_of_managing_it(role: str):
    tasks = _role_tasks(_ROLES / role)
    # It never sets the dir mode (the #1378 regression: a `file: mode=0755` task).
    assert not [t for t in tasks if (t.get("ansible.builtin.file") or {}).get("path") == _DROPZONE]
    stat = next(t for t in tasks if (t.get("ansible.builtin.stat") or {}).get("path") == _DROPZONE)
    reg = stat["register"]
    checks = next(
        t["ansible.builtin.assert"]["that"]
        for t in tasks
        if any(reg in c for c in (t.get("ansible.builtin.assert") or {}).get("that", []))
    )
    assert f"{reg}.stat.mode == '2775'" in checks


def test_node_exporter_runs_before_the_collector_roles_in_observability():
    play = yaml.safe_load((_ANSIBLE / "observability.yml").read_text(encoding="utf-8"))[0]
    names = [r if isinstance(r, str) else r["role"] for r in play["roles"]]
    for role in _COLLECTOR_ROLES:
        assert names.index("node-exporter") < names.index(role), role


def test_default_acl_grants_node_exporter_read_and_no_world_access():
    tasks = _role_tasks(_ROLES / "node-exporter")
    cmd = next(
        t["ansible.builtin.command"]["cmd"]
        for t in tasks
        if "setfacl" in str((t.get("ansible.builtin.command") or {}).get("cmd", ""))
    )
    assert cmd.endswith(f" {_DROPZONE}")
    assert cmd.split()[:2] == ["setfacl", "-d"]
    entries = set(cmd.split()[3].split(","))
    # node_exporter reads; group read only (no write/exec); world gets nothing (#1378).
    assert entries == {"u:node_exporter:r", "g::r", "o::-"}


def test_requester_unit_has_no_world_bits_umask():
    unit = _REPO / "components/mq-resiliency-clients/systemd/mq-app-requester.service"
    assert "UMask=0027" in unit.read_text(encoding="utf-8").splitlines()
