"""CLI tests for `mqlab netem set|clear|show` — the WAN latency knob (#1105).

Asserts the verb's argument/dispatch wiring at the unit level: each subcommand
shells lab/scripts/net-latency.sh with the right action + args, and a non-zero
script exit surfaces as the CLI's exit code (never a silent pass). The script's
own attach-point behaviour (per-tap netem on virbr-wan -> symmetric RTT ~= 2D) is
proven empirically and guarded by shellcheck + the cold-rebuild validation #1098,
not here.
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
        transcript=Transcript(transcript_path("netem", "20260801T000000Z")),
        pauser=_NoPause(),
    )


def _seed(monkeypatch, tmp_path):
    # netem needs no topology (it shapes the host bridge, not a stack) — only a repo
    # root so lab_script() resolves lab/scripts/net-latency.sh.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))


def test_netem_set_shells_script(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["netem", "set", "--delay", "10ms"])
    assert result.exit_code == 0
    cmd = runner.recorded[-1]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("/lab/scripts/net-latency.sh")
    assert cmd.argv[2:] == ["set", "10ms"]


def test_netem_clear_shells_script(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["netem", "clear"])
    assert result.exit_code == 0
    cmd = runner.recorded[-1]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("/lab/scripts/net-latency.sh")
    assert cmd.argv[2:] == ["clear"]


def test_netem_show_shells_script(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["netem", "show"])
    assert result.exit_code == 0
    cmd = runner.recorded[-1]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("/lab/scripts/net-latency.sh")
    assert cmd.argv[2:] == ["show"]


def test_netem_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["netem", "set", "--delay", "10ms"])
    # a non-zero script exit (e.g. missing bridge / no taps) surfaces as the CLI's
    # exit code — the knob fails loud, never a silent no-op.
    assert result.exit_code == 7


def test_netem_no_subcommand_is_help(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["netem"])
    # no_args_is_help -> usage, non-zero, never a no-op success.
    assert result.exit_code != 0
    assert "set" in result.output and "clear" in result.output and "show" in result.output
