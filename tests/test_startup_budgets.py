"""Startup-budget guardrails for the JVM-heavy bring-up tier (#1162, epic .github#249).

The nested-virt JVM cold-start tax (spec 4.3) means a heavy service can legitimately
take many minutes to start under macOS host oversubscription — so its systemd
`TimeoutStartSec` and paired Ansible readiness wait must be **generous-but-bounded**
(never `infinity`, never the silent 90 s systemd default, never a stale tight value),
and its fatality must match data-path dependence (fail-loud where the data path
depends, non-fatal-with-warn where it does not).

These guards pin the reconciled budgets (see
`docs/reports/2026-09-20-startup-budget-inventory.md`) so a future edit cannot quietly
drift them at `vrg-validate` time instead of at the slow cold-rebuild acceptance gate.
They cover the gap `test_logsearch_budgets.py` (Dashboards + Data Prepper) and
`test_opensearch_render.py` (the OpenSearch `retries`/`delay`) leave: the **mqweb** unit
(1800 s) and its non-fatal gate (300 s), and the **OpenSearch** unit (reconciled
180 s -> 900 s, #1162) and its fatal gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ROLES = REPO_ROOT / "ansible" / "roles"

MQWEB_UNIT = ROLES / "mqweb" / "templates" / "mqweb.service.j2"
MQWEB_TASKS = ROLES / "mqweb" / "tasks" / "main.yml"
OPENSEARCH_INSTALL = ROLES / "opensearch" / "tasks" / "install.yml"
OPENSEARCH_CONFIGURE = ROLES / "opensearch" / "tasks" / "configure.yml"

# Bounded window for a JVM-heavy service's systemd start budget: wide enough to clear
# the ~7x nested-virt cold-start tax with headroom (spec 4.3), bounded so a drift to the
# 90 s systemd default, the old OpenSearch 180 s, or `TimeoutStartSec=infinity` is caught.
MIN_JVM_TIMEOUT = 300
MAX_JVM_TIMEOUT = 3600

# The ~15-min budget the logsearch tier is aligned on (#1034/#1040); OpenSearch's unit
# is reconciled to it here (#1162). mqweb's own bounded non-fatal readiness gate.
LOGSEARCH_BUDGET_SECONDS = 900
MQWEB_READINESS_TIMEOUT = 300


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
    raise AssertionError(f"{path} has no copy task rendering {dest} — has the unit moved? (#1162)")


def _timeout_start_sec(unit_text: str) -> int:
    """Extract the numeric `TimeoutStartSec=<n>` from a systemd unit body.

    A missing directive (silent 90 s default) or a non-numeric value (`infinity`) has no
    `\\d+` to match, so the guard fails loud rather than treating an unbounded budget as
    if it were present.
    """
    match = re.search(r"^\s*TimeoutStartSec=(\d+)\s*$", unit_text, re.MULTILINE)
    assert match is not None, (
        "unit must declare a bounded numeric TimeoutStartSec "
        f"(no unset default, no infinity); unit body:\n{unit_text}"
    )
    return int(match.group(1))


# --- mqweb / Liberty --------------------------------------------------------------------


def test_mqweb_unit_start_timeout_is_bounded_and_generous() -> None:
    """The Liberty JVM unit must give a bounded-but-generous start budget (#1151/#1162):
    within the JVM window and at least as long as its own readiness gate."""
    timeout = _timeout_start_sec(MQWEB_UNIT.read_text(encoding="utf-8"))
    assert MIN_JVM_TIMEOUT <= timeout <= MAX_JVM_TIMEOUT, (
        f"mqweb TimeoutStartSec must be in [{MIN_JVM_TIMEOUT}, {MAX_JVM_TIMEOUT}] s "
        f"(bounded, no 90 s default / no infinity); got {timeout}"
    )
    assert timeout >= MQWEB_READINESS_TIMEOUT, (
        f"mqweb unit budget ({timeout} s) must be >= its readiness gate "
        f"({MQWEB_READINESS_TIMEOUT} s) so the start job is not reaped before the gate ends"
    )


def test_mqweb_readiness_gate_is_bounded_and_non_fatal() -> None:
    """The mqweb REST/console readiness gate is bounded at 300 s and **non-fatal**
    (`failed_when: false`): the message path does not depend on mqweb, so a slow console
    warns and proceeds rather than aborting provision (spec 4.2 / 4.3)."""
    wait = next(
        (
            t
            for t in _load_tasks(MQWEB_TASKS)
            if t.get("ansible.builtin.wait_for", {}).get("port") == 9443
        ),
        None,
    )
    assert wait is not None, "mqweb main.yml has no wait_for :9443 readiness task (#1162)"
    timeout = wait["ansible.builtin.wait_for"].get("timeout")
    assert timeout == MQWEB_READINESS_TIMEOUT, (
        f"mqweb readiness wait_for timeout must be {MQWEB_READINESS_TIMEOUT} s; got {timeout!r}"
    )
    assert wait.get("failed_when") is False, (
        "mqweb readiness gate must stay NON-fatal (failed_when: false) — the message path "
        f"does not depend on the REST/console; got failed_when={wait.get('failed_when')!r}"
    )


# --- OpenSearch -------------------------------------------------------------------------


def test_opensearch_unit_start_timeout_matches_readiness_budget() -> None:
    """The OpenSearch unit budget is reconciled (180 -> 900, #1162) to equal its real
    900 s readiness gate: bounded, in the JVM window, and coherent with the effective
    budget (spec 4.3 note 2). Catches a regression to the old 180 s."""
    content = _unit_content(OPENSEARCH_INSTALL, "/etc/systemd/system/opensearch.service")
    timeout = _timeout_start_sec(content)
    assert MIN_JVM_TIMEOUT <= timeout <= MAX_JVM_TIMEOUT, (
        f"opensearch TimeoutStartSec must be in [{MIN_JVM_TIMEOUT}, {MAX_JVM_TIMEOUT}] s "
        f"(no 180 s / 90 s / infinity); got {timeout}"
    )
    assert timeout == LOGSEARCH_BUDGET_SECONDS, (
        f"opensearch unit budget must be reconciled to the {LOGSEARCH_BUDGET_SECONDS} s "
        f"readiness gate (#1162); got {timeout}"
    )


def test_opensearch_readiness_gate_is_bounded_and_fatal() -> None:
    """The OpenSearch cluster-health gate budgets 900 s (180 x 5) and is **fatal**: the
    log-ingestion data path depends on the store, so a never-ready tier aborts the play
    (no `failed_when: false`) rather than limping forward (spec 4.3 / #1040)."""
    wait = next(
        (
            t
            for t in _load_tasks(OPENSEARCH_CONFIGURE)
            if "until" in t and "ansible.builtin.uri" in t
        ),
        None,
    )
    assert wait is not None, "opensearch configure.yml has no uri/until readiness task (#1162)"
    budget = wait.get("retries", 0) * wait.get("delay", 0)
    assert budget == LOGSEARCH_BUDGET_SECONDS, (
        f"opensearch readiness budget (retries x delay) must be {LOGSEARCH_BUDGET_SECONDS} s; "
        f"got {wait.get('retries')!r} x {wait.get('delay')!r} = {budget}"
    )
    assert wait.get("failed_when") is not False, (
        "opensearch readiness gate must stay FATAL (no failed_when: false) — the data path "
        "depends on the store; a never-ready tier must abort, not warn-and-proceed"
    )
