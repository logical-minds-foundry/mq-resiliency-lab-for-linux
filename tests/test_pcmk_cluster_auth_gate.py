"""Guards for the pcmk-cluster auth+setup flake fix (#916).

A cold `pcmk-ubuntu` provision failed transiently at the DR-site (`mqpcmk-b`) run-once
`pcs host auth` + `pcs cluster setup` task; a plain `--from provision` retry recovered.
The task was `no_log` (it carries the hacluster password), so the failure was opaque.

The fix: a bounded pcsd readiness gate on every site node's heartbeat address before
auth; a bounded retry of the (idempotent) auth+setup; the password passed via
`environment:` instead of the command line; and a rescue that fails loud with pcs's rc
and output, password redacted. These guards pin that shape so a regression is caught at
`vrg-validate` time rather than at the slow cold-rebuild acceptance gate.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PCMK_TASKS = REPO_ROOT / "ansible" / "roles" / "pcmk-cluster" / "tasks" / "main.yml"


def _tasks() -> list[dict]:
    tasks = yaml.safe_load(PCMK_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list) and tasks, f"{PCMK_TASKS} did not parse to a task list"
    return tasks


def _index_of(tasks: list[dict], name_substr: str) -> int:
    for i, t in enumerate(tasks):
        if name_substr in (t.get("name") or ""):
            return i
    raise AssertionError(f"no pcmk-cluster task whose name contains {name_substr!r} (#916)")


def _auth_block() -> dict:
    tasks = _tasks()
    return tasks[_index_of(tasks, "auth + cluster setup")]


def _auth_task() -> dict:
    block = _auth_block()
    assert "block" in block, "auth + cluster setup must be a block with a rescue (#916)"
    (task,) = block["block"]
    return task


def test_pcsd_readiness_gate_runs_after_start_and_before_auth() -> None:
    tasks = _tasks()
    start = _index_of(tasks, "pcsd enabled + started")
    gate = _index_of(tasks, "wait for pcsd on every site node")
    auth = _index_of(tasks, "auth + cluster setup")
    assert start < gate < auth, "pcsd gate must sit between pcsd start and auth (#916)"


def test_pcsd_readiness_gate_is_bounded_and_covers_every_site_node_hb() -> None:
    tasks = _tasks()
    gate = tasks[_index_of(tasks, "wait for pcsd on every site node")]
    wf = gate["ansible.builtin.wait_for"]
    assert wf["port"] == 2224
    assert wf["host"] == "{{ item.hb }}"
    assert isinstance(wf["timeout"], int) and 0 < wf["timeout"] <= 600
    assert gate["loop"] == "{{ pcmk_site_nodes }}"
    assert gate["run_once"] is True
    # Fail loud: the gate must not be made non-fatal.
    assert "failed_when" not in gate and "ignore_errors" not in gate


def test_auth_setup_retries_bounded_and_keeps_no_log() -> None:
    task = _auth_task()
    assert task["no_log"] is True, "the task handling the password must stay no_log (#916)"
    assert task["register"] == "pcsform"
    assert "pcsform.rc == 0" in task["until"]
    assert isinstance(task["retries"], int) and 1 <= task["retries"] <= 10
    assert isinstance(task["delay"], int) and task["delay"] > 0
    assert _auth_block()["run_once"] is True


def test_password_is_not_on_the_command_line() -> None:
    task = _auth_task()
    script = task["ansible.builtin.shell"]
    assert "lookup(" not in script, "the password must not be templated into the script (#916)"
    assert '-p "$PCMK_HACLUSTER_PASSWORD"' in script
    # The env var's value is the controller-env lookup (not a literal secret).
    controller_lookup = "{{ lookup('env', 'PCMK_HACLUSTER_PASSWORD') }}"
    assert task["environment"]["PCMK_HACLUSTER_PASSWORD"] == controller_lookup


def test_enable_and_start_run_outside_the_formed_probe() -> None:
    # A failed attempt that wrote corosync.conf but never started the cluster must be
    # healed on retry: enable/start run after the probe's fi, not inside its else-branch.
    script = _auth_task()["ansible.builtin.shell"]
    fi = script.index("\nfi\n")
    assert script.index("pcs cluster setup") < fi
    assert script.index("pcs cluster enable --all") > fi
    assert script.index("pcs cluster start --all") > fi
    assert "exit 0" not in script, "already-formed must not short-circuit enable/start (#916)"


def test_rescue_fails_loud_with_redacted_diagnostics() -> None:
    (rescue,) = _auth_block()["rescue"]
    assert "no_log" not in rescue, "the diagnostic must be visible (#916)"
    fail = rescue["ansible.builtin.fail"]["msg"]
    assert "_pcmk_rc" in fail
    assert "replace(_pcmk_pw, '<redacted>')" in fail
    raw = rescue["vars"]["_pcmk_raw"]
    for field in ("pcsform.stdout", "pcsform.stderr", "pcsform.msg"):
        assert field in raw
    # The raw (unredacted) text may only reach the message through the redaction
    # branch or the empty-password branch.
    assert "if (_pcmk_pw | length > 0) else _pcmk_raw" in fail
