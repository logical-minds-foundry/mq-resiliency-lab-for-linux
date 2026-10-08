"""Guard the baked SAN target box and the retired SAN deb cache (#1278, epic .github#280
spec §4.7.1, superseding .github#108).

The SAN targets (san-a/san-b) used to boot the bare Ubuntu base box and install
drbd-utils, targetcli-fb and the kernel-modules package from a deb cache that mqlab filled
by running `apt-get download` on the CONTROLLER — the controller's release, so 26.04 SANs
would have installed noble packages, and the kernel-keyed modules package kept missing.
Now they boot a baked `san` box whose bake installs those packages on the target OS
itself. These static checks pin that contract:

- no SAN deb cache code, role branch, doc or prereq survives;
- bake-san.yml bakes the INSTALL halves of drbd-san and iscsi-target, never their
  per-run configure halves (no DRBD resource, no iSCSI target);
- each install half is a package_facts skip-if-baked guard plus a plain apt install, so
  the roles still work at bake time and on an un-baked host;
- the kernel-modules package name is routed through the per-OS vars include, not
  hard-coded (26.04 has no linux-modules-extra);
- the per-run main.yml runs the install half first;
- the manifest hash digests bake-san.yml's role closure, and only the san box bakes the
  SAN roles.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from mqlab import phases, topology, versions
from tests.boxfleet import box_bakes, manifest_hash_args
from tests.test_boot_trim_baked import _iter_tasks, _plays, _role_includes

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
MANIFEST_HASH = REPO_ROOT / "lab" / "boxes" / "_manifest-hash.sh"
SAN_ROLES = ("drbd-san", "iscsi-target")
OS_VARS_INCLUDE = "../../../tasks/os-vars.yml"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _san_box() -> str:
    catalog = versions.load_catalog()
    return catalog.box("san", catalog.infra).name


# --- the box ------------------------------------------------------------------------


def test_san_nodes_resolve_to_baked_san_box(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(versions.instances, "read_record", lambda stack: None)
    catalog = versions.load_catalog()
    nb = versions.node_boxes(topology.load(), catalog)
    assert nb["san-a"].name == nb["san-b"].name == f"san-{catalog.infra.token}"


def test_san_role_bakes_bake_san_on_ubuntu_only() -> None:
    catalog = versions.load_catalog()
    assert catalog.roles["san"] == {
        "bake": {"ubuntu": "san"},
        "mq_bearing": False,
        "components": ("mq-resiliency-observability",),
    }
    assert box_bakes()[_san_box()] == ("ubuntu", "san")


# --- the retired deb cache ----------------------------------------------------------


def test_no_san_deb_cache_remains() -> None:
    assert not (SRC / "mqlab" / "sandeb.py").exists()
    assert not (REPO_ROOT / "tests" / "test_sandeb.py").exists()
    assert not (REPO_ROOT / "docs" / "development" / "san-deb-cache.md").exists()
    for role in SAN_ROLES:
        text = "".join(p.read_text(encoding="utf-8") for p in (ROLES / role).rglob("*.yml"))
        for token in ("san-debs", "san_media_dir", ".target-kernel", "delegate_to"):
            assert token not in text, f"{role}: deb-cache remnant {token!r}"


def test_bootstrap_declares_no_san_prereq() -> None:
    assert all("san" not in p.ensure for p in phases.PHASES)


# --- bake-san.yml: install halves only ----------------------------------------------


def test_bake_san_runs_the_install_half_of_each_san_role() -> None:
    plays = _plays("san")
    for role in SAN_ROLES:
        includes = _role_includes(plays, role)
        assert len(includes) == 1, f"bake-san.yml must include {role} exactly once"
        spec = includes[0]["ansible.builtin.include_role"]
        assert spec.get("tasks_from") == "install", f"{role}: bake only its install half"


def test_bake_san_configures_no_drbd_resource_and_no_iscsi_target() -> None:
    text = (ANSIBLE / "bake-san.yml").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for token in ("drbdadm", "create-md", "mqlun", "/etc/drbd.d", "targetcli /", "saveconfig"):
        assert token not in body, f"bake-san.yml must not configure per-run state ({token!r})"


def test_bake_san_checks_the_modules_resolve_for_the_baked_kernel() -> None:
    (task,) = [
        t
        for t in _iter_tasks(_plays("san"))
        if str(t.get("ansible.builtin.command", "")).startswith("modinfo")
    ]
    assert task["ansible.builtin.command"] == "modinfo -F version {{ item }}"
    assert task["loop"] == ["drbd", "target_core_mod"]
    assert "ignore_errors" not in task and "failed_when" not in task


# --- the install halves: skip-if-baked + plain apt ----------------------------------

INSTALL_BODIES = {
    "drbd-san": ROLES / "drbd-san" / "tasks" / "install.yml",
    "iscsi-target": ROLES / "iscsi-target" / "tasks" / "install-Debian.yml",
}
GUARDS = {
    "drbd-san": (
        "'drbd-utils' not in ansible_facts.packages "
        "or drbd_san_kernel_modules_pkg not in ansible_facts.packages"
    ),
    "iscsi-target": "'targetcli-fb' not in ansible_facts.packages",
}
PACKAGES = {
    "drbd-san": ["drbd-utils", "{{ drbd_san_kernel_modules_pkg }}"],
    "iscsi-target": "targetcli-fb",
}


@pytest.mark.parametrize("role", SAN_ROLES)
def test_install_half_is_a_package_facts_guard_then_plain_apt(role: str) -> None:
    tasks = list(_iter_tasks(_load(INSTALL_BODIES[role])))
    facts = [t for t in tasks if "ansible.builtin.package_facts" in t]
    apts = [t for t in tasks if "ansible.builtin.apt" in t]
    assert len(facts) == 1 and len(apts) == 1, f"{role}: one package_facts, one apt"
    assert facts[0]["ansible.builtin.package_facts"] == {"manager": "apt"}
    assert tasks.index(facts[0]) < tasks.index(apts[0]), f"{role}: facts before the install"
    apt = apts[0]["ansible.builtin.apt"]
    assert apt["name"] == PACKAGES[role]
    assert apt["state"] == "present" and apt["update_cache"] is True
    assert apt["lock_timeout"] == "{{ apt_lock_timeout }}"
    assert apts[0]["when"].strip() == GUARDS[role]
    assert not [t for t in tasks if "ansible.builtin.shell" in t], f"{role}: no shell install"


def test_drbd_kernel_modules_package_comes_from_the_os_vars_include() -> None:
    tasks = list(_iter_tasks(_load(INSTALL_BODIES["drbd-san"])))
    (include,) = [t for t in tasks if t.get("ansible.builtin.include_tasks") == OS_VARS_INCLUDE]
    assert include["vars"] == {"os_vars_role": "drbd-san"}
    assert tasks.index(include) == 0, "load the per-OS vars before anything installs"
    code = [
        line
        for line in INSTALL_BODIES["drbd-san"].read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if "linux-modules" in line], "package name is per-OS vars"


def test_ubuntu24_names_linux_modules_extra_for_the_running_kernel() -> None:
    assert _load(ROLES / "drbd-san" / "vars" / "Ubuntu-24.yml") == {
        "drbd_san_kernel_modules_pkg": "linux-modules-extra-{{ ansible_kernel }}"
    }


def test_ubuntu26_names_the_base_linux_modules_for_the_running_kernel() -> None:
    """26.04 has no linux-modules-extra; DRBD + LIO ship in linux-modules-<kver> (#1282)."""
    assert _load(ROLES / "drbd-san" / "vars" / "Ubuntu-26.yml") == {
        "drbd_san_kernel_modules_pkg": "linux-modules-{{ ansible_kernel }}"
    }


def test_iscsi_target_drops_either_wildcard_default_portal() -> None:
    """targetcli-fb 2.x auto-creates a 0.0.0.0:3260 portal, 3.0.1+ (Ubuntu 26.04) an
    [::0]:3260 one; a surviving wildcard blocks the SAN-net portal, so both go (#1282)."""
    (task,) = [
        t
        for t in _iter_tasks(_load(ROLES / "iscsi-target" / "tasks" / "main.yml"))
        if t.get("name") == "configure target (per-object idempotent probes)"
    ]
    script = task["ansible.builtin.shell"]
    wildcards = (("0.0.0.0:3260", "delete 0.0.0.0 3260"), ("[::0]:3260", "delete ::0 3260"))
    for listed, deleted in wildcards:
        assert f"grep -qF -- '{listed}'" in script
        assert f'targetcli "/iscsi/$iqn/tpg1/portals" {deleted}' in script
    for script_path in ("pcmk-dr-cutover.sh", "pcmk-dr-force.sh"):
        text = (REPO_ROOT / "lab" / "scripts" / script_path).read_text(encoding="utf-8")
        assert "portals delete 0.0.0.0 3260" in text and "portals delete ::0 3260" in text


@pytest.mark.parametrize("role", SAN_ROLES)
def test_per_run_main_runs_the_install_half_first(role: str) -> None:
    tasks = _load(ROLES / role / "tasks" / "main.yml")
    assert tasks[0].get("ansible.builtin.include_tasks") == "install.yml"


def test_iscsi_target_install_half_is_the_os_adapter() -> None:
    (task,) = _load(ROLES / "iscsi-target" / "tasks" / "install.yml")
    assert task["ansible.builtin.include_tasks"] == "install-{{ ansible_os_family }}.yml"


# --- manifest-hash closure ----------------------------------------------------------


def _hash(root: Path, box: str) -> str:
    script = root / "lab" / "boxes" / "_manifest-hash.sh"
    return subprocess.run(  # noqa: S603
        [str(script), box, *manifest_hash_args(box)], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    "probe",
    [
        "drbd-san/tasks/install.yml",
        "drbd-san/tasks/main.yml",
        "drbd-san/vars/Ubuntu-24.yml",
        "iscsi-target/tasks/install-Debian.yml",
    ],
)
def test_san_role_edits_flip_only_the_san_box_hash(tmp_path: Path, probe: str) -> None:
    """bake-san.yml's role closure enters the san box's manifest hash (an edit forces a
    rebake), and no other box bakes the SAN roles. Runs against a scratch copy of the hash
    inputs so the real tree is never mutated."""
    (tmp_path / "lab" / "boxes").mkdir(parents=True)
    shutil.copy2(MANIFEST_HASH, tmp_path / "lab" / "boxes" / MANIFEST_HASH.name)
    shutil.copy2(REPO_ROOT / "lab" / "mq-version", tmp_path / "lab" / "mq-version")
    shutil.copytree(ROLES, tmp_path / "ansible" / "roles")
    shutil.copytree(ANSIBLE / "group_vars", tmp_path / "ansible" / "group_vars")
    for bake in ANSIBLE.glob("bake-*.yml"):
        shutil.copy2(bake, tmp_path / "ansible" / bake.name)

    boxes = box_bakes()
    before = {box: _hash(tmp_path, box) for box in boxes}
    target = tmp_path / "ansible" / "roles" / probe
    target.write_text(target.read_text(encoding="utf-8") + "# probe\n", encoding="utf-8")
    after = {box: _hash(tmp_path, box) for box in boxes}

    flipped = {box for box in boxes if before[box] != after[box]}
    assert flipped == {_san_box()}
