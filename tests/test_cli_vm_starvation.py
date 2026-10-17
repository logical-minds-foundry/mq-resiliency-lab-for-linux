"""`mqlab vm starvation`: name host CPU starvation in the running guests (#1602).

Pins the CLI wiring (only running topology guests are checked, the extra vars travel as
JSON so a spaced --since survives, the playbook's exit code is the verb's, nothing runs
when no guest is up) and the playbook's contract (read-only, requires its inputs, and the
two kernel signatures it searches for match real lines from the #1602 incidents).
"""

from __future__ import annotations

import io
import json
import pathlib
import re

import yaml
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_PLAYBOOK = pathlib.Path("ansible/host-starvation.yml")

_TOPO = (
    "nodes:\n"
    "  nha-a1: {nics: {net-mgmt: 10.50.0.11}}\n"
    "  nha-a2: {nics: {net-mgmt: 10.50.0.12}}\n"
    "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
    "groups:\n  nha_a: [nha-a1, nha-a2]\n"
    "stacks: {}\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("vm-starvation", "20261017T000000Z")),
        pauser=_NoPause(),
    )


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_TOPO)


def _probe(states):
    lines = [" Id   Name        State", "----"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def _invoke(monkeypatch, runner, *args):
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    return CliRunner().invoke(cli.app, ["vm", "starvation", *args])


def _extra_vars(command) -> dict[str, str]:
    argv = command.argv
    assert argv[:2] == ["ansible-playbook", "host-starvation.yml"]
    assert argv[2] == "-e"
    return json.loads(argv[3])


# --- CLI wiring ------------------------------------------------------------------------


def test_checks_only_the_running_guests_over_the_whole_boot(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    states = {"nha-a1": "running", "nha-a2": "shut off", "obs": "running"}
    runner = RecordingRunner(results=[_probe(states), ScriptedResult([])])
    result = _invoke(monkeypatch, runner)
    assert result.exit_code == 0, result.output
    play = runner.recorded[-1]
    assert _extra_vars(play) == {"starvation_hosts": "nha-a1:obs", "starvation_since": ""}
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_a_spaced_since_reaches_the_playbook_whole(monkeypatch, tmp_path):
    # key=value extra vars split on spaces; the JSON form must carry '10 min ago' intact.
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"obs": "running"}), ScriptedResult([])])
    result = _invoke(monkeypatch, runner, "--since", "10 min ago")
    assert result.exit_code == 0, result.output
    assert _extra_vars(runner.recorded[-1])["starvation_since"] == "10 min ago"


def test_starvation_found_fails_the_verb_with_the_playbook_exit_code(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"obs": "running"}), ScriptedResult([], 2)])
    result = _invoke(monkeypatch, runner)
    assert result.exit_code == 2


def test_no_running_guest_exits_3_without_running_the_playbook(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"nha-a1": "shut off"})])
    result = _invoke(monkeypatch, runner)
    assert result.exit_code == 3
    assert [c.argv[0] for c in runner.recorded] == ["virsh"]


# --- the playbook's contract -------------------------------------------------------------


def _plays():
    return yaml.safe_load(_PLAYBOOK.read_text())


def test_playbook_requires_its_inputs():
    guard, _read, _report = _plays()
    assert guard["hosts"] == "localhost"
    that = guard["tasks"][0]["ansible.builtin.assert"]["that"]
    assert any("starvation_hosts" in cond for cond in that)
    assert any("starvation_since" in cond for cond in that)


def test_playbook_is_read_only():
    # Every task on a guest only reads (journalctl) or computes; nothing changes a guest.
    _guard, read, _report = _plays()
    modules = {key for task in read["tasks"] for key in task if key.startswith("ansible.")}
    assert modules == {"ansible.builtin.command", "ansible.builtin.set_fact"}
    command = next(t for t in read["tasks"] if "ansible.builtin.command" in t)
    assert command["changed_when"] is False
    assert "'journalctl', '-k'" in command["ansible.builtin.command"]["argv"]


def _search_patterns() -> list[str]:
    _guard, read, _report = _plays()
    count = next(t for t in read["tasks"] if "ansible.builtin.set_fact" in t)
    found = re.findall(r"select\('search', '([^']+)'\)", " ".join(count["vars"].values()))
    assert len(found) == 2
    return found


# Real kernel lines from the #1602 incidents (nha-ubuntu-a1 and obs, 2026-10-17).
_LOCKUP = (
    "2026-10-17T11:26:56+00:00 nha-ubuntu-a1 kernel: watchdog: BUG: soft lockup - CPU#0 "
    "stuck for 69s! [amqzmuc0:7450]"
)
_STALL = (
    "2026-10-17T11:26:56+00:00 nha-ubuntu-a1 kernel: rcu: INFO: rcu_preempt detected "
    "stalls on CPUs/tasks:"
)
_SELF_STALL = (
    "2026-10-17T11:30:00+00:00 obs kernel: rcu: INFO: rcu_sched self-detected stall on CPU"
)
_NOISE = [
    "2026-10-17T11:26:56+00:00 nha-ubuntu-a1 kernel: rcu: rcu_preempt kthread starved for "
    "79389 jiffies!",
    "2026-10-17T11:45:50+00:00 nha-ubuntu-a1 kernel: [CRTC:36:crtc-0] vblank wait timed out",
]


def test_signatures_match_the_incident_lines_once_each():
    lockup, stall = _search_patterns()
    assert re.search(lockup, _LOCKUP)
    assert not re.search(lockup, _STALL)
    assert re.search(stall, _STALL)
    assert re.search(stall, _SELF_STALL)
    assert not re.search(stall, _LOCKUP)
    for line in _NOISE:  # follow-on lines of one event must not count it twice
        assert not re.search(lockup, line)
        assert not re.search(stall, line)


def test_report_fails_loud_on_starved_or_unread_guests_and_names_the_cause():
    _guard, _read, report = _plays()
    check = report["tasks"][-1]["ansible.builtin.assert"]
    assert check["that"] == ["_starved | length == 0", "_unread | length == 0"]
    assert "HOST CPU STARVATION" in check["fail_msg"]
    assert "lab-gotchas.md" in check["fail_msg"]
    assert "Could not read the kernel log" in check["fail_msg"]
