from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.pauser import NoTTYError
from mqlab.render import Renderer
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
        transcript=Transcript(transcript_path("vm", "20260609T000000Z")),
        pauser=pauser,
    )


def _seed_topology(tmp_path, names):
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    body = "nodes:\n" + "".join(f"  {n}: {{}}\n" for n in names)
    (tmp_path / "lab" / "topology.yaml").write_text(body)


def test_vm_create_runs_vagrant_up_per_guest(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1", "rdqm-a1"])
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "all"])
    assert result.exit_code == 0
    assert [c.argv for c in runner.recorded] == [
        ["vagrant", "up", "node-a1"],
        ["vagrant", "up", "rdqm-a1"],
    ]
    assert all(str(c.cwd).endswith("/lab") for c in runner.recorded)


def test_vm_create_by_setup_name_resolves_members_in_order(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  pcmk-a2: {}\n"
        "setups:\n  pcmk-san-ha:\n    members: [san-a, pcmk-a1, pcmk-a2]\n"
    )
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(3)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "pcmk-san-ha"])
    assert result.exit_code == 0
    assert [c.argv[-1] for c in runner.recorded] == ["san-a", "pcmk-a1", "pcmk-a2"]


def test_vm_up_runs_virsh_start_on_the_domain(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1", "pcmk-a2"])
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "pcmk"])
    assert result.exit_code == 0
    assert [c.argv for c in runner.recorded] == [
        ["virsh", "-c", "qemu:///system", "start", "lab_pcmk-a1"],
        ["virsh", "-c", "qemu:///system", "start", "lab_pcmk-a2"],
    ]


def test_vm_up_without_pattern_is_a_usage_error():
    result = CliRunner().invoke(cli.app, ["vm", "up"])
    assert result.exit_code == 2


def test_vm_no_match_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "zzz"])
    assert result.exit_code == 2
    assert "no lab guest matches" in result.output


def test_vm_down_runs_virsh_shutdown(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "down", "all"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "shutdown", "lab_node-a1"]


def test_vm_destroy_runs_virsh_undefine_with_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv == [
        "virsh",
        "-c",
        "qemu:///system",
        "undefine",
        "lab_node-a1",
        "--remove-all-storage",
    ]


def test_vm_status_reads_virsh_and_joins_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[ScriptedResult([" -  lab_pcmk-a1  shut off"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "list", "--all"]


def test_vm_status_nonzero_exit_propagates(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status"])
    assert result.exit_code == 1


def test_vm_status_accepts_a_setup_selector(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  rdqm-a1: {}\n"
        "setups:\n  pcmk-san-ha:\n    members: [san-a, pcmk-a1]\n"
    )
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status", "pcmk-san-ha"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[-1] == "--all"  # still virsh list --all (the source)


def test_vm_up_step_without_tty_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1", "node-a2"])  # 2 steps -> pause fires after step 1
    runner = RecordingRunner(results=[ScriptedResult([]), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _FailPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "all", "--step"])
    assert result.exit_code == 2


def test_vm_ssh_execs_vagrant_in_lab(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    chdirs: list[str] = []
    execs: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli.os, "chdir", lambda p: chdirs.append(str(p)))
    monkeypatch.setattr(cli.os, "execvp", lambda f, a: execs.append((f, a)))
    result = CliRunner().invoke(cli.app, ["vm", "ssh", "node-a1"])
    assert result.exit_code == 0
    assert execs == [("vagrant", ["vagrant", "ssh", "node-a1"])]
    assert chdirs and chdirs[0].endswith("/lab")
