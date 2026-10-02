"""Guard the per-run configure halves' directories against the bake losing them (#1265).

#1265: a cold nativeha-ubuntu bootstrap failed in observe because the baked x86_64
obs-ubuntu2404 box had no /etc/systemd/system/grafana-server.service.d. The grafana install
half created it (empty: both drop-ins are per-run), then snapd-off's purge removed it:
snapd's postrm runs `deb-systemd-helper purge`, whose rmdir_if_empty deletes EVERY empty
directory under /etc/systemd/system and /etc/systemd/user. obs on aarch64 keeps snapd
(no purge), which is why its box kept the directory. Three fixes, each pinned here:

* grafana's configure half ensures the drop-in dir itself, before any drop-in is written;
* snapd-off records the empty /etc/systemd dirs before the purge and restores + verifies
  them after (snapd's own dirs stay gone);
* every Ubuntu bake ends with a bake-dirs-guard play that fails the bake if a directory a
  per-run configure half writes into is missing from the image. Its per-box list is
  derived-checked here against the install halves each bake runs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_boot_trim_baked import _box_bakes, _plays, _role_includes, _split

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
GUARD_ROLE = ROLES / "bake-dirs-guard"
SNAP_ROLE = ROLES / "snapd-off"
GRAFANA = ROLES / "grafana"
MANIFEST_HASH = REPO_ROOT / "lab" / "boxes" / "_manifest-hash.sh"
DROPIN_DIR = "/etc/systemd/system/grafana-server.service.d"


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _defaults(role: str) -> dict:
    return _yaml(ROLES / role / "defaults" / "main.yml")


# Per role whose INSTALL half a bake runs: (directory its per-run CONFIGURE half writes
# into, token proving configure.yml writes there, token proving install.yml creates it).
CONFIGURE_DIRS: dict[str, list[tuple[str, str, str]]] = {
    "grafana": [(DROPIN_DIR, "{{ grafana_dropin_dir }}/", "{{ grafana_dropin_dir }}")],
    "prometheus": [
        ("/etc/prometheus/targets", "dest: /etc/prometheus/targets/", "- /etc/prometheus/targets")
    ],
    "alloy": [("/etc/alloy", "dest: /etc/alloy/", "- /etc/alloy")],
    "bind-dns": [("/etc/bind/zones", 'dest: "/etc/bind/zones/', "path: /etc/bind/zones")],
    "opensearch": [
        (
            _defaults("opensearch")["opensearch_repo_dir"],
            'dest: "{{ opensearch_repo_dir }}"',
            '- "{{ opensearch_repo_dir }}"',
        )
    ],
}


def _guard_list(box: str, stem: str) -> list[str]:
    (task,) = _role_includes(_plays(stem)[-1], "bake-dirs-guard")
    dirs = (task.get("vars") or {}).get("bake_dirs_guard_required")
    assert isinstance(dirs, list) and dirs, f"{box}: the guard play needs a non-empty list"
    return dirs


def _install_halves(stem: str) -> set[str]:
    roles = set()
    for play in _plays(stem):
        for role in CONFIGURE_DIRS:
            for task in _role_includes(play, role):
                if task["ansible.builtin.include_role"].get("tasks_from") == "install":
                    roles.add(role)
    return roles


# --- fix 1: grafana configure is self-sufficient ---------------------------------------


def test_grafana_dropin_dir_is_one_variable() -> None:
    assert _defaults("grafana")["grafana_dropin_dir"] == DROPIN_DIR
    for half in ("install.yml", "configure.yml"):
        text = (GRAFANA / "tasks" / half).read_text(encoding="utf-8")
        live = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in live if DROPIN_DIR in ln], f"{half}: use grafana_dropin_dir"


def test_grafana_configure_creates_the_dropin_dir_before_any_dropin() -> None:
    tasks = _yaml(GRAFANA / "tasks" / "configure.yml")

    def _target(task: dict) -> str:
        for verb in ("ansible.builtin.copy", "ansible.builtin.template", "ansible.builtin.file"):
            spec = task.get(verb) or {}
            if "dest" in spec or "path" in spec:
                return str(spec.get("dest", spec.get("path")))
        return ""

    mkdir = [
        i
        for i, t in enumerate(tasks)
        if _target(t) == "{{ grafana_dropin_dir }}"
        and t["ansible.builtin.file"].get("state") == "directory"
    ]
    users = [i for i, t in enumerate(tasks) if _target(t).startswith("{{ grafana_dropin_dir }}/")]
    assert len(mkdir) == 1, "configure must ensure the drop-in dir exactly once (#1265)"
    assert len(users) == 3, f"expected rendering.conf write/remove + admin.conf, got {users}"
    assert mkdir[0] < min(users), "the dir must be ensured BEFORE any drop-in task (#1265)"
    spec = tasks[mkdir[0]]["ansible.builtin.file"]
    assert (spec["owner"], spec["group"], spec["mode"]) == ("root", "root", "0755")
    assert "when" not in tasks[mkdir[0]], "the drop-in dir is needed unconditionally"


# --- fix 2: snapd-off undoes the purge's empty-dir pruning ------------------------------


def test_snapd_off_restores_the_empty_systemd_dirs_around_the_purge() -> None:
    tasks = _yaml(SNAP_ROLE / "tasks" / "main.yml")
    names = [t.get("name", "") for t in tasks]

    def _at(fragment: str) -> int:
        (hit,) = [i for i, n in enumerate(names) if fragment in n]
        return hit

    order = [
        _at("record the empty dirs under /etc/systemd"),
        _at("keep the non-snapd empty dirs"),
        _at("purge snapd (no snap"),
        _at("restore the empty /etc/systemd dirs"),
        _at("look for every restored dir"),
        _at("verify no non-snapd dir under /etc/systemd was lost"),
    ]
    assert order == sorted(order), "record -> filter -> purge -> restore -> verify (#1265)"
    for i in order:
        assert tasks[i]["when"] == "snapd_off_mode == 'purge'", names[i]
    record = tasks[order[0]]["ansible.builtin.command"]["argv"]
    assert "snapd_off_prune_roots" in record and "'-empty'" in record
    restore = tasks[order[3]]["ansible.builtin.file"]
    assert restore["state"] == "directory"
    assert {"mode", "owner", "group"} <= set(restore), "restore the recorded mode/owner/group"
    verify = tasks[order[5]]["ansible.builtin.assert"]
    assert verify["that"] == "item.stat.isdir | default(false)"


def test_snapd_off_prune_roots_match_deb_systemd_helper() -> None:
    """rmdir_if_empty runs over <dpkg_root>/etc/systemd/system/* and .../user/*."""
    defaults = _defaults("snapd-off")
    assert defaults["snapd_off_prune_roots"] == ["/etc/systemd/system", "/etc/systemd/user"]
    assert defaults["snapd_off_dir_format"] == "%m %u %g %p\\n"


@pytest.mark.parametrize(
    ("line", "snapd_own"),
    [
        ("755 root root /etc/systemd/system/snapd.mounts.target.wants", True),
        ("755 root root /etc/systemd/system/snap-core24-1.mount.d", True),
        (f"755 root root {DROPIN_DIR}", False),
        ("755 root root /etc/systemd/system/sleep.target.wants", False),
        ("755 root root /etc/systemd/user/default.target.wants", False),
    ],
)
def test_snapd_off_keeps_only_snapds_own_dirs_gone(line: str, snapd_own: bool) -> None:
    regex = _defaults("snapd-off")["snapd_off_own_dir_regex"]
    assert bool(re.search(regex, line)) is snapd_own


# --- fix 3: the bake guard -------------------------------------------------------------


def test_every_ubuntu_bake_ends_with_the_guard_and_no_rhel_bake_has_it() -> None:
    ubuntu, rhel = _split()
    for box, stem in ubuntu.items():
        plays = _plays(stem)
        assert _role_includes(plays[-1], "bake-dirs-guard"), f"{box}: guard must be LAST"
        assert sum(len(_role_includes(p, "bake-dirs-guard")) for p in plays) == 1, box
        assert plays[-1]["become"] is True, f"{box}: the guard must stat as root"
    wrong = [box for box, stem in rhel.items() if _role_includes(_plays(stem), "bake-dirs-guard")]
    assert wrong == [], f"the guard is wired for the Ubuntu bakes only: {wrong}"


def test_guard_lists_every_configure_dir_of_the_install_halves_each_box_bakes() -> None:
    ubuntu, _ = _split()
    for box, stem in ubuntu.items():
        listed = _guard_list(box, stem)
        assert len(listed) == len(set(listed)), f"{box}: duplicate guard entries"
        expected = {d for role in _install_halves(stem) for d, _, _ in CONFIGURE_DIRS[role]}
        assert expected, f"{box}: every Ubuntu box bakes at least alloy's install half"
        assert expected <= set(listed), f"{box}: guard misses {sorted(expected - set(listed))}"
        assert set(listed) <= expected, f"{box}: guard lists dirs no baked role relies on"
    assert DROPIN_DIR in _guard_list("obs-ubuntu2404", "obs"), "the #1265 dir itself"


@pytest.mark.parametrize("role", sorted(CONFIGURE_DIRS))
def test_configure_dirs_map_matches_the_role_code(role: str) -> None:
    """Each mapped dir is created by the role's install half and written by its configure."""
    install = (ROLES / role / "tasks" / "install.yml").read_text(encoding="utf-8")
    configure = (ROLES / role / "tasks" / "configure.yml").read_text(encoding="utf-8")
    for path, writes, creates in CONFIGURE_DIRS[role]:
        assert writes in configure, f"{role}: configure no longer writes {path} ({writes!r})"
        assert creates in install, f"{role}: install no longer creates {path} ({creates!r})"


def test_guard_role_fails_loud() -> None:
    assert _defaults("bake-dirs-guard")["bake_dirs_guard_required"] == []
    tasks = _yaml(GUARD_ROLE / "tasks" / "main.yml")
    refuse = tasks[0]["ansible.builtin.assert"]
    assert refuse["that"] == "bake_dirs_guard_required | length > 0"
    assert tasks[1]["ansible.builtin.stat"]["path"] == "{{ item }}"
    final = tasks[-1]
    assert final["ansible.builtin.assert"]["that"] == "_bake_dirs_guard_missing | length == 0"
    assert "rejectattr('stat.exists')" in final["vars"]["_bake_dirs_guard_absent"]
    assert "rejectattr('stat.isdir')" in final["vars"]["_bake_dirs_guard_not_dir"]


# --- manifest-hash closure -------------------------------------------------------------


def _hash(root: Path, box: str) -> str:
    script = root / "lab" / "boxes" / "_manifest-hash.sh"
    return subprocess.run(
        [str(script), box], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize(
    ("role", "flips"),
    [
        ("bake-dirs-guard", "ubuntu"),
        ("snapd-off", "ubuntu"),
        ("grafana", "obs"),
    ],
)
def test_fix_roles_flip_the_right_manifest_hashes(tmp_path: Path, role: str, flips: str) -> None:
    """An edit to each #1265 role must force a rebake of exactly the boxes that bake it.
    Runs against a scratch copy of the hash inputs so the real tree is never mutated."""
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
        expect = kind == "ubuntu" if flips == "ubuntu" else box == "obs-ubuntu2404"
        assert (before[box] != after[box]) is expect, f"{box}: {role} edit, flip={expect}"
