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


def _seed_nets(tmp_path, names):
    nets = tmp_path / "lab" / "networks"
    nets.mkdir(parents=True, exist_ok=True)
    for name in names:
        (nets / f"{name}.xml").write_text("<network/>")


def test_net_up_all_runs_script_with_resolved_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-data-a", "net-data-b", "net-wan"])
    runner = RecordingRunner(results=[ScriptedResult(["up: net-wan"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "up", "all"])
    assert result.exit_code == 0
    argv = runner.recorded[0].argv
    assert argv[0] == "bash"
    assert argv[1].endswith("lab/scripts/net-up.sh")
    assert argv[2:] == ["net-data-a", "net-data-b", "net-wan"]


def test_net_up_regex_selects_subset(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-data-a", "net-data-b", "net-wan"])
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "up", "data"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[2:] == ["net-data-a", "net-data-b"]


def test_net_up_without_pattern_is_a_usage_error():
    # The safety gate: a bare destructive/bulk verb must not default to everything.
    result = CliRunner().invoke(cli.app, ["net", "up"])
    assert result.exit_code == 2


def test_net_up_propagates_step_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-wan"])
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "up", "all"])
    assert result.exit_code == 3


def test_net_down_all_runs_script_with_resolved_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-wan"])
    runner = RecordingRunner(results=[ScriptedResult(["down: net-wan"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "down", "all"])
    assert result.exit_code == 0
    argv = runner.recorded[0].argv
    assert argv[1].endswith("lab/scripts/net-down.sh")
    assert argv[2:] == ["net-wan"]


def test_net_down_without_pattern_is_a_usage_error():
    result = CliRunner().invoke(cli.app, ["net", "down"])
    assert result.exit_code == 2


def test_net_no_match_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-wan"])
    result = CliRunner().invoke(cli.app, ["net", "down", "zzz"])
    assert result.exit_code == 2
    assert "no lab network matches" in result.output


def test_net_invalid_regex_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-wan"])
    result = CliRunner().invoke(cli.app, ["net", "show", "["])
    assert result.exit_code == 2
    assert "invalid pattern" in result.output


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


def test_net_show_runs_three_reads_for_one_net(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-data-a"])
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "show", "net-data-a"])
    assert result.exit_code == 0
    subcommands = [c.argv[3] for c in runner.recorded]
    assert subcommands == ["net-info", "net-dumpxml", "net-dhcp-leases"]
    assert all(c.argv[-1] == "net-data-a" for c in runner.recorded)


def test_net_show_expands_pattern_to_multiple_nets(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-data-a", "net-data-b", "net-wan"])
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(6)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["net", "show", "data"])
    assert result.exit_code == 0
    assert len(runner.recorded) == 6
    assert [c.argv[-1] for c in runner.recorded] == ["net-data-a"] * 3 + ["net-data-b"] * 3


def test_net_show_step_without_tty_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_nets(tmp_path, ["net-data-a"])  # 3 steps -> the pause fires after step 1
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _FailPause()))
    result = CliRunner().invoke(cli.app, ["net", "show", "net-data-a", "--step"])
    assert result.exit_code == 2


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
