from __future__ import annotations

import io
import json

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
        transcript=Transcript(transcript_path("obs", "20260610T000000Z")),
        pauser=_NoPause(),
    )


def _seed_monitoring(tmp_path):
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  obs_box: [obs]\n"
        "  probe: [mon-probe]\n"
        "setups:\n"
        "  monitoring:\n    groups: [obs_box, probe]\n"
    )


# --- targets: renders the file_sd JSON under build/ from topology ---


def test_obs_targets_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "targets"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "prometheus" / "targets" / "node.json"
    hosts = {e["labels"]["host"] for e in json.loads(written.read_text())}
    assert hosts == {"obs", "mon-probe"}


# --- up: renders targets, then runs create + provision steps via the runner ---


def test_obs_up_renders_then_creates_then_provisions(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(
        results=[ScriptedResult(["rendered"]), ScriptedResult(["up"]), ScriptedResult(["ok"])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[1][:3] == ["vagrant", "up", "obs"]
    assert "ansible-playbook" in argvs[2]
    # bare filename (run from ansible/), not a doubled ansible/ansible/ path
    assert argvs[2][-1] == "site-obs.yml"
    # both artifacts rendered eagerly when the steps were built: the scrape
    # targets AND the Ansible inventory the provision step reads
    assert (tmp_path / "build" / "prometheus" / "targets" / "node.json").exists()
    assert (tmp_path / "build" / "inventory.ini").exists()


def test_obs_up_propagates_step_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=4)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 4


# --- status: filters the fleet to the monitoring setup (virsh list --all) ---


def test_obs_status_reads_virsh_for_the_monitoring_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([" -  lab_obs  running"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "status"])

    assert result.exit_code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "list", "--all"]


def test_obs_status_nonzero_exit_propagates(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "status"])

    assert result.exit_code == 1


# --- open: prints the URL and the SSH tunnel one-liner ---


def test_obs_open_prints_url_and_tunnel():
    result = CliRunner().invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    assert "http://10.50.0.2:3000" in result.stdout
    assert "ssh -L" in result.stdout
