from __future__ import annotations

import io
import json

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult


@pytest.fixture(autouse=True)
def _stub_mq_ensure(monkeypatch):
    # obs up ensures the monitoring MQ tarball before site-obs.yml copies it (#335);
    # stub the fetch by default so unit tests never hit IBM's CDN. The dedicated test
    # below overrides this with a recorder to assert the call.
    monkeypatch.setattr(cli, "ensure_mq_tarballs", lambda *a, **k: [])


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
        "  svc-sim: {nics: {net-mgmt: 10.50.0.50}}\n"
        "  app-client: {nics: {net-mgmt: 10.50.0.60}}\n"
        "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "  rdqm_a: [rdqm-a1]\n  rdqm_b: [rdqm-b1]\n"
        "  svc: [svc-sim]\n  app: [app-client]\n"
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
    written = tmp_path / "build" / "work" / "prometheus" / "targets" / "node.json"
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
            ScriptedResult(["pki"]),
            ScriptedResult(["ok"]),
            ScriptedResult(["host ok"]),
            ScriptedResult(["relay ok"]),
            ScriptedResult(['{"database":"ok"}']),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[1][:3] == ["vagrant", "up", "obs"]
    # PKI material is ensured before site-obs.yml's pki-distribute needs it (#341)
    assert "site-pki.yml" in argvs[2]
    assert "ansible-playbook" in argvs[3]
    # bare filename (run from ansible/), not a doubled ansible/ansible/ path
    assert argvs[3][-1] == "site-obs.yml"
    # the host collector is provisioned via a connection=local host-obs play
    assert any("host-obs.yml" in a for a in argvs)
    # after provisioning bounces grafana, the port-forward relay is re-healed (#264)
    assert argvs[5] == ["sudo", "systemctl", "restart", *cli._RELAY_UNITS]
    # ...then the workstation-facing endpoint is verified fail-loud (curl -fsS)
    assert argvs[6][0] == "curl"
    assert "-fsS" in argvs[6]
    assert argvs[6][-1] == "http://localhost:3000/api/health"
    # all three artifacts rendered eagerly when the steps were built
    assert (tmp_path / "build" / "work" / "prometheus" / "targets" / "node.json").exists()
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()
    assert (tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-status.json").exists()


def test_obs_up_ensures_monitoring_mq_tarball(monkeypatch, tmp_path):
    # obs up runs site-obs.yml directly (not via _provision), so it must itself ensure
    # mon-probe's MQ tarball — with the repo default version, since monitoring has no
    # manifest. (#335)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    ensured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli, "ensure_mq_tarballs", lambda setup, version, *a, **k: ensured.append((setup, version))
    )
    runner = RecordingRunner(results=[ScriptedResult(["ok"]) for _ in range(7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    assert ensured == [("monitoring", cli.DEFAULT_MQ_VERSION)]


def test_obs_up_also_renders_the_cockpit_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["ok"]) for _ in range(7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"
    assert board.exists()
    assert json.loads(board.read_text())["uid"] == "lab-pcmk-cluster"


def test_obs_up_also_renders_the_nativeha_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["ok"]) for _ in range(7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-nativeha-cluster.json"
    assert board.exists()
    assert json.loads(board.read_text())["uid"] == "lab-nativeha-cluster"


def test_obs_up_also_renders_the_rdqm_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["ok"]) for _ in range(7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))

    result = CliRunner().invoke(cli.app, ["obs", "up"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-rdqm-cluster.json"
    assert board.exists()
    assert json.loads(board.read_text())["uid"] == "lab-rdqm-cluster"


def test_obs_dashboard_also_renders_the_rdqm_board(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    board = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-rdqm-cluster.json"
    assert json.loads(board.read_text())["uid"] == "lab-rdqm-cluster"


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


# --- open: prints the workstation URL and explains the automatic forward ---


def test_obs_open_prints_workstation_url_and_automatic_forward():
    result = CliRunner().invoke(cli.app, ["obs", "open"])
    assert result.exit_code == 0
    # the workstation browses plain localhost:3000 (auto-forwarded), plus the
    # in-VM direct URL for reference
    assert "http://localhost:3000/d/lab-fleet-node" in result.stdout
    assert "http://10.50.0.2:3000/d/lab-fleet-node" in result.stdout
    assert "/explore" in result.stdout
    assert "mqlab-requester" in result.stdout
    # the forward is automatic and anonymous now — the stale manual-tunnel /
    # admin-login guidance must be gone (#264, #258)
    assert "automatic" in result.stdout
    assert "ssh -F" not in result.stdout
    assert "-L 3000:" not in result.stdout
    assert "admin / admin" not in result.stdout


def test_obs_dashboard_writes_file_from_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(RecordingRunner()))

    result = CliRunner().invoke(cli.app, ["obs", "dashboard"])

    assert result.exit_code == 0
    written = tmp_path / "build" / "work" / "grafana" / "dashboards" / "lab-status.json"
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
    data = json.loads((tmp_path / "build" / "work" / "obs" / "reach-peers.json").read_text())
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
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()
    assert (tmp_path / "build" / "work" / "obs" / "reach-peers.json").exists()


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


def test_obs_up_runs_prepare_lab(monkeypatch, tmp_path, prepare_lab_calls):
    # obs up shells `vagrant up obs mon-probe`, so it must gate (#276).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_monitoring(tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["x"]) for _ in range(7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["obs", "up"])
    assert result.exit_code == 0
    assert prepare_lab_calls == ["prepare"]
