"""Guard the bake-time cloud-init + snapd boot trim in every Ubuntu box (#1250).

#1247 showed cloud-init.service and snapd.seeded.service as the biggest boot units on the
slow guests. #1250 verified on a booted clone that cloud-init still does two load-bearing
jobs (cloud-init-local re-renders the mgmt NIC's netplan for the clone's MAC; the init
stage's growpart + resizefs grow / to the 20G disk), and that only obs on aarch64 has snaps
(the chromium snap behind grafana-image-renderer). These guards pin the fix:

* every Ubuntu bake (derived from build-fatbox.sh, so a new Ubuntu box cannot slip past)
  includes both roles in its LAST play, and no RHEL bake includes either;
* cloud-init keeps its two load-bearing services and is never disabled outright, the
  drop-in trims exactly to growpart + resizefs, and the role verifies fail-loud;
* snapd is purged (behind a no-snaps guard) everywhere except obs on aarch64, which keeps
  it and masks only the snapd.seeded gate;
* both roles are in the manifest-hash closure, so a later edit forces a rebake.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.boxfleet import box_bakes, manifest_hash_args

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
CI_ROLE = ROLES / "cloud-init-trim"
SNAP_ROLE = ROLES / "snapd-off"
MANIFEST_HASH = REPO_ROOT / "lab" / "boxes" / "_manifest-hash.sh"
TRIM_ROLES = ("cloud-init-trim", "snapd-off")


def _box_bakes() -> dict[str, tuple[str, str]]:
    """box -> (base OS family, bake stem), from the catalog-derived fleet (#1274)."""
    return box_bakes()


def _iter_tasks(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _role_includes(node: Any, role: str) -> list[dict]:
    found = []
    for task in _iter_tasks(node):
        for verb in ("ansible.builtin.include_role", "ansible.builtin.import_role"):
            spec = task.get(verb)
            if isinstance(spec, dict) and spec.get("name") == role:
                found.append(task)
    return found


def _plays(stem: str) -> list[dict]:
    plays = yaml.safe_load((ANSIBLE / f"bake-{stem}.yml").read_text(encoding="utf-8"))
    assert isinstance(plays, list) and plays
    return plays


def _tasks(role: Path) -> list[dict]:
    tasks = yaml.safe_load((role / "tasks" / "main.yml").read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks
    return tasks


def _defaults(role: Path) -> dict:
    return yaml.safe_load((role / "defaults" / "main.yml").read_text(encoding="utf-8"))


def _task(role: Path, fragment: str) -> dict:
    return next(t for t in _tasks(role) if fragment in t.get("name", ""))


def _split() -> tuple[dict[str, str], dict[str, str]]:
    bakes = _box_bakes()
    ubuntu = {box: stem for box, (kind, stem) in bakes.items() if kind == "ubuntu"}
    rhel = {box: stem for box, (kind, stem) in bakes.items() if kind == "rhel"}
    assert ubuntu and rhel, f"expected both Ubuntu and RHEL boxes in the fleet; got {bakes}"
    return ubuntu, rhel


def _trim_play(box: str, plays: list[dict]) -> dict:
    """The #1250 trim play: the last play that does anything to the image. Only the
    read-only #1265 bake-dirs-guard play may follow it (it must check the purged result)."""
    assert len(plays) >= 2, f"{box}: expected the trim play and the #1265 guard play"
    guard = plays[-1]
    assert _role_includes(guard, "bake-dirs-guard"), f"{box}: the LAST play must be the guard"
    assert len(guard["tasks"]) == 1, f"{box}: the #1265 guard play must run only the guard"
    return plays[-2]


@pytest.mark.parametrize("role", TRIM_ROLES)
def test_every_ubuntu_bake_runs_the_role_in_its_last_play(role: str) -> None:
    ubuntu, rhel = _split()
    for box, stem in ubuntu.items():
        plays = _plays(stem)
        assert _role_includes(_trim_play(box, plays), role), (
            f"{box}: {role} must run in the last play before the #1265 guard (#1250)"
        )
        assert sum(len(_role_includes(p, role)) for p in plays) == 1, f"{box}: {role} twice"
    wrong = [box for box, stem in rhel.items() if _role_includes(_plays(stem), role)]
    assert wrong == [], f"RHEL bakes must not include the Ubuntu-only {role} (#1250): {wrong}"


def test_apt_autoupdate_off_is_still_the_first_play() -> None:
    """Appending the #1250 play must not disturb #1225's first-play requirement."""
    ubuntu, _ = _split()
    for box, stem in ubuntu.items():
        assert _role_includes(_plays(stem)[0], "apt-autoupdate-off"), box


def test_snapd_mode_is_keep_only_for_obs_on_aarch64() -> None:
    ubuntu, _ = _split()
    for box, stem in ubuntu.items():
        (task,) = _role_includes(_trim_play(box, _plays(stem)), "snapd-off")
        mode = (task.get("vars") or {}).get("snapd_off_mode")
        if box == "obs-ubuntu24":
            assert mode == "{{ 'keep' if ansible_architecture == 'aarch64' else 'purge' }}"
        else:
            assert mode is None, f"{box}: must take the default snapd_off_mode (purge)"
    assert _defaults(SNAP_ROLE)["snapd_off_mode"] == "purge"


def test_obs_aarch64_snap_is_really_the_chromium_browser_deb() -> None:
    """The keep-on-aarch64 exception exists because of exactly this install."""
    install = yaml.safe_load(
        (ROLES / "grafana-image-renderer" / "tasks" / "install.yml").read_text(encoding="utf-8")
    )
    arm = next(t for t in install if t.get("name") == "install chromium (aarch64)")
    assert arm["ansible.builtin.apt"]["name"] == "chromium-browser"
    assert arm["when"] == "ansible_architecture == 'aarch64'"


# --- cloud-init-trim -----------------------------------------------------------------


def test_cloud_init_dropin_trims_to_growpart_and_resizefs() -> None:
    cfg = yaml.safe_load((CI_ROLE / "files" / "99_lab_trim.cfg").read_text(encoding="utf-8"))
    defaults = _defaults(CI_ROLE)
    assert cfg["cloud_init_modules"] == ["growpart", "resizefs"]
    assert cfg["cloud_init_modules"] == defaults["cloud_init_trim_init_modules"]
    assert cfg["cloud_config_modules"] == []
    assert cfg["cloud_final_modules"] == []
    assert cfg["preserve_hostname"] is True
    assert "datasource_list" not in cfg, "the trim must not touch datasource discovery"
    # cloud.cfg.d files are read in lexical order: the drop-in must be a .cfg under it.
    assert defaults["cloud_init_trim_conf"].startswith("/etc/cloud/cloud.cfg.d/")
    assert defaults["cloud_init_trim_conf"].endswith(".cfg")


def test_cloud_init_keeps_its_load_bearing_services() -> None:
    defaults = _defaults(CI_ROLE)
    assert defaults["cloud_init_trim_required_services"] == [
        "cloud-init-local.service",
        "cloud-init.service",
    ]
    masked = defaults["cloud_init_trim_masked_services"]
    assert masked == ["cloud-config.service", "cloud-final.service"]
    assert not set(masked) & set(defaults["cloud_init_trim_required_services"])
    # Never disabled outright: no task may create the marker (it is only stat-ed).
    for task in _tasks(CI_ROLE):
        for verb in ("ansible.builtin.file", "ansible.builtin.copy", "ansible.builtin.command"):
            assert "cloud-init.disabled" not in str(task.get(verb, "")), task.get("name")
    stat = _task(CI_ROLE, "look for a cloud-init.disabled")["ansible.builtin.stat"]
    assert stat["path"] == "/etc/cloud/cloud-init.disabled"


def test_cloud_init_role_verifies_fail_loud() -> None:
    merged = _task(CI_ROLE, "read the merged cloud-init config")
    argv = " ".join(merged["ansible.builtin.command"]["argv"])
    assert "from cloudinit.stages import Init" in argv
    check = _task(CI_ROLE, "verify the merged config")["ansible.builtin.assert"]["that"]
    assert "_cfg.cloud_init_modules == cloud_init_trim_init_modules" in check
    masked = _task(CI_ROLE, "verify the cloud-init config + final services are masked")
    assert "masked" in str(masked["failed_when"])
    required = _task(CI_ROLE, "verify cloud-init-local + cloud-init stay enabled")
    assert "['enabled']" in str(required["failed_when"])
    gone = _task(CI_ROLE, "verify cloud-init is not disabled outright")
    assert gone["ansible.builtin.assert"]["that"] == "not _cloud_init_trim_disabled.stat.exists"


# --- snapd-off -----------------------------------------------------------------------


def test_snapd_purge_is_guarded_by_a_no_snaps_check() -> None:
    names = [t.get("name", "") for t in _tasks(SNAP_ROLE)]
    guard = names.index("refuse to purge snapd while a snap is installed")
    purge = names.index("purge snapd (no snap is installed, nothing uses it)")
    assert guard < purge, "the no-snaps guard must run before the purge"
    spec = _task(SNAP_ROLE, "purge snapd (no snap")["ansible.builtin.apt"]
    assert spec["name"] == "snapd" and spec["state"] == "absent" and spec["purge"] is True
    assert _task(SNAP_ROLE, "list the installed snaps")["ansible.builtin.command"]["argv"] == [
        "snap",
        "list",
    ]


def test_snapd_pin_blocks_reinstall_and_is_verified() -> None:
    pin = (SNAP_ROLE / "files" / "99lab-no-snapd").read_text(encoding="utf-8")
    stanza = [ln for ln in pin.splitlines() if ln and not ln.startswith("#")]
    assert stanza == ["Package: snapd", "Pin: release a=*", "Pin-Priority: -1"]
    assert _defaults(SNAP_ROLE)["snapd_off_pin"].startswith("/etc/apt/preferences.d/")
    check = _task(SNAP_ROLE, "verify the pin leaves snapd no install candidate")
    assert "Candidate: (none)" in check["ansible.builtin.assert"]["that"]
    gone = _task(SNAP_ROLE, "verify snapd is gone")
    assert "'snapd' not in ansible_facts.packages" in gone["ansible.builtin.assert"]["that"]


def test_snapd_keep_mode_masks_only_the_seeded_gate() -> None:
    assert _defaults(SNAP_ROLE)["snapd_off_seeded_unit"] == "snapd.seeded.service"
    mask = _task(SNAP_ROLE, "mask the snapd.seeded boot gate")
    assert mask["ansible.builtin.systemd"]["masked"] is True
    assert mask["when"] == "snapd_off_mode == 'keep'"
    verify = _task(SNAP_ROLE, "verify snapd.seeded is masked while snapd itself stays enabled")
    expected = "_snapd_off_units.stdout_lines != ['masked', 'enabled', 'enabled']"
    assert verify["failed_when"] == expected


@pytest.mark.parametrize("role", (CI_ROLE, SNAP_ROLE))
def test_roles_refuse_a_non_debian_host(role: Path) -> None:
    first = _tasks(role)[0]
    assert "ansible_os_family == 'Debian'" in str(first["ansible.builtin.assert"]["that"])


# --- manifest-hash closure -----------------------------------------------------------


def _hash(root: Path, box: str) -> str:
    script = root / "lab" / "boxes" / "_manifest-hash.sh"
    return subprocess.run(
        [str(script), box, *manifest_hash_args(box)], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("role", TRIM_ROLES)
def test_role_is_in_the_manifest_hash_closure(tmp_path: Path, role: str) -> None:
    """Editing the role must flip every Ubuntu box's manifest hash (forces a rebake), and
    leave the RHEL boxes' hash alone. Runs against a scratch copy of the hash inputs so the
    real tree is never mutated."""
    (tmp_path / "lab" / "boxes").mkdir(parents=True)
    shutil.copy2(MANIFEST_HASH, tmp_path / "lab" / "boxes" / MANIFEST_HASH.name)
    shutil.copy2(REPO_ROOT / "lab" / "mq-version", tmp_path / "lab" / "mq-version")
    shutil.copytree(ROLES, tmp_path / "ansible" / "roles")
    shutil.copytree(ANSIBLE / "group_vars", tmp_path / "ansible" / "group_vars")
    for bake in ANSIBLE.glob("bake-*.yml"):
        shutil.copy2(bake, tmp_path / "ansible" / bake.name)

    boxes = _box_bakes()
    before = {box: _hash(tmp_path, box) for box in boxes}
    probe = tmp_path / "ansible" / "roles" / role / "defaults" / "main.yml"
    probe.write_text(probe.read_text(encoding="utf-8") + "# probe\n", encoding="utf-8")
    after = {box: _hash(tmp_path, box) for box in boxes}

    for box, (kind, _stem) in boxes.items():
        if kind == "ubuntu":
            assert before[box] != after[box], f"{box}: {role} edit did not flip the manifest hash"
        else:
            assert before[box] == after[box], f"{box}: Ubuntu-only {role} edit flipped a RHEL hash"
