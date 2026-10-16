"""drbd9 role + pdrbd box wiring (epic .github#310 T3, #1423).

The pacemaker-drbd arm needs DRBD 9 (RDQM ships kmod-drbd-9; the in-kernel module is
8.4.11, 2-node only, no quorum). LINBIT's PPA `drbd-dkms` is built against the box's
kernel at BAKE and proven to load as 9.x there; the per-run configure half re-proves it
for the BOOTED kernel and fails loud, naming the fix, on a kernel drift (Review Focus #2).

The fleet-derived per-bake guards (apt-autoupdate-off, motd-off, boot trim, bake-dirs
guard) only see a box once a topology stack declares its role (Task 4), so the pdrbd
bake's copies of those plays are pinned here directly until then.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mqlab.versions import OsRef, load_catalog

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "drbd9"
BAKE = ANSIBLE / "bake-pdrbd-ubuntu.yml"
CATALOG = REPO_ROOT / "lab" / "versions.yaml"


def _tasks(name: str) -> list[dict[str, Any]]:
    return yaml.safe_load((ROLE / "tasks" / name).read_text(encoding="utf-8"))


def _task(tasks: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    (task,) = [t for t in tasks if t["name"].startswith(prefix)]
    return task


def _plays() -> list[dict[str, Any]]:
    return yaml.safe_load(BAKE.read_text(encoding="utf-8"))


def _role_includes(play: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        t["ansible.builtin.include_role"]
        for t in play.get("tasks", [])
        if "ansible.builtin.include_role" in t
    ]


# --- catalog + builder wiring ------------------------------------------------------------


def test_pdrbd_box_role_is_catalogued() -> None:
    cat = load_catalog(CATALOG)
    assert cat.roles["pdrbd"]["bake"] == {"ubuntu": "pdrbd-ubuntu"}
    assert cat.roles["pdrbd"]["components"] == ("mq-resiliency-observability",)


def test_pdrbd_is_mq_bearing_because_it_bakes_mq() -> None:
    """The box bakes MQ through mq-install, whose skip-if-baked guard keys on presence
    only: a pin bump must flip the manifest hash (#1087) or a node boots the old MQ."""
    entry = load_catalog(CATALOG).box("pdrbd", OsRef("ubuntu", 24))
    assert (entry.name, entry.bake_stem, entry.mq_bearing) == (
        "pdrbd-ubuntu24",
        "pdrbd-ubuntu",
        True,
    )


def test_build_fatbox_stages_mq_media_for_pdrbd() -> None:
    text = (REPO_ROOT / "lab" / "boxes" / "build-fatbox.sh").read_text(encoding="utf-8")
    assert "  mq-ubuntu | nativeha-ubuntu | pcmk-ubuntu | pdrbd-ubuntu)" in text


# --- the bake playbook ---------------------------------------------------------------------


def test_bake_playbook_bakes_drbd9_install_half_and_mq() -> None:
    includes = [i for p in _plays() for i in _role_includes(p)]
    assert {"name": "drbd9", "tasks_from": "install"} in includes
    assert any(i["name"] == "mq-install" for i in includes)
    assert not any(i["name"] == "drbd9" and "tasks_from" not in i for i in includes), (
        "the bake must never run the per-run configure half (VG on the instance disk)"
    )


def test_bake_playbook_keeps_every_ubuntu_bake_invariant() -> None:
    plays = _plays()
    first = [i["name"] for i in _role_includes(plays[0])]
    assert first == ["apt-autoupdate-off"], "auto-updates off must be the FIRST play (#1225)"
    every = [i["name"] for p in plays for i in _role_includes(p)]
    for role in ("entropy", "motd-off", "cloud-init-trim", "snapd-off"):
        assert role in every, role
    last = [i["name"] for i in _role_includes(plays[-1])]
    assert last == ["bake-dirs-guard"], "the bake guard must be the LAST play (#1265)"


# --- install half (baked) ------------------------------------------------------------------


def test_drbd9_install_uses_linbit_ppa_and_dkms() -> None:
    text = (ROLE / "tasks" / "install.yml").read_text(encoding="utf-8")
    assert "ppa:linbit/linbit-drbd9-stack" in text
    assert "drbd-dkms" in text


def test_drbd9_install_builds_against_the_running_kernels_headers_first() -> None:
    tasks = _tasks("install.yml")
    headers = _task(tasks, "kernel headers + DKMS")
    module = _task(tasks, "DRBD 9 userland + DKMS module")
    assert "linux-headers-{{ ansible_kernel }}" in headers["ansible.builtin.apt"]["name"]
    assert tasks.index(headers) < tasks.index(module)


def test_drbd9_install_proves_module_version_at_bake() -> None:
    tasks = _tasks("install.yml")
    check = _task(tasks, "assert the built module is DRBD 9")
    assert tasks.index(_task(tasks, "load the module")) < tasks.index(check)
    assert "match('^9[.]')" in check["ansible.builtin.assert"]["that"]


# --- configure half (per run) --------------------------------------------------------------


def test_drbd9_configure_asserts_drbd9_for_running_kernel() -> None:
    """Review Focus #2: a booted kernel that is not the one the DKMS module was built for
    must fail loud and name the fix, never let modprobe fall back to the in-kernel 8.4."""
    tasks = _tasks("main.yml")
    probe = next(t for t in tasks if t.get("register") == "drbd9_modver")
    assert "uname -r" in probe["ansible.builtin.shell"]
    # A kernel with NO drbd module at all (modinfo rc 1) must still reach the assert and
    # its fix message; only an unexpected modinfo error fails at the probe.
    assert probe["failed_when"] == "drbd9_modver.rc not in [0, 1]"
    assert probe["changed_when"] is False
    check = _task(tasks, "assert DRBD 9 for the running kernel")
    assert check["ansible.builtin.assert"]["that"] == [
        "drbd9_modver.rc == 0",
        "drbd9_modver.stdout is match('^9[.]')",
    ]
    assert "rebuild the pdrbd box" in check["ansible.builtin.assert"]["fail_msg"]
    load = _task(tasks, "load drbd")
    assert tasks.index(check) < tasks.index(load), "the drift guard must run before modprobe"


def test_drbd9_configure_asserts_the_loaded_module_is_9() -> None:
    """A drbd module already loaded before the role ran (the lsmod guard skips modprobe)
    must still be proven 9.x — the guarantee is about the LOADED module."""
    tasks = _tasks("main.yml")
    load = _task(tasks, "load drbd")
    loaded = _task(tasks, "assert the loaded drbd module is DRBD 9")
    assert tasks.index(load) < tasks.index(loaded)
    assert "rebuild the pdrbd box" in loaded["ansible.builtin.assert"]["fail_msg"]


def test_drbd9_configure_skips_install_on_a_baked_box() -> None:
    inc = _task(_tasks("main.yml"), "DRBD 9 install half")
    assert inc["ansible.builtin.include_tasks"] == "install.yml"
    assert inc["when"] == "'drbd-dkms' not in ansible_facts.packages"


def test_drbd9_vg_creation_is_probe_guarded_and_never_prompts() -> None:
    vg = _task(_tasks("main.yml"), "drbdpool-style VG")
    script = vg["ansible.builtin.shell"]
    assert "vgs {{ drbd9_vg }}" in script
    assert "pvcreate {{ drbd9_disk }} </dev/null" in script
    assert "vgcreate {{ drbd9_vg }} {{ drbd9_disk }} </dev/null" in script


def test_drbd9_defaults() -> None:
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
    assert defaults == {"drbd9_vg": "drbdpool", "drbd9_disk": "/dev/vdb"}


def test_no_drbd9_task_swallows_errors() -> None:
    for name in ("install.yml", "main.yml"):
        text = (ROLE / "tasks" / name).read_text(encoding="utf-8")
        assert "|| true" not in text, name
        assert "failed_when: false" not in text, name
        assert "ignore_errors" not in text, name
