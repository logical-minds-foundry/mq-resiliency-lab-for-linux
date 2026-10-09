"""Guard: no RHEL package install runs before the offline DVD repo exists (#1403).

The RHEL guests are offline and unregistered. Their only package source is the install
DVD, which the RHEL install adapters mount at ``/media/rhel`` and expose through
``/etc/yum.repos.d/rhel-dvd.repo``. A ``dnf`` / ``package`` task that runs before that
setup finds no repo. It passes only when the base box already carries the package, which
is how the ``mq-nativeha-rhel9`` bake installed ``acl`` first for months and why the same
playbook failed on RHEL 10, whose minimal base has no ``acl`` (#1403).

This test walks each RHEL entry playbook in Ansible's execution order, through
``import_playbook``, ``roles:``, ``include_role`` / ``import_role`` (``tasks_from``),
``include_tasks`` / ``import_tasks`` and ``block``s, tracking which host groups have had
their DVD repo set up. Any package-install task that reaches a group before that point
fails the test.

Limits, stated so nobody reads more into a green run than it proves:

- ``when:`` conditions are not evaluated, so the walk is over-inclusive: a role behind
  ``when:`` is still walked. The one exception is a task whose ``when`` pins it to the
  Debian family, which never runs on a RHEL guest.
- Handlers and ``meta`` role dependencies are not walked. No role ships a
  ``meta/main.yml`` today; the walker fails if one appears, so it cannot be skipped
  silently.
- An include path still templated after substituting ``ansible_os_family`` fails the
  walk rather than being skipped.
- Only the provision entry points are walked (the two RHEL bakes, ``site-nativeha.yml``,
  ``site-rdqm.yml``). The standalone plays (TLS, switchover, validation, observability)
  run against an already-provisioned guest whose repo is in place.
- On the per-run RDQM path the repo FILE is baked into the box (rdqm-install's install
  half) and the configure half only re-mounts the DVD, so for ``site-rdqm.yml`` the
  mount is the readiness point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"

DVD_REPO_FILE = "/etc/yum.repos.d/rhel-dvd.repo"
REPO = "repo"
MOUNT = "mount"

# The inventory groups whose guests run RHEL. The bake plays target the single `bake`
# host, which is RHEL for the RHEL bake playbooks walked here.
RHEL_GROUPS = frozenset({"nha_rhel_crr_a", "nha_rhel_crr_b", "rdqm_a", "rdqm_b"})
BAKE_GROUP = "bake"

# entry playbook -> (is a bake play, which task makes the repo usable)
ENTRIES = {
    "bake-nativeha-rhel.yml": (True, REPO),
    "bake-mq-rdqm.yml": (True, REPO),
    "site-nativeha.yml": (False, REPO),
    "site-rdqm.yml": (False, MOUNT),
}

PKG_MODULES = frozenset(
    {
        "ansible.builtin.dnf",
        "dnf",
        "ansible.builtin.yum",
        "yum",
        "ansible.builtin.package",
        "package",
    }
)
SHELL_MODULES = ("ansible.builtin.shell", "shell", "ansible.builtin.command", "command")
INCLUDE_ROLE = ("ansible.builtin.include_role", "include_role")
IMPORT_ROLE = ("ansible.builtin.import_role", "import_role")
INCLUDE_TASKS = (
    "ansible.builtin.include_tasks",
    "include_tasks",
    "ansible.builtin.import_tasks",
    "import_tasks",
)
COPY_MODULES = ("ansible.builtin.copy", "copy")
SHELL_INSTALL = re.compile(r"\b(?:dnf|yum)\s+(?:-y\s+)?install\b")
DEBIAN_ONLY = re.compile(r"ansible_os_family\s*==\s*['\"]Debian['\"]")
MAX_DEPTH = 30


class WalkError(Exception):
    """The walker met a construct it cannot follow; failing beats skipping it."""


@dataclass
class Walk:
    """The ordered outcome of walking one entry playbook."""

    marker: str
    ready: set[str] = field(default_factory=set)
    installs: list[tuple[str, str, frozenset[str]]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    acl_after_repo: set[str] = field(default_factory=set)


def _load(path: Path) -> Any:
    if not path.is_file():
        raise WalkError(f"include target does not exist: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def _first(task: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in task:
            return task[key]
    return None


def _install_target(task: dict[str, Any]) -> str | None:
    """What a package-install task installs (a short label), or None for any other task."""
    for module in PKG_MODULES:
        if module in task:
            spec = task[module]
            name = spec.get("name") if isinstance(spec, dict) else spec
            return str(name)
    shell = _first(task, SHELL_MODULES)
    text = shell.get("cmd", "") if isinstance(shell, dict) else str(shell or "")
    if SHELL_INSTALL.search(text):
        return "shell dnf install"
    return None


def _marker_kind(task: dict[str, Any]) -> str | None:
    copy = _first(task, COPY_MODULES)
    if isinstance(copy, dict) and copy.get("dest") == DVD_REPO_FILE:
        return REPO
    shell = _first(task, SHELL_MODULES)
    if isinstance(shell, str) and "mount /media/rhel" in shell:
        return MOUNT
    return None


def _debian_only(task: dict[str, Any]) -> bool:
    when = task.get("when")
    conditions = when if isinstance(when, list) else [when]
    return any(isinstance(c, str) and DEBIAN_ONLY.search(c) for c in conditions)


def _resolve(path: str, base: Path) -> Path:
    path = path.replace("{{ ansible_os_family }}", "RedHat")
    if "{{" in path:
        raise WalkError(f"templated include path the walker cannot resolve: {path!r}")
    return (base / path).resolve()


def _role_tasks(root: Path, name: str, tasks_from: str | None) -> Path:
    role = root / "roles" / name
    if (role / "meta" / "main.yml").is_file():
        raise WalkError(f"role {name} has meta/main.yml; teach the walker its dependencies")
    stem = tasks_from or "main"
    return role / "tasks" / (stem if stem.endswith(".yml") else f"{stem}.yml")


def _walk_tasks(
    tasks: Any, here: Path, root: Path, groups: frozenset[str], walk: Walk, depth: int
) -> None:
    if depth > MAX_DEPTH:
        raise WalkError(f"include depth over {MAX_DEPTH} at {here}; an include cycle?")
    for task in tasks or []:
        if not isinstance(task, dict) or _debian_only(task):
            continue
        for key in ("block", "rescue", "always"):
            _walk_tasks(task.get(key), here, root, groups, walk, depth + 1)
        role = _first(task, INCLUDE_ROLE + IMPORT_ROLE)
        if role is not None:
            path = _role_tasks(root, role["name"], role.get("tasks_from"))
            _walk_tasks(_load(path), path.parent, root, groups, walk, depth + 1)
            continue
        include = _first(task, INCLUDE_TASKS)
        if include is not None:
            target = include["file"] if isinstance(include, dict) else include
            path = _resolve(target, here)
            _walk_tasks(_load(path), path.parent, root, groups, walk, depth + 1)
            continue
        _visit(task, here, groups, walk)


def _visit(task: dict[str, Any], here: Path, groups: frozenset[str], walk: Walk) -> None:
    if _marker_kind(task) == walk.marker:
        walk.ready |= groups
        return
    target = _install_target(task)
    if target is None:
        return
    label = f"{here.name}: {task.get('name', '(unnamed)')} [{target}]"
    walk.installs.append((label, target, groups))
    missing = groups - walk.ready
    if missing:
        walk.violations.append(f"{label} runs on {sorted(missing)} before the DVD repo")
    elif target == "acl":
        walk.acl_after_repo |= groups


def _play_groups(play: dict[str, Any], bake: bool) -> frozenset[str]:
    if bake:
        return frozenset({BAKE_GROUP})
    tokens = set(re.findall(r"[A-Za-z0-9_]+", str(play.get("hosts", ""))))
    return frozenset(tokens & RHEL_GROUPS)


def walk_playbook(path: Path, root: Path, bake: bool, walk: Walk, depth: int = 0) -> Walk:
    """Walk one playbook (and its imports) in execution order, recording into `walk`."""
    for play in _load(path):
        if "import_playbook" in play:
            imported = _resolve(play["import_playbook"], path.parent)
            walk_playbook(imported, root, bake, walk, depth + 1)
            continue
        groups = _play_groups(play, bake)
        if not groups:
            continue
        _walk_tasks(play.get("pre_tasks"), path.parent, root, groups, walk, depth)
        for entry in play.get("roles") or []:
            name = entry if isinstance(entry, str) else entry.get("role", entry.get("name"))
            role_path = _role_tasks(root, name, None)
            _walk_tasks(_load(role_path), role_path.parent, root, groups, walk, depth + 1)
        _walk_tasks(play.get("tasks"), path.parent, root, groups, walk, depth)
        _walk_tasks(play.get("post_tasks"), path.parent, root, groups, walk, depth)
    return walk


def _walk_entry(name: str) -> tuple[Walk, frozenset[str]]:
    bake, marker = ENTRIES[name]
    walk = walk_playbook(ANSIBLE / name, ANSIBLE, bake, Walk(marker))
    if bake:
        return walk, frozenset({BAKE_GROUP})
    return walk, frozenset().union(*(groups for _, _, groups in walk.installs))


@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_no_package_install_precedes_the_dvd_repo(entry: str) -> None:
    walk, _ = _walk_entry(entry)
    assert walk.installs, f"{entry}: walked no package installs; the guard is looking at nothing"
    assert walk.violations == [], f"{entry}: package installs before the offline repo (#1403)"


@pytest.mark.parametrize("entry", sorted(ENTRIES))
def test_acl_is_installed_after_the_dvd_repo_on_every_rhel_group(entry: str) -> None:
    """Pin the #1403 fix itself: every RHEL group gets acl, and only once its repo is up."""
    walk, targeted = _walk_entry(entry)
    assert targeted, f"{entry}: reached no RHEL group"
    assert walk.acl_after_repo == targeted, (
        f"{entry}: groups without an acl install after the repo: "
        f"{sorted(targeted - walk.acl_after_repo)}"
    )


def test_site_plays_reach_every_rhel_group() -> None:
    """The RHEL group list is not stale: the two site entries reach all four groups."""
    reached = _walk_entry("site-nativeha.yml")[1] | _walk_entry("site-rdqm.yml")[1]
    assert reached == RHEL_GROUPS


# --- the walker itself, on synthetic trees ------------------------------------------------


def _tree(tmp_path: Path, playbook: list[dict[str, Any]], files: dict[str, Any]) -> Path:
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(body), encoding="utf-8")
    pb = tmp_path / "pb.yml"
    pb.write_text(yaml.safe_dump(playbook), encoding="utf-8")
    return pb


REPO_TASK = {"name": "repo", "ansible.builtin.copy": {"dest": DVD_REPO_FILE, "content": "x"}}
MOUNT_TASK = {
    "name": "mount",
    "ansible.builtin.shell": "mountpoint -q /media/rhel || mount /media/rhel",
}
ACL_TASK = {"name": "acl", "ansible.builtin.dnf": {"name": "acl", "state": "present"}}


def _run(pb: Path, root: Path, bake: bool = True, marker: str = REPO) -> Walk:
    return walk_playbook(pb, root, bake, Walk(marker))


def test_walker_flags_the_1403_order(tmp_path: Path) -> None:
    """The pre-fix bake shape: acl first, then the role that writes the repo."""
    files = {"roles/r/tasks/install-RedHat.yml": [REPO_TASK]}
    include = {"ansible.builtin.include_role": {"name": "r", "tasks_from": "install-RedHat"}}
    play = {"hosts": "bake", "tasks": [ACL_TASK, include]}
    walk = _run(_tree(tmp_path, [play], files), tmp_path)
    assert len(walk.violations) == 1
    assert "acl" in walk.violations[0]
    assert walk.acl_after_repo == set()


def test_walker_accepts_the_fixed_order_through_nested_includes(tmp_path: Path) -> None:
    files = {
        "roles/r/tasks/main.yml": [{"include_tasks": "install-{{ ansible_os_family }}.yml"}],
        "roles/r/tasks/install-RedHat.yml": [
            {"ansible.builtin.include_tasks": {"file": "../../../tasks/shared.yml"}},
            REPO_TASK,
            {"block": [ACL_TASK], "rescue": [], "always": []},
        ],
        "roles/r/tasks/install-Debian.yml": [ACL_TASK],
        "tasks/shared.yml": [{"name": "noop", "ansible.builtin.debug": {"msg": "x"}}],
        "roles/q/tasks/main.yml": [
            {"name": "apt only", "ansible.builtin.apt": {"name": "acl"}},
            {
                "name": "deb",
                "ansible.builtin.package": {"name": "x"},
                "when": "ansible_os_family == 'Debian'",
            },
            {"name": "deb list", "package": "y", "when": ["x", 'ansible_os_family == "Debian"']},
            {"name": "shell", "ansible.builtin.shell": {"cmd": "dnf -y install foo"}},
            {"name": "cmd", "command": "echo hi"},
            "not-a-task",
        ],
    }
    play = {"hosts": "rdqm_a:rdqm_b", "roles": ["r", {"role": "q"}], "post_tasks": [ACL_TASK]}
    other = {"hosts": "localhost", "tasks": [ACL_TASK]}
    walk = _run(_tree(tmp_path, [other, play], files), tmp_path, bake=False)
    assert walk.violations == []
    assert walk.acl_after_repo == {"rdqm_a", "rdqm_b"}
    assert [t for _, t, _ in walk.installs] == ["acl", "shell dnf install", "acl"]


def test_walker_mount_marker_and_imported_playbook(tmp_path: Path) -> None:
    files = {"inner.yml": [{"hosts": "rdqm_b", "tasks": [MOUNT_TASK, REPO_TASK, ACL_TASK]}]}
    walk = _run(_tree(tmp_path, [{"import_playbook": "inner.yml"}], files), tmp_path, False, MOUNT)
    assert walk.violations == []
    assert walk.acl_after_repo == {"rdqm_b"}


def test_walker_mount_does_not_count_where_the_repo_is_required(tmp_path: Path) -> None:
    play = {"hosts": "bake", "pre_tasks": [MOUNT_TASK, ACL_TASK]}
    walk = _run(_tree(tmp_path, [play], {}), tmp_path)
    assert len(walk.violations) == 1


@pytest.mark.parametrize(
    ("files", "task", "match"),
    [
        ({}, {"include_tasks": "missing.yml"}, "does not exist"),
        ({}, {"include_tasks": "{{ x }}.yml"}, "templated include"),
        (
            {"roles/m/meta/main.yml": {"dependencies": []}, "roles/m/tasks/main.yml": []},
            {"import_role": {"name": "m"}},
            "meta/main.yml",
        ),
        (
            {"loop.yml": [{"include_tasks": "loop.yml"}]},
            {"include_tasks": "loop.yml"},
            "include depth",
        ),
    ],
)
def test_walker_fails_loudly_on_what_it_cannot_follow(
    tmp_path: Path, files: dict[str, Any], task: dict[str, Any], match: str
) -> None:
    pb = _tree(tmp_path, [{"hosts": "bake", "tasks": [task]}], files)
    with pytest.raises(WalkError, match=match):
        _run(pb, tmp_path)


def test_walker_treats_an_empty_include_as_no_tasks(tmp_path: Path) -> None:
    (tmp_path / "empty.yml").write_text("", encoding="utf-8")
    pb = _tree(tmp_path, [{"hosts": "bake", "tasks": [{"include_tasks": "empty.yml"}]}], {})
    assert _run(pb, tmp_path).installs == []
