"""Pin rdqm-install's DRBD kmod choice to IBM's ``modver`` (#1408).

The role used to glob ``kmod-drbd-*<release>-*.rpm`` for the EXACT running kernel
release, which refused kernels IBM supports by family (the RHEL 9.8 GA kernel, #1396).
It now asks the ``modver`` helper shipped in the MQ media, as IBM's 10.0 RDQM install
doc directs, and fails loudly when modver errors or answers with anything unusable.

These checks pin that contract statically and run the role's own conditions (through
Ansible's Templar) against real ``modver -m`` output captured in
``tests/fixtures/rdqm_modver_m.yaml``:

- the selection task runs modver in query mode for ``ansible_kernel``, with no
  error-softening, and the old exact-release glob is gone;
- the parse accepts every captured supported answer and rejects malformed ones;
- the chosen rpm is what step 1 of the install hands to dnf;
- the known-issue warning fires exactly for kernels inside IBM's listed ranges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = Path(__file__).resolve().parents[1]
ROLE = REPO_ROOT / "ansible" / "roles" / "rdqm-install"
INSTALL = ROLE / "tasks" / "install.yml"
RHEL9_VARS = ROLE / "vars" / "RedHat-9.yml"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "rdqm_modver_m.yaml"

MODVER = "ask IBM's modver which drbd kmod fits the running kernel (loud fail)"
PARSE = "modver named one kmod rpm that this MQ level supports (loud fail)"
RECORD = "record the kmod modver chose (relative to the unpacked media) and the kernel release"
STAT = "stat the kmod rpm modver chose"
EXISTS = "the kmod modver chose ships in the unpacked media (loud fail)"
WARN = "WARN if the running kernel is in IBM's known-compatibility-issue list (non-fatal)"
STEP1 = "install MQ + cluster prereqs (step 1 of 2)"
UNPACK = "unpack"

# Keys that would let a failing task pass quietly.
SOFTENERS = ("ignore_errors", "failed_when", "rescue", "until")


def _tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = yaml.safe_load(INSTALL.read_text(encoding="utf-8"))
    return tasks


def _task(name: str) -> dict[str, Any]:
    (task,) = [t for t in _tasks() if t.get("name") == name]
    return task


def _fixture() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    return data


def _rhel9_vars() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(RHEL9_VARS.read_text(encoding="utf-8"))
    return data


def _templar(variables: dict[str, Any]) -> Templar:
    return Templar(loader=DataLoader(), variables=variables)


def _true(condition: str, variables: dict[str, Any]) -> bool:
    return _templar(variables).evaluate_conditional(trust_as_template(condition))


def _render(template: str, variables: dict[str, Any]) -> Any:
    return _templar(variables).template(trust_as_template(template))


def _first_failing(conditions: list[str], variables: dict[str, Any]) -> str | None:
    """Mimic ``assert``: evaluate in order, stop at the first false condition."""
    return next((c for c in conditions if not _true(c, variables)), None)


def _parse_vars(stdout_lines: list[str], mq_version: str = "10.0.0.0") -> dict[str, Any]:
    return {"kmod_modver": {"stdout_lines": stdout_lines}, "mq_version": mq_version}


def _media() -> str:
    """The guest dir install.yml unpacks the MQ tar into: modver must run from there."""
    dest: str = _task(UNPACK)["ansible.builtin.unarchive"]["dest"]
    return f"{dest}/MQServer"


SUPPORTED = [c for c in _fixture()["cases"] if c["rc"] == 0]
UNSUPPORTED = [c for c in _fixture()["cases"] if c["rc"] != 0]


# --- the selection task: IBM's modver, query mode, no fallback --------------------------


def test_exact_release_glob_is_gone() -> None:
    text = INSTALL.read_text(encoding="utf-8")
    assert "kmod-drbd-*" not in text
    assert "GLOB" not in text
    assert "uname -r" not in text
    assert "kmod_pick" not in text


def test_modver_runs_in_query_mode_for_the_running_kernel() -> None:
    task = _task(MODVER)
    assert task["ansible.builtin.command"] == {
        "argv": [
            f"{_media()}/Advanced/RDQM/PreReqs/{{{{ drbd_kmod_dir }}}}/modver",
            "-m",
            "{{ ansible_kernel }}",
        ]
    }
    assert task["register"] == "kmod_modver"
    assert task["changed_when"] is False


def test_modver_is_never_asked_to_install() -> None:
    """``-i`` would dnf-install the kmod alone, outside the two-step pre-QM ordering."""
    callers = [t["name"] for t in _tasks() if "/modver" in yaml.safe_dump(t)]
    assert callers == [MODVER]
    assert "-i" not in _task(MODVER)["ansible.builtin.command"]["argv"]


@pytest.mark.parametrize("name", [MODVER, PARSE, RECORD, STAT, EXISTS])
def test_selection_tasks_fail_loudly(name: str) -> None:
    task = _task(name)
    softened = [key for key in SOFTENERS if key in task]
    assert softened == [], f"{name}: {softened}"
    assert "when" not in task, f"{name} must run unconditionally"


def test_selection_runs_in_order_before_the_install() -> None:
    names = [t.get("name") for t in _tasks()]
    order = [names.index(n) for n in (MODVER, PARSE, RECORD, STAT, EXISTS, WARN, STEP1)]
    assert order == sorted(order)


def test_step1_installs_the_rpm_modver_chose() -> None:
    script = _task(STEP1)["ansible.builtin.shell"]
    assert script.rstrip().endswith("{{ rdqm_kmod_rpm }}")


def test_exists_check_names_the_stat() -> None:
    task = _task(EXISTS)["ansible.builtin.assert"]
    assert task["that"] == ["kmod_rpm_stat.stat.exists"]
    assert _true(task["that"][0], {"kmod_rpm_stat": {"stat": {"exists": True}}})
    assert not _true(task["that"][0], {"kmod_rpm_stat": {"stat": {"exists": False}}})
    assert _task(STAT)["ansible.builtin.stat"]["path"] == f"{_media()}/{{{{ rdqm_kmod_rpm }}}}"
    assert _task(STAT)["register"] == "kmod_rpm_stat"


# --- the parse, against real modver -m output -------------------------------------------


@pytest.mark.parametrize("case", SUPPORTED, ids=[c["kernel"] for c in SUPPORTED])
def test_parse_accepts_captured_modver_answers(case: dict[str, Any]) -> None:
    fixture = _fixture()
    conditions = _task(PARSE)["ansible.builtin.assert"]["that"]
    variables = _parse_vars(case["stdout"], fixture["mq_version"])
    assert _first_failing(conditions, variables) is None

    record = _task(RECORD)["ansible.builtin.set_fact"]
    facts = {
        **variables,
        "ansible_kernel": case["kernel"],
        "drbd_kmod_dir": fixture["kmod_dir"],
        "rhel_el_tag": "el9",
    }
    assert _render(record["rdqm_kmod_rpm"], facts) == (
        f"Advanced/RDQM/PreReqs/{fixture['kmod_dir']}/{case['stdout'][0]}"
    )
    assert _render(record["rdqm_kernel_release"], facts) == case["kernel"].split(".el9")[0]


@pytest.mark.parametrize("case", UNSUPPORTED, ids=[c["kernel"] for c in UNSUPPORTED])
def test_unsupported_kernel_fails_twice_over(case: dict[str, Any]) -> None:
    """modver exits non-zero (the command task fails), and its text would fail the parse."""
    assert case["rc"] != 0
    conditions = _task(PARSE)["ansible.builtin.assert"]["that"]
    assert _first_failing(conditions, _parse_vars(case["stdout"])) is not None


KMOD = "kmod-drbd-9.3.1_5.14.0_570.12.1-1.x86_64.rpm"


@pytest.mark.parametrize(
    ("stdout_lines", "mq_version", "failing"),
    [
        ([], "10.0.0.0", "length"),
        ([KMOD], "10.0.0.0", "length"),
        ([KMOD, "10.0.0.0", "extra"], "10.0.0.0", "length"),
        (["../evil/" + KMOD, "10.0.0.0"], "10.0.0.0", "kmod-drbd"),
        (["kmod-drbd-9.3.1_5.14.0_570.12.1-1.x86_64.rpm.bak", "10.0.0.0"], "10.0.0.0", "kmod-drbd"),
        (["Kernel module 'x' is compatible", "10.0.0.0"], "10.0.0.0", "kmod-drbd"),
        ([KMOD, "10.0"], "10.0.0.0", "{3}"),
        ([KMOD, "10.0.0.5"], "10.0.0.0", "version"),
    ],
    ids=[
        "no-output",
        "no-min-level",
        "extra-line",
        "path-not-a-file-name",
        "not-an-rpm",
        "verbose-text",
        "short-level",
        "needs-newer-mq",
    ],
)
def test_parse_rejects_malformed_answers(
    stdout_lines: list[str], mq_version: str, failing: str
) -> None:
    conditions = _task(PARSE)["ansible.builtin.assert"]["that"]
    first = _first_failing(conditions, _parse_vars(stdout_lines, mq_version))
    assert first is not None
    assert failing in first


def test_parse_accepts_an_older_minimum_level() -> None:
    conditions = _task(PARSE)["ansible.builtin.assert"]["that"]
    assert _first_failing(conditions, _parse_vars([KMOD, "10.0.0.0"], "10.0.0.5")) is None


def test_parse_failure_message_renders() -> None:
    msg = _task(PARSE)["ansible.builtin.assert"]["fail_msg"]
    variables = {**_parse_vars(["Unsupported kernel release."]), "ansible_kernel": "6.12.0"}
    rendered = _render(msg, variables)
    assert "Unsupported kernel release." in rendered
    assert "6.12.0" in rendered


# --- IBM's known-issue kernels: a visible WARNING, never a failure ----------------------


def _warn_vars(kernel_release: str) -> dict[str, Any]:
    return {"rdqm_kernel_release": kernel_release, **_rhel9_vars()}


def _warned(kernel_release: str) -> bool:
    """True when the WARN task fires for any of IBM's listed ranges."""
    conditions = _task(WARN)["when"]
    variables = _warn_vars(kernel_release)
    return any(
        _first_failing(conditions, {**variables, "item": item}) is None
        for item in variables["rdqm_kernel_known_issues"]
    )


@pytest.mark.parametrize("release", ["5.14.0-570.12.1", "5.14.0-570.13.1", "5.14.0-570.15.1"])
def test_known_issue_kernel_warns(release: str) -> None:
    assert _warned(release)


@pytest.mark.parametrize(
    "release",
    ["5.14.0-570.11.9", "5.14.0-570.16.1", "5.14.0-570.120.1", "5.14.0-687.5.3", "5.14.0-503.11.1"],
)
def test_kernel_outside_the_ranges_does_not_warn(release: str) -> None:
    assert not _warned(release)


def test_warning_is_non_fatal_and_loops_the_vendored_ranges() -> None:
    task = _task(WARN)
    assert set(task) == {"name", "ansible.builtin.debug", "loop", "loop_control", "when"}
    assert task["loop"] == "{{ rdqm_kernel_known_issues }}"


def test_warning_message_renders_with_its_source() -> None:
    task = _task(WARN)
    variables = {
        **_warn_vars("5.14.0-570.12.1"),
        "ansible_kernel": "5.14.0-570.12.1.el9_6.x86_64",
        "kmod_modver": {"stdout_lines": [KMOD, "10.0.0.0"]},
        "item": _rhel9_vars()["rdqm_kernel_known_issues"][0],
    }
    rendered = _render(task["ansible.builtin.debug"]["msg"], variables)
    assert rendered.startswith("WARNING: kernel 5.14.0-570.12.1.el9_6.x86_64 ")
    assert "5.14.0-570.12.1 -> 5.14.0-570.15.1" in rendered
    assert rendered.endswith(f"Installing modver's choice {KMOD} anyway.")
    assert _render(task["loop_control"]["label"], variables) == (
        "5.14.0-570.12.1 -> 5.14.0-570.15.1"
    )


def test_vendored_known_issue_list_is_sourced_and_ordered() -> None:
    data = _rhel9_vars()
    source = urlparse(data["rdqm_kernel_known_issues_source"])
    assert (source.scheme, source.netloc, source.path) == (
        "https",
        "www.ibm.com",
        "/support/pages/node/1087143",
    )
    assert data["rdqm_kernel_known_issues_as_of"] == "2026-10-09"
    ranges = data["rdqm_kernel_known_issues"]
    assert ranges == [{"first": "5.14.0-570.12.1", "last": "5.14.0-570.15.1"}]
