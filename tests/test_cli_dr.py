"""CLI tests for `mqlab dr cutover|failback` — the DR *operation* verb (#867).

Asserts the verb's argument/dispatch/env wiring at the unit level: it resolves the
stack, gates on the rdqm mechanism, renders the inventory the wrapped script's ansible
calls read, and shells lab/scripts/rdqm-dr-cutover.sh with the right direction, QM, cwd,
and (opt-in) RPO0_DRILL env. Live cutover behaviour is out of scope here — the script's
own contract/parser tests live in tests/test_rdqm_dr_cutover.py, and a real seed→cut→
retrieve RPO-0 run is a deferred follow-on that needs the live rdqm DR arm up.
"""

from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("dr", "20260801T000000Z")),
        pauser=_NoPause(),
    )


# The live rdqm-rhel arm: one cluster group, a short (so QM names derive: RDQMAPP), and a
# data VIP. The DR script defaults are fine; the verb passes direction + stack.qm.qm_app.
_RDQM_TOPO = (
    "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
    "groups:\n  rdqm_a: [rdqm-a1]\n"
    "stacks:\n  rdqm-rhel:\n    mechanism: rdqm\n    os: rhel\n    short: RDQM\n"
    "    cluster_group: rdqm_a\n    groups: [rdqm_a]\n"
    "    qm: { vip: 10.10.1.100, vip_b: 10.10.2.100 }\n"
    "    verbs:\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)

# A non-rdqm stack (pacemaker-san) — has a QM but no rdqm-dr-cutover flow.
_PCMK_TOPO = (
    "nodes:\n  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  pcmk_a: [pcmk-a1]\n"
    "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n    os: ubuntu\n    short: PCMK\n"
    "    cluster_group: pcmk_a\n    groups: [pcmk_a]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
    "    verbs: { qm-status: { pcs: status resources } }\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
)


def _seed(monkeypatch, tmp_path, topo=_RDQM_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def test_dr_cutover_shells_rdqm_script_a2b(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel"])
    assert result.exit_code == 0
    cmd = runner.recorded[-1]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("/lab/scripts/rdqm-dr-cutover.sh")
    # cutover -> a2b (site A -> B); the QM is the stack's short-derived app QM.
    assert cmd.argv[2:] == ["a2b", "RDQMAPP"]
    assert str(cmd.cwd).endswith("/ansible")
    # no drill unless asked -> no RPO0_DRILL env injected.
    assert cmd.env is None
    # the script's ansible calls read the rendered inventory.
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_dr_failback_shells_rdqm_script_b2a(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "failback", "rdqm-rhel"])
    assert result.exit_code == 0
    # failback -> b2a (site B -> A); the mirror direction.
    assert runner.recorded[-1].argv[2:] == ["b2a", "RDQMAPP"]


def test_dr_cutover_rpo0_drill_sets_env(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel", "--rpo0-drill"])
    assert result.exit_code == 0
    # --rpo0-drill flips the script's opt-in RPO-0 message-survival assertion on via env.
    assert runner.recorded[-1].env == {"RPO0_DRILL": "1"}


def test_dr_failback_rpo0_drill_sets_env(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "failback", "rdqm-rhel", "--rpo0-drill"])
    assert result.exit_code == 0
    assert runner.recorded[-1].env == {"RPO0_DRILL": "1"}


def test_dr_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel"])
    # a non-zero script exit surfaces as the CLI's exit code — an RPO-0 violation
    # (the script's own `return 1`) fails the verb loud, never a silent pass.
    assert result.exit_code == 7


def test_dr_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "nope"])
    assert result.exit_code == 2
    assert "no stack" in result.output


def test_dr_non_rdqm_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PCMK_TOPO)
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "pcmk-ubuntu"])
    assert result.exit_code == 2
    assert "only the rdqm mechanism" in result.output


def test_dr_no_subcommand_is_help(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["dr"])
    # no_args_is_help -> usage, non-zero, never a no-op success.
    assert result.exit_code != 0
    assert "cutover" in result.output and "failback" in result.output
