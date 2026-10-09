"""Guard: the lab never stages large artifacts in ``/tmp`` (#1417).

Ubuntu 26.04 mounts ``/tmp`` as tmpfs sized at 50% of RAM (974M on a 1.9 GB build VM).
The obs bake ran it out of space unpacking prometheus/loki/logcli/alloy/node_exporter,
and the multi-GB MQ tarball with its ``MQServer/`` tree could never fit. The tmpfs
default stays; the lab stages on disk instead, under ONE variable, ``lab_stage_dir``
(``ansible/group_vars/all/staging.yml``), created at the point of use by
``ansible/tasks/lab-stage-dir.yml``.

What this pins:

- **No /tmp path anywhere it could be staged.** Every non-comment line under
  ``ansible/``, ``lab/boxes/`` and ``src/mqlab/`` is scanned for a ``/tmp`` path
  (``/var/tmp`` and ``/etc/tmpfiles.d`` do not match). A hit fails unless it is on
  ``ALLOWED``, the reviewed list of small, legitimately ephemeral files, each with its
  reason. A stale allow-list entry fails too, so the list cannot rot.
- **Every unarchive lands somewhere reviewed.** Its ``dest`` is ``lab_stage_dir`` or a
  listed final install dir; a new unarchive must be added to one or the other.
- **The MQ media and observability unpacks use lab_stage_dir**, by task name, and the
  MQ install files remove the staged media once installed.
- **Every task file that stages creates the dir first**: the include of
  ``lab-stage-dir.yml`` precedes the first task that names ``lab_stage_dir``.
- **No staging or cleanup task softens a failure** (no ``ignore_errors``/``failed_when``).
- **Ansible's remote temp stays on disk**: ``ansible.cfg`` pins ``remote_tmp`` to
  ``~/.ansible/tmp`` and nothing else sets it.
- ``lab_stage_dir`` is absolute, on ``/var/tmp``, and equal to ``mqlab``'s
  ``GUEST_STAGE_DIR`` (the ad-hoc snapshot transport, which loads no group_vars).
"""

# S108 (hard-coded temp path) is the subject of this file: every /tmp literal here is a
# path the guard detects, allows by review, or asserts the lab no longer uses.
# ruff: noqa: S108

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from mqlab import logsearch

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
ROLES = ANSIBLE / "roles"
STAGING_VARS = ANSIBLE / "group_vars" / "all" / "staging.yml"
STAGE_DIR_TASKS = ANSIBLE / "tasks" / "lab-stage-dir.yml"
STAGE_INCLUDE = "../../../tasks/lab-stage-dir.yml"
STAGE = "{{ lab_stage_dir }}"

# Where a /tmp path could stage something on a guest (or on the build VM).
SCAN_ROOTS = (ANSIBLE, REPO_ROOT / "lab" / "boxes", REPO_ROOT / "src" / "mqlab")

# A /tmp path: not preceded by a path character (so /var/tmp, x/tmp and {{ v }}/tmp do
# not match) and not continued by a word character (so /etc/tmpfiles.d does not match).
# The token runs on through path characters, which is what ALLOWED keys on.
TMP_PATH = re.compile(r"(?<![\w.}/-])/tmp(?![\w-])[\w./${}*-]*")

# (repo-relative file, /tmp token) -> why it may stay. Small and ephemeral only: anything
# that is downloaded, unpacked or copied in bulk belongs in lab_stage_dir instead.
ALLOWED: dict[tuple[str, str], str] = {
    ("ansible/tasks/lab-stage-dir.yml", "/tmp"): (
        "the runtime assertion that rejects a lab_stage_dir under /tmp, and its message"
    ),
    ("ansible/roles/mq-event-monitor/templates/run.sh.j2", "/tmp/mq-event-monitor.${QM}.stderr"): (
        "the last amqsevt run's stderr, a few lines, overwritten each loop"
    ),
    ("ansible/site-nativeha-spike-crr.yml", "/tmp/nhaspike-ks/keystore.{{"): (
        "controller-side hand-off of four small keystore files (throwaway spike)"
    ),
    ("ansible/site-pcmk-authz-validate.yml", "/tmp/authz-mqmon-ks/"): (
        "controller-side fetch of the two small mq_prometheus keystore files"
    ),
    ("ansible/site-pcmk-authz-validate.yml", "/tmp/authz-mqmon-ks/{{"): (
        "controller-side source of the same two keystore files"
    ),
    ("ansible/site-pcmk-authz-validate.yml", "/tmp/authz-n2-rogue"): (
        "the N2 probe's minted rogue keystore (a few KB), removed by the play's teardown"
    ),
    ("ansible/site-pcmk-authz-validate.yml", "/tmp/authz-n2-rogue/rogue.p12"): (
        "the same rogue keystore file"
    ),
    ("ansible/site-nativeha-ubuntu-authz-validate.yml", "/tmp/authz-nhau-mqmon-ks/"): (
        "controller-side fetch of the two small mq_prometheus keystore files"
    ),
    ("ansible/site-nativeha-ubuntu-authz-validate.yml", "/tmp/authz-nhau-mqmon-ks/{{"): (
        "controller-side source of the same two keystore files"
    ),
    ("ansible/site-rdqm-authz-validate.yml", "/tmp/authz-rdqm-mqmon-ks/"): (
        "controller-side fetch of the two small mq_prometheus keystore files"
    ),
    ("ansible/site-rdqm-authz-validate.yml", "/tmp/authz-rdqm-mqmon-ks/{{"): (
        "controller-side source of the same two keystore files"
    ),
}

# Unarchive destinations that are a final install location, not staging. A new
# unarchive must unpack into lab_stage_dir or be added here, with its reason.
INSTALL_DESTS = {
    "/usr/share": "opensearch / opensearch-dashboards / data-prepper unpack in place",
    "{{ opensearch_repo_dir }}": "a restored snapshot unpacks straight into path.repo",
}

UNARCHIVE = ("ansible.builtin.unarchive", "unarchive")
INCLUDE_TASKS = ("ansible.builtin.include_tasks", "include_tasks")

# (task file, unarchive task name) whose dest must be lab_stage_dir.
STAGED_UNARCHIVES = [
    ("prometheus/tasks/install.yml", "download + unpack prometheus"),
    ("loki/tasks/install.yml", "download + unpack loki"),
    ("loki/tasks/install.yml", "download + unpack logcli"),
    ("alloy/tasks/install.yml", "download + unpack alloy"),
    ("node-exporter/tasks/main.yml", "download + unpack node_exporter"),
    ("mq-install/tasks/main.yml", "unpack"),
    ("mq-client/tasks/main.yml", "unpack"),
    ("mq-nativeha/tasks/install-RedHat.yml", "unpack"),
    ("mq-nativeha/tasks/install-Debian.yml", "unpack"),
    ("mq-nativeha-spike/tasks/main.yml", "unpack"),
    ("rdqm-install/tasks/install.yml", "unpack"),
]
MQ_MEDIA_FILES = sorted({f for f, name in STAGED_UNARCHIVES if name == "unpack"})


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _flatten(node: Any) -> list[dict[str, Any]]:
    """Every task/block dict, in execution order, descending into plays and blocks."""
    out: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_flatten(item))
    elif isinstance(node, dict):
        out.append(node)
        for key in ("pre_tasks", "tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            out.extend(_flatten(node.get(key)))
    return out


def _task_files() -> list[Path]:
    """Every YAML task list or playbook under ansible/ (vars files hold no tasks)."""
    files = sorted(ANSIBLE.glob("*.yml")) + sorted(ANSIBLE.glob("tasks/*.yml"))
    for sub in ("tasks", "handlers"):
        files += sorted(ROLES.glob(f"*/{sub}/*.yml"))
    return files


def _module_args(task: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any] | None:
    for name in names:
        if name in task:
            args: dict[str, Any] = task[name]
            return args
    return None


def _named(rel: str, name: str) -> dict[str, Any]:
    tasks = [t for t in _flatten(_load(ROLES / rel)) if t.get("name") == name]
    assert len(tasks) == 1, f"{rel}: expected exactly one task named {name!r}"
    return tasks[0]


def _tmp_hits() -> set[tuple[str, str]]:
    hits: set[tuple[str, str]] = set()
    for root in SCAN_ROOTS:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if "__pycache__" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                hits.update((_rel(path), m.group(0)) for m in TMP_PATH.finditer(line))
    return hits


# --- the text scan -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "token"),
    [
        ("    dest: /tmp", "/tmp"),
        ('    src: "/tmp/{{ pkg }}/x"', "/tmp/{{"),
        ("cd /tmp/MQServer", "/tmp/MQServer"),
        ("tar xzf a.tgz -C /tmp", "/tmp"),
    ],
)
def test_scan_catches_tmp_paths(line: str, token: str) -> None:
    assert [m.group(0) for m in TMP_PATH.finditer(line)] == [token]


@pytest.mark.parametrize(
    "line",
    [
        "dest: /var/tmp/lab-staging",
        "dest: /etc/tmpfiles.d/x.conf",
        'path: "{{ lab_stage_dir }}/tmp"',
        "remote_tmp = ~/.ansible/tmp",
        "tmp_path / 'x'",
    ],
)
def test_scan_ignores_non_tmp_paths(line: str) -> None:
    assert TMP_PATH.search(line) is None


def test_no_unreviewed_tmp_path() -> None:
    unreviewed = sorted(_tmp_hits() - set(ALLOWED))
    assert unreviewed == [], (
        "these stage under /tmp, a RAM-sized tmpfs on Ubuntu 26.04 (#1417): move them to "
        f"{STAGE}, or, for a small ephemeral file only, add a reasoned ALLOWED entry: "
        f"{unreviewed}"
    )


def test_allow_list_has_no_stale_entries() -> None:
    stale = sorted(set(ALLOWED) - _tmp_hits())
    assert stale == [], f"ALLOWED entries that no longer match anything: {stale}"


# --- unarchive destinations ----------------------------------------------------------


def test_every_unarchive_lands_in_staging_or_a_reviewed_install_dir() -> None:
    seen = []
    for path in _task_files():
        for task in _flatten(_load(path)):
            args = _module_args(task, UNARCHIVE)
            if args is None:
                continue
            dest = args.get("dest")
            seen.append(dest)
            assert dest == STAGE or dest in INSTALL_DESTS, (
                f"{_rel(path)}: {task.get('name')!r} unpacks into {dest!r}; stage it in "
                f"{STAGE} or add a reviewed install dir to INSTALL_DESTS"
            )
    assert seen, "found no unarchive tasks at all: the walk is broken"


@pytest.mark.parametrize(("rel", "name"), STAGED_UNARCHIVES)
def test_media_and_observability_unpacks_use_lab_stage_dir(rel: str, name: str) -> None:
    args = _module_args(_named(rel, name), UNARCHIVE)
    assert args is not None, f"{rel}: {name!r} is not an unarchive"
    assert args["dest"] == STAGE
    assert str(args["creates"]).startswith(f"{STAGE}/")


@pytest.mark.parametrize("rel", MQ_MEDIA_FILES)
def test_mq_media_is_staged_and_removed(rel: str) -> None:
    unpack_task = _named(rel, "unpack")
    unpack = _module_args(unpack_task, UNARCHIVE)
    assert unpack is not None
    assert unpack["src"] == f"{STAGE}/mqadv.tar.gz"
    assert unpack["creates"] == f"{STAGE}/MQServer"
    tasks = _flatten(_load(ROLES / rel))
    tarball = f"{STAGE}/mqadv.tar.gz"
    copies = [t for t in tasks if (t.get("ansible.builtin.copy") or {}).get("dest") == tarball]
    assert len(copies) == 1, f"{rel}: the MQ tarball must be copied to {tarball}"
    (at,) = [i for i, t in enumerate(tasks) if t.get("name") == "remove the staged MQ media"]
    cleanup = tasks[at]
    assert cleanup["ansible.builtin.file"] == {"path": "{{ item }}", "state": "absent"}
    assert cleanup["loop"] == [tarball, f"{STAGE}/MQServer"]
    last_use = max(i for i, t in enumerate(tasks) if "MQServer" in _own(t) and i != at)
    assert at > last_use, f"{rel}: media removed before its last use"
    # Root: lab_stage_dir is root-owned, and some plays apply these roles without become.
    for task in [*copies, unpack_task, cleanup]:
        assert task.get("become") is True, f"{rel}: {task.get('name')!r} needs become: true"


# --- the staging dir is created before use, and nothing softens a failure ------------


def _stage_users() -> list[Path]:
    return [
        p
        for p in _task_files()
        if p != STAGE_DIR_TASKS and "lab_stage_dir" in p.read_text(encoding="utf-8")
    ]


def _own(task: dict[str, Any]) -> str:
    """The task's own YAML, without the nested tasks of a block."""
    return yaml.safe_dump({k: v for k, v in task.items() if k not in ("block", "rescue", "always")})


def _touches_stage(task: dict[str, Any]) -> bool:
    """A task that names lab_stage_dir. A set_fact counts: component-install derives its
    staging path from it there, and every later use goes through that fact."""
    return "lab_stage_dir" in _own(task)


def _includes_stage_dir(task: dict[str, Any]) -> bool:
    return any(task.get(key) == STAGE_INCLUDE for key in INCLUDE_TASKS)


def _softened(task: dict[str, Any]) -> list[str]:
    """Keys that would let a failing task pass quietly. A ``failed_when`` that adds a
    failure condition (runtime-install's checksum check) is a strengthening, not one."""
    found = [k for k in ("ignore_errors", "ignore_unreachable") if task.get(k)]
    if "failed_when" in task and task["failed_when"] in (False, "false", "False"):
        found.append("failed_when")
    return found


def test_softened_detects_quiet_failures() -> None:
    assert _softened({"ignore_errors": True}) == ["ignore_errors"]
    assert _softened({"failed_when": False}) == ["failed_when"]
    assert _softened({"failed_when": "not x.stat.exists"}) == []
    assert _softened({"name": "plain"}) == []


def test_staging_users_are_the_expected_roles() -> None:
    users = {_rel(p) for p in _stage_users()}
    expected = {f"ansible/roles/{rel}" for rel, _ in STAGED_UNARCHIVES} | {
        "ansible/roles/opensearch/tasks/configure.yml",
        "ansible/roles/runtime-install/tasks/main.yml",
        "ansible/roles/component-install/tasks/main.yml",
    }
    assert users == expected


@pytest.mark.parametrize("path", _stage_users(), ids=_rel)
def test_staging_dir_is_created_before_first_use(path: Path) -> None:
    tasks = _flatten(_load(path))
    include = [i for i, t in enumerate(tasks) if _includes_stage_dir(t)]
    assert include, f"{_rel(path)} stages under lab_stage_dir without including {STAGE_INCLUDE}"
    users = [i for i, t in enumerate(tasks) if _touches_stage(t)]
    assert users, f"{_rel(path)} names lab_stage_dir but no task acts on it"
    assert include[0] < users[0], f"{_rel(path)}: a task uses lab_stage_dir before it is created"


@pytest.mark.parametrize("path", _stage_users(), ids=_rel)
def test_staging_tasks_fail_loudly(path: Path) -> None:
    for task in _flatten(_load(path)):
        if _touches_stage(task):
            softened = _softened(task)
            assert softened == [], f"{_rel(path)}: {task.get('name')!r} softens with {softened}"


def test_stage_dir_task_asserts_and_creates_root_owned_dir() -> None:
    check, mkdir = _load(STAGE_DIR_TASKS)
    assert check["ansible.builtin.assert"]["that"] == [
        "lab_stage_dir is defined",
        "lab_stage_dir is match('/')",
        "lab_stage_dir is not match('/tmp(/|$)')",
    ]
    assert mkdir["ansible.builtin.file"] == {
        "path": STAGE,
        "state": "directory",
        "owner": "root",
        "group": "root",
        "mode": "0755",
    }
    assert mkdir["become"] is True
    for task in (check, mkdir):
        assert _softened(task) == []
        assert "when" not in task


# --- the variable, its Python twin, and Ansible's remote temp ------------------------


def test_lab_stage_dir_is_on_disk() -> None:
    value = _load(STAGING_VARS)["lab_stage_dir"]
    assert value == "/var/tmp/lab-staging"
    assert value == logsearch.GUEST_STAGE_DIR, "mqlab's guest staging must match lab_stage_dir"


def test_ansible_remote_tmp_is_pinned_on_disk() -> None:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(ANSIBLE / "ansible.cfg", encoding="utf-8")
    assert cfg.get("defaults", "remote_tmp") == "~/.ansible/tmp"


def test_nothing_else_sets_remote_tmp() -> None:
    setters = []
    for root in (ANSIBLE, REPO_ROOT / "lab", REPO_ROOT / "src" / "mqlab"):
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if path == ANSIBLE / "ansible.cfg" or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"remote_tmp|REMOTE_TMP", text):
                setters.append(_rel(path))
    assert setters == [], f"only ansible/ansible.cfg may set Ansible's remote temp: {setters}"
