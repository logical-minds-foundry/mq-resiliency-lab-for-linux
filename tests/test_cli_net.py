from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.pauser import NoTTYError
from mqlab.render import Renderer
from mqlab.runner import SubprocessRunner
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


class _NoPause:
    def wait(self) -> None:
        return None


class _FailPause:
    def wait(self) -> None:
        raise NoTTYError("no tty")


def _deps(runner, pauser):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("net-up", "20260609T000000Z")),
        pauser=pauser,
    )


def test_net_up_runs_the_groomed_script(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult(["up: net-wan"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "up"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[0] == "bash"
    assert runner.recorded[0].argv[1].endswith("lab/scripts/net-up.sh")


def test_net_up_propagates_step_failure_as_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "up"])
    assert result.exit_code == 3


def test_net_up_step_without_tty_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _FailPause()))
    # net up is a single step in production; swap in two steps so the pause fires.
    monkeypatch.setattr(cli, "_net_up_steps", cli._twin_steps)
    result = CliRunner().invoke(cli.app, ["net", "up", "--step"])
    assert result.exit_code == 2


def test_net_down_runs_the_groomed_script(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "scripts").mkdir(parents=True)
    runner = RecordingRunner(results=[ScriptedResult(["down: net-wan"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "down"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[1].endswith("lab/scripts/net-down.sh")


def test_net_status_renders_and_succeeds(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    runner = RecordingRunner(results=[ScriptedResult([" net-wan active yes yes"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "status"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[-1] == "--all"


def test_net_status_nonzero_exit_propagates(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "status"])
    assert result.exit_code == 1


def test_build_deps_constructs_real_dependencies(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    deps = cli.build_deps("net-up", "20260609T000000Z")
    try:
        assert isinstance(deps.runner, SubprocessRunner)
    finally:
        deps.transcript.close()


def test_main_invokes_the_app(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(cli, "app", lambda: called.append(True))
    cli.main()
    assert called == [True]
