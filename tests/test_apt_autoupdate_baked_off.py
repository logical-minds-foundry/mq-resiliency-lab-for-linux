"""Guard that apt auto-updates are permanently OFF in every baked Ubuntu box (#1225).

The #1200 runs showed Ubuntu's apt-daily / apt-daily-upgrade / unattended-upgrades firing
on every freshly booted guest and holding the dpkg lock, so provision's #1173 wait cost
18s / 73s / 208s across three otherwise-identical runs. The boxes are short-lived and
rebuilt cold about weekly (the staleness gate enforces it), so that rebuild is the update
path and the in-guest updater is turned off at BAKE time. These guards pin that:

* every Ubuntu bake (derived from build-fatbox.sh, so a new Ubuntu box cannot slip past)
  includes the `apt-autoupdate-off` role, and no RHEL bake does;
* the role masks all five units, waits out an in-flight run before masking the services,
  zeroes APT::Periodic, and verifies the masks fail-loud;
* the bake include is reachable by the manifest-hash role closure, so a later edit to the
  role flips the hash and forces a rebake.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "apt-autoupdate-off"
FATBOX = REPO_ROOT / "lab" / "boxes" / "build-fatbox.sh"
MANIFEST_HASH = REPO_ROOT / "lab" / "boxes" / "_manifest-hash.sh"

UNITS = {
    "apt-daily.timer",
    "apt-daily-upgrade.timer",
    "apt-daily.service",
    "apt-daily-upgrade.service",
    "unattended-upgrades.service",
}


def _box_bakes() -> dict[str, tuple[str, str]]:
    """box -> (base kind, bake stem), parsed from build-fatbox.sh's --box case."""
    text = FATBOX.read_text(encoding="utf-8")
    rows = re.findall(r"^\s*([a-z0-9-]+)\)\s+BASE_KIND=(\w+);.*BAKE=([a-z0-9-]+)", text, re.M)
    assert rows, f"could not parse the --box table from {FATBOX}"
    return {box: (kind, stem) for box, kind, stem in rows}


def _iter_tasks(node: Any) -> Any:
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _includes_role(path: Path, role: str) -> bool:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for task in _iter_tasks(doc):
        for verb in ("ansible.builtin.include_role", "ansible.builtin.import_role"):
            spec = task.get(verb)
            if isinstance(spec, dict) and spec.get("name") == role:
                return True
    return False


def _role_tasks() -> list[dict]:
    tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks
    return tasks


def _defaults() -> dict:
    return yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))


def test_every_ubuntu_bake_includes_the_role_and_no_rhel_bake_does() -> None:
    bakes = _box_bakes()
    ubuntu = {box: stem for box, (kind, stem) in bakes.items() if kind == "ubuntu"}
    rhel = {box: stem for box, (kind, stem) in bakes.items() if kind == "rhel"}
    assert ubuntu and rhel, f"expected both Ubuntu and RHEL boxes in {FATBOX}; got {bakes}"
    missing = [
        box
        for box, stem in ubuntu.items()
        if not _includes_role(ANSIBLE / f"bake-{stem}.yml", "apt-autoupdate-off")
    ]
    assert missing == [], f"Ubuntu bakes missing the apt-autoupdate-off role (#1225): {missing}"
    wrong = [
        box
        for box, stem in rhel.items()
        if _includes_role(ANSIBLE / f"bake-{stem}.yml", "apt-autoupdate-off")
    ]
    assert wrong == [], f"RHEL bakes must not include the Ubuntu-only role (#1225): {wrong}"


def test_role_disables_runs_first_in_each_ubuntu_bake() -> None:
    """The disable must be the bake's FIRST play so the bake's own apt work never races an
    auto-update run (and the role's masks are in place before anything else)."""
    for box, (kind, stem) in _box_bakes().items():
        if kind != "ubuntu":
            continue
        plays = yaml.safe_load((ANSIBLE / f"bake-{stem}.yml").read_text(encoding="utf-8"))
        first = [plays[0]]
        assert _includes_role_doc(first, "apt-autoupdate-off"), (
            f"{box}: apt-autoupdate-off must run in the bake's first play (#1225)"
        )


def _includes_role_doc(doc: Any, role: str) -> bool:
    for task in _iter_tasks(doc):
        spec = task.get("ansible.builtin.include_role")
        if isinstance(spec, dict) and spec.get("name") == role:
            return True
    return False


def test_role_masks_all_five_units() -> None:
    defaults = _defaults()
    timers = set(defaults["apt_autoupdate_timers"])
    services = set(defaults["apt_autoupdate_services"])
    assert timers | services == UNITS, f"role must cover exactly {sorted(UNITS)}"
    masks = [
        t for t in _role_tasks() if (t.get("ansible.builtin.systemd") or {}).get("masked") is True
    ]
    loops = {str(t.get("loop")) for t in masks}
    assert "{{ apt_autoupdate_timers }}" in loops and "{{ apt_autoupdate_services }}" in loops, (
        f"role must mask both the timer and the service lists (#1225); masked loops={loops}"
    )


def test_role_waits_out_in_flight_run_before_masking_services() -> None:
    """Never kill a mid-dpkg run: the timers are masked, then a bounded until-loop waits for
    the oneshot services to go idle, and only then are the services masked."""
    names = [t.get("name", "") for t in _role_tasks()]
    tasks = _role_tasks()
    i_timers = next(i for i, t in enumerate(tasks) if "apt_autoupdate_timers" in str(t.get("loop")))
    i_wait = next(i for i, n in enumerate(names) if "in-flight" in n)
    i_svcs = next(i for i, t in enumerate(tasks) if "apt_autoupdate_services" in str(t.get("loop")))
    assert i_timers < i_wait < i_svcs, f"order: mask timers -> wait -> mask services: {names}"
    wait = tasks[i_wait]
    assert wait.get("until") and wait.get("retries") and wait.get("delay")
    assert "activating" in str(wait["until"]), "oneshot 'activating' must count as busy"
    svc_mask = tasks[i_svcs]["ansible.builtin.systemd"]
    assert "state" not in svc_mask, "services must be masked, never stopped/killed mid-run"


def test_role_zeroes_apt_periodic() -> None:
    copy = next(t["ansible.builtin.copy"] for t in _role_tasks() if "ansible.builtin.copy" in t)
    assert copy["dest"] == "{{ apt_autoupdate_conf }}"
    dest = _defaults()["apt_autoupdate_conf"]
    assert dest.startswith("/etc/apt/apt.conf.d/99"), (
        f"drop-in must sort after 10periodic/20auto-upgrades to win; got {dest}"
    )
    conf = (ROLE / "files" / copy["src"]).read_text(encoding="utf-8")
    for knob in ("Update-Package-Lists", "Download-Upgradeable-Packages", "Unattended-Upgrade"):
        assert f'APT::Periodic::{knob} "0";' in conf, f"APT::Periodic::{knob} must be 0"


def test_role_verifies_masks_fail_loud() -> None:
    verify = next(t for t in _role_tasks() if "verify" in t.get("name", ""))
    assert "is-enabled" in str(verify["ansible.builtin.command"])
    assert "masked" in str(verify.get("failed_when")), "the verify must fail unless all masked"


def _hash(root: Path, box: str) -> str:
    script = root / "lab" / "boxes" / "_manifest-hash.sh"
    return subprocess.run(
        [str(script), box], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_role_is_in_the_manifest_hash_closure(tmp_path: Path) -> None:
    """Editing the role must flip every Ubuntu box's manifest hash (forces a rebake), and
    leave the RHEL boxes' hash alone. Runs against a scratch copy of the hash inputs so the
    real tree is never mutated."""
    (tmp_path / "lab" / "boxes").mkdir(parents=True)
    shutil.copy2(MANIFEST_HASH, tmp_path / "lab" / "boxes" / MANIFEST_HASH.name)
    shutil.copy2(REPO_ROOT / "lab" / "mq-version", tmp_path / "lab" / "mq-version")
    shutil.copytree(ANSIBLE / "roles", tmp_path / "ansible" / "roles")
    shutil.copytree(ANSIBLE / "group_vars", tmp_path / "ansible" / "group_vars")
    for bake in ANSIBLE.glob("bake-*.yml"):
        shutil.copy2(bake, tmp_path / "ansible" / bake.name)

    boxes = _box_bakes()
    before = {box: _hash(tmp_path, box) for box in boxes}
    probe = tmp_path / "ansible" / "roles" / "apt-autoupdate-off" / "defaults" / "main.yml"
    probe.write_text(probe.read_text(encoding="utf-8") + "# probe\n", encoding="utf-8")
    after = {box: _hash(tmp_path, box) for box in boxes}

    for box, (kind, _stem) in boxes.items():
        if kind == "ubuntu":
            assert before[box] != after[box], f"{box}: role edit did not flip the manifest hash"
        else:
            assert before[box] == after[box], f"{box}: Ubuntu-only role edit flipped a RHEL hash"
