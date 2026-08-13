"""Readiness-budget guards for the logsearch tier (#1040, epic .github#198).

#1034 widened only the OpenSearch readiness wait to ~15 min (180 retries x 5 s).
The cold-boot rebuild (#1022) then got past OpenSearch and failed at the NEXT
logsearch-tier gate — Data Prepper's readiness wait (old 180 s) — because Data
Prepper (a JVM) and OpenSearch Dashboards (the last, most-starved service in the
sequence) also start slowly under macOS host oversubscription.

#1040 widens the REMAINING logsearch-tier readiness/start budgets to ~15 min
(900 s) so a host-contended cold boot reaches a fully-up tier one-pass, while
keeping every wait FAIL-LOUD (the plays still abort if a component never comes
up). These guards pin the widened budgets so a regression to the old 180 s / 300 s
values is caught at `vrg-validate` time instead of at the slow, expensive
cold-rebuild acceptance gate (#1022).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES = REPO_ROOT / "ansible" / "roles"

DATA_PREPPER_CONFIGURE = ROLES / "data-prepper" / "tasks" / "configure.yml"
DATA_PREPPER_INSTALL = ROLES / "data-prepper" / "tasks" / "install.yml"
DASHBOARDS_CONFIGURE = ROLES / "opensearch-dashboards" / "tasks" / "configure.yml"
DASHBOARDS_INSTALL = ROLES / "opensearch-dashboards" / "tasks" / "install.yml"

# The ~15-min budget the whole logsearch tier is aligned on (#1034/#1040).
BUDGET_SECONDS = 900


def _load_tasks(path: Path) -> list[dict]:
    """Parse an Ansible tasks file into a list of task dicts (fail loud if empty)."""
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks, f"{path} did not parse to a non-empty task list"
    return tasks


def _unit_content(path: Path, dest: str) -> str:
    """Return the inline systemd-unit `content:` block a copy task drops at `dest`."""
    for task in _load_tasks(path):
        copy = task.get("ansible.builtin.copy", {})
        if isinstance(copy, dict) and copy.get("dest") == dest:
            return copy["content"]
    raise AssertionError(
        f"{path} has no copy task rendering {dest} — has the systemd unit moved? (#1040)"
    )


# --- Data Prepper -----------------------------------------------------------------------


def test_data_prepper_readiness_wait_budget_is_15_minutes() -> None:
    """The Data Prepper otel_logs_source port wait must budget 900 s (#1040): the old
    180 s timed out on the #1022 host-contended cold boot while the JVM was still
    binding :21892. Must stay fail-loud (a bounded wait_for that aborts on timeout)."""
    tasks = _load_tasks(DATA_PREPPER_CONFIGURE)
    wait = next(
        (t for t in tasks if "ansible.builtin.wait_for" in t),
        None,
    )
    assert wait is not None, (
        "data-prepper configure.yml has no ansible.builtin.wait_for readiness task (#1040)"
    )
    timeout = wait["ansible.builtin.wait_for"].get("timeout")
    assert timeout == BUDGET_SECONDS, (
        "data-prepper readiness wait_for timeout must be widened to "
        f"{BUDGET_SECONDS} s for host-contended cold boots (#1040); got {timeout!r}"
    )


def test_data_prepper_unit_start_timeout_is_15_minutes() -> None:
    """The Data Prepper systemd unit must give the JVM 900 s to start (#1040), matching
    the readiness wait — and must NOT retain the old 180 s TimeoutStartSec."""
    content = _unit_content(DATA_PREPPER_INSTALL, "/etc/systemd/system/data-prepper.service")
    assert "TimeoutStartSec=900" in content, (
        "data-prepper systemd unit must set TimeoutStartSec=900 for host-contended cold "
        f"boots (#1040); unit content:\n{content}"
    )
    assert "TimeoutStartSec=180" not in content, (
        "data-prepper systemd unit still carries the old TimeoutStartSec=180 — the #1040 "
        f"widening did not take; unit content:\n{content}"
    )


# --- OpenSearch Dashboards --------------------------------------------------------------


def test_dashboards_readiness_wait_budget_is_15_minutes() -> None:
    """The Dashboards /api/status readiness loop must budget ~900 s (180 retries x 5 s
    delay) (#1040): Dashboards starts last and is the most starved under host
    oversubscription. Must stay fail-loud (a bounded retries/until that aborts)."""
    tasks = _load_tasks(DASHBOARDS_CONFIGURE)
    wait = next(
        (t for t in tasks if "until" in t and "ansible.builtin.uri" in t),
        None,
    )
    assert wait is not None, (
        "opensearch-dashboards configure.yml has no uri/until readiness task (#1040)"
    )
    assert wait.get("retries") == 180, (
        "dashboards readiness retries must be widened to 180 (x 5 s = 900 s) for "
        f"host-contended cold boots (#1040); got {wait.get('retries')!r}"
    )
    assert wait.get("delay") == 5, (
        f"dashboards readiness delay must remain 5 s (180 x 5 = 900 s) (#1040); "
        f"got {wait.get('delay')!r}"
    )


def test_dashboards_unit_start_timeout_is_15_minutes() -> None:
    """The Dashboards systemd unit must give it 900 s to start (#1040), matching the
    readiness loop — and must NOT retain the old 180 s TimeoutStartSec."""
    content = _unit_content(DASHBOARDS_INSTALL, "/etc/systemd/system/opensearch-dashboards.service")
    assert "TimeoutStartSec=900" in content, (
        "opensearch-dashboards systemd unit must set TimeoutStartSec=900 for "
        f"host-contended cold boots (#1040); unit content:\n{content}"
    )
    assert "TimeoutStartSec=180" not in content, (
        "opensearch-dashboards systemd unit still carries the old TimeoutStartSec=180 — "
        f"the #1040 widening did not take; unit content:\n{content}"
    )
