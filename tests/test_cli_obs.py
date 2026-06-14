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
    # Every group host also needs a node with a net-mgmt IP — render_inventory
    # raises on a group that references an undefined host.
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "  san-b: {nics: {net-mgmt: 10.50.0.6}}\n"
        "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
        "  pcmk-b1: {nics: {net-mgmt: 10.50.0.61}}\n"
        "  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "  rdqm-b1: {nics: {net-mgmt: 10.50.0.41}}\n"
        "  qm-main: {nics: {net-mgmt: 10.50.0.10}}\n"
        "  dtcc-sim: {nics: {net-mgmt: 10.50.0.50}}\n"
        "  app-client: {nics: {net-mgmt: 10.50.0.60}}\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "  rdqm_a: [rdqm-a1]\n  rdqm_b: [rdqm-b1]\n"
        "  qm: [qm-main]\n  dtcc: [dtcc-sim]\n  client: [app-client]\n"
        "  obs_box: [obs]\n  probe: [mon-probe]\n"
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
    assert {"obs", "mon-probe"} <= hosts


# --- up: renders targets, then runs create + provision steps via the runner ---


def test_obs_up_renders_then_creates_then_provisions(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(
        results=[
            ScriptedResult(["rendered"]),
            ScriptedResult(["up"]),
            ScriptedResult(["ok"]),
            ScriptedResult(["host ok"]),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[1][:3] == ["vagrant", "up", "obs"]
    assert "ansible-playbook" in argvs[2]
    # bare filename (run from ansible/), not a doubled ansible/ansible/ path
    assert argvs[2][-1] == "site-obs.yml"
    # the host collector is provisioned via a connection=local host-obs play
    assert any("host-obs.yml" in a for a in argvs)
    # all three artifacts rendered eagerly when the steps were built
    assert (tmp_path / "build" / "prometheus" / "targets" / "node.json").exists()
    assert (tmp_path / "build" / "inventory.ini").exists()
    assert (tmp_path / "build" / "grafana" / "dashboards" / "lab-status.json").exists()


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
    assert "-L 3000:10.50.0.2:3000" in result.stdout
    assert "limactl list" in result.stdout


def test_obs_dashboard_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "grafana" / "dashboards" / "lab-status.json"
    assert json.loads(written.read_text())["uid"] == "lab-fleet-node"


def test_obs_net_state_emits_textfile_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab" / "networks").mkdir(parents=True)
    (tmp_path / "lab" / "networks" / "net-hb-a.xml").write_text("<network/>")
    runner = RecordingRunner(
        results=[
            ScriptedResult(
                [
                    " Name      State    Autostart   Persistent",
                    "----------------------------------------------",
                    " net-hb-a   active   yes         yes",
                ]
            )
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "net-state"])

    assert result.exit_code == 0
    assert 'lab_network_state{network="net-hb-a"} 2' in result.stdout


def test_obs_reach_peers_writes_build_json(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  pcmk-a1: {nics: {net-hb-a: 172.16.1.51}}\n"
        "  pcmk-a2: {nics: {net-hb-a: 172.16.1.52}}\n"
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "reach-peers"])

    assert result.exit_code == 0
    data = json.loads((tmp_path / "build" / "obs" / "reach-peers.json").read_text())
    assert data["pcmk-a1"]["net-hb-a"][0]["peer"] == "pcmk-a2"


# --- instrument: install node_exporter (+ net-reach) on a setup's running guests ---

_VIRSH = ["virsh", "-c", "qemu:///system"]

_INSTRUMENT_TOPO = (
    "nodes:\n"
    "  san-a: {nics: {net-mgmt: 10.50.0.5, net-hb-a: 172.16.1.5}}\n"
    "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51, net-hb-a: 172.16.1.51}}\n"
    "  pcmk-a2: {nics: {net-mgmt: 10.50.0.52, net-hb-a: 172.16.1.52}}\n"
    "groups:\n"
    "  san_a: [san-a]\n"
    "  pcmk_a: [pcmk-a1, pcmk-a2]\n"
    "setups:\n"
    "  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
)


def _probe(states):
    """A scripted `virsh list --all` result placing each guest in a given state."""
    lines = [" Id   Name              State", "------------------------------------"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def test_obs_instrument_renders_then_runs_observability_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(_INSTRUMENT_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running", "pcmk-a2": "running"}),
            ScriptedResult([]),  # ansible-playbook observability.yml
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "instrument", "pcmk_san_ha"])

    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == [*_VIRSH, "list", "--all"]
    play = runner.recorded[-1]
    assert play.argv == [
        "ansible-playbook",
        "observability.yml",
        "--limit",
        "pcmk_san_ha",
    ]
    assert str(play.cwd).endswith("/ansible")
    # renders the inventory the play resolves through + the reach-peers map net-reach reads
    assert (tmp_path / "build" / "inventory.ini").exists()
    assert (tmp_path / "build" / "obs" / "reach-peers.json").exists()


def test_obs_instrument_unknown_setup_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(_INSTRUMENT_TOPO)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "instrument", "nope"])

    assert result.exit_code == 2
    assert "no lab setup named" in result.output


def test_obs_instrument_members_down_advises_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(_INSTRUMENT_TOPO)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a* absent
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "instrument", "pcmk_san_ha"])

    assert result.exit_code == 3
    # only the probe ran — no playbook against down guests
    assert [c.argv for c in runner.recorded] == [[*_VIRSH, "list", "--all"]]


def test_obs_instrument_playbook_failure_propagates_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir()
    (tmp_path / "lab" / "topology.yaml").write_text(_INSTRUMENT_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running", "pcmk-a2": "running"}),
            ScriptedResult([], exit_code=4),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "instrument", "pcmk_san_ha"])

    assert result.exit_code == 4
