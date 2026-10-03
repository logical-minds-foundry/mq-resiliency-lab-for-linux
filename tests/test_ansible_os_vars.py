"""Guard the per-OS-version vars indirection (#1277, epic .github#280 §4.8).

Version-specific Ansible values live in ``roles/<role>/vars/<Distribution>-<major>.yml``
and are loaded through ONE shared include, ``ansible/tasks/os-vars.yml``, which FAILS
when no file matches: an OS the role was never taught must not half-provision on a
silent family default. These static checks pin that contract:

- the include uses ``first_found`` with no ``skip`` / ``errors: ignore`` escape hatch;
- every role that calls the include names ITSELF (``os_vars_role``), and is listed here;
- each such role ships a vars file for every catalog OS that runs it, DERIVED from
  ``lab/versions.yaml`` (via the catalog loader) — so adding ``rhel: 10`` to a stack's
  supported list fails here until the role grows ``RedHat-10.yml``;
- the RHEL install bodies carry no hand-written ``el9`` / ``9.6`` literal;
- the rendered values are byte-for-byte what the literals were on RHEL 9.6.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml

from mqlab.versions import load_catalog

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
OS_VARS = ANSIBLE / "tasks" / "os-vars.yml"
INCLUDE_PATH = "../../../tasks/os-vars.yml"

# catalog family -> Ansible's `ansible_distribution` fact (the vars-file name prefix).
DISTRIBUTION = {"rhel": "RedHat", "ubuntu": "Ubuntu"}

# Ansible role -> the catalog stacks whose OSes run its os-vars include. Only the code
# path that CONSUMES the per-version vars counts: mq-nativeha includes os-vars from
# install-RedHat.yml alone, so it maps to the RHEL stack, never nativeha-ubuntu.
# mq-nativeha-spike runs on the reused rdqm-* RHEL slots.
ROLE_STACKS = {
    "rdqm-install": ("rdqm-rhel",),
    "mq-nativeha": ("nativeha-rhel-crr",),
    "mq-nativeha-spike": ("rdqm-rhel",),
}

# The RHEL install bodies whose hand-written version literals moved into vars.
RHEL_INSTALL_BODIES = {
    "rdqm-install": ANSIBLE / "roles/rdqm-install/tasks/install.yml",
    "mq-nativeha": ANSIBLE / "roles/mq-nativeha/tasks/install-RedHat.yml",
    "mq-nativeha-spike": ANSIBLE / "roles/mq-nativeha-spike/tasks/main.yml",
}

# RHEL 9.6 facts as the guest reports them (ansible_distribution_version is the point).
RHEL96_FACTS = {
    "ansible_distribution": "RedHat",
    "ansible_distribution_major_version": "9",
    "ansible_distribution_version": "9.6",
}

# The pre-#1277 literals, verbatim — the rendered output must reproduce them exactly.
DVD_REPO_BEFORE = """\
[dvd-baseos]
name=RHEL 9.6 DVD BaseOS
baseurl=file:///media/rhel/BaseOS
enabled=1
gpgcheck=0
[dvd-appstream]
name=RHEL 9.6 DVD AppStream
baseurl=file:///media/rhel/AppStream
enabled=1
gpgcheck=0
"""
KMOD_PICK_BEFORE = """\
set -e
KREL=$(uname -r)
BASE=${KREL%%.el9*}
GLOB="/tmp/MQServer/Advanced/RDQM/PreReqs/el9/kmod-drbd-9/kmod-drbd-*${BASE//-/_}-*.rpm"
KMOD=$(ls $GLOB 2>/dev/null || true)
if [ -z "$KMOD" ]; then
  echo "ERROR: no kmod-drbd for kernel $KREL; shipped kmods:" >&2
  ls /tmp/MQServer/Advanced/RDQM/PreReqs/el9/kmod-drbd-9/ >&2
  exit 1
fi
echo "$KMOD"
"""
KMOD_RPM = "kmod-drbd-picked-by-the-previous-task.rpm"
PREREQS_BEFORE = f"""\
set -e
cd /tmp/MQServer
dnf install -y \\
  MQSeriesRuntime-*.rpm MQSeriesServer-*.rpm MQSeriesGSKit-*.rpm \\
  MQSeriesJava-*.rpm MQSeriesJRE-*.rpm MQSeriesWeb-*.rpm \\
  MQSeriesSDK-*.rpm MQSeriesClient-*.rpm MQSeriesSamples-*.rpm \\
  Advanced/RDQM/PreReqs/el9/pacemaker-2/*.rpm \\
  Advanced/RDQM/PreReqs/el9/drbd-utils-9/*.rpm \\
  {KMOD_RPM}
"""


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _iter_tasks(node: Any) -> Any:
    """Yield every task/block dict in a parsed task file, descending into blocks."""
    if isinstance(node, list):
        for item in node:
            yield from _iter_tasks(item)
    elif isinstance(node, dict):
        yield node
        for key in ("tasks", "pre_tasks", "post_tasks", "handlers", "block", "rescue", "always"):
            if key in node:
                yield from _iter_tasks(node[key])


def _os_vars_includes() -> list[tuple[str, Path, dict[str, Any]]]:
    """Every (role, task file, include task) that calls the shared os-vars include."""
    found = []
    for path in sorted((ANSIBLE / "roles").glob("*/tasks/*.yml")):
        for task in _iter_tasks(_load(path)):
            if task.get("ansible.builtin.include_tasks") == INCLUDE_PATH:
                found.append((path.parts[-3], path, task))
    return found


def _named(path: Path, name: str) -> dict[str, Any]:
    tasks = [t for t in _iter_tasks(_load(path)) if t.get("name") == name]
    assert len(tasks) == 1, f"{path}: expected exactly one task named {name!r}"
    return tasks[0]


def _render(template: str, context: dict[str, Any]) -> str:
    env = jinja2.Environment(  # noqa: S701 — renders shell/ini text, never HTML
        undefined=jinja2.StrictUndefined, keep_trailing_newline=True
    )
    return env.from_string(template).render(context)


def _rhel96_context(role: str) -> dict[str, Any]:
    """Facts + the role's RedHat-9 vars, rendered the way Ansible resolves them lazily."""
    raw = _load(ANSIBLE / "roles" / role / "vars" / "RedHat-9.yml")
    return {**RHEL96_FACTS, **{k: _render(v, RHEL96_FACTS) for k, v in raw.items()}}


def test_os_vars_include_fails_loudly_without_a_match() -> None:
    """first_found with no skip / ignore: a missing vars file must FAIL, not fall back."""
    text = OS_VARS.read_text(encoding="utf-8")
    assert "first_found" in text
    assert "skip" not in text and "ignore" not in text, "os-vars.yml must not soften a miss"
    (task,) = _load(OS_VARS)
    assert "ignore_errors" not in task and "failed_when" not in task
    assert "default(" not in task["ansible.builtin.include_vars"]["file"]
    params = task["vars"]["params"]
    assert set(params) == {"files", "paths"}, f"no extra first_found options: {params}"
    assert params["files"] == [
        "{{ ansible_distribution }}-{{ ansible_distribution_major_version }}.yml"
    ]
    assert params["paths"] == ["{{ playbook_dir }}/roles/{{ os_vars_role }}/vars"]


def test_every_os_vars_caller_names_itself_and_is_listed() -> None:
    """A caller loading ANOTHER role's vars, or an unlisted caller, escapes the file check."""
    callers = _os_vars_includes()
    assert callers, "no role includes tasks/os-vars.yml"
    for role, path, task in callers:
        assert task.get("vars", {}).get("os_vars_role") == role, f"{path}: os_vars_role != {role}"
    assert {role for role, _, _ in callers} == set(ROLE_STACKS)


@pytest.mark.parametrize("role", sorted(ROLE_STACKS))
def test_role_ships_vars_for_each_catalog_os(role: str) -> None:
    """Derived from lab/versions.yaml: every OS a role's stacks support needs its file."""
    catalog = load_catalog()
    refs = {ref for stack in ROLE_STACKS[role] for ref in catalog.stacks[stack]["supported"]}
    assert refs, f"{role}: its stacks support no OS"
    vars_dir = ANSIBLE / "roles" / role / "vars"
    missing = [
        f"{DISTRIBUTION[r.family]}-{r.major}.yml"
        for r in sorted(refs)
        if not (vars_dir / f"{DISTRIBUTION[r.family]}-{r.major}.yml").is_file()
    ]
    assert missing == [], f"{role} has no per-OS vars for: {missing}"


@pytest.mark.parametrize("role", sorted(RHEL_INSTALL_BODIES))
def test_rhel_install_body_carries_no_version_literal(role: str) -> None:
    text = RHEL_INSTALL_BODIES[role].read_text(encoding="utf-8")
    assert not re.search(r"el9|9\.6", text), f"{role}: version literal left in the install body"


@pytest.mark.parametrize("role", sorted(RHEL_INSTALL_BODIES))
def test_dvd_repo_renders_identically_on_rhel96(role: str) -> None:
    task = _named(RHEL_INSTALL_BODIES[role], "dnf repos from the DVD")
    content = task["ansible.builtin.copy"]["content"]
    assert _render(content, _rhel96_context(role)) == DVD_REPO_BEFORE


def test_rdqm_prereq_paths_render_identically_on_rhel96() -> None:
    body = RHEL_INSTALL_BODIES["rdqm-install"]
    ctx = {**_rhel96_context("rdqm-install"), "kmod_pick": {"stdout": f"{KMOD_RPM}\n"}}
    kmod = _named(body, "select the drbd kmod matching the running kernel (loud fail)")
    assert _render(kmod["ansible.builtin.shell"], ctx) == KMOD_PICK_BEFORE
    prereqs = _named(body, "install MQ + cluster prereqs (step 1 of 2)")
    assert _render(prereqs["ansible.builtin.shell"], ctx) == PREREQS_BEFORE
