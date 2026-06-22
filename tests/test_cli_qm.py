from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("qm", "20260611T000000Z")),
        pauser=_NoPause(),
    )


_ARMS = (
    "arms:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n    cluster_group: pcmk_a\n    verbs:\n"
    "      qm-create: { playbook: site-pcmk-qm.yml }\n"
    "      qm-destroy: { playbook: site-pcmk-qm-down.yml }\n"
    "      qm-up: { pcs: resource enable mq_group }\n"
    "      qm-down: { pcs: resource disable mq_group }\n"
    "      qm-status: { pcs: status resources }\n"
)

_TOPO = (
    "nodes:\n"
    "  san-a:   {nics: {net-mgmt: 10.50.0.5}}\n"
    "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    + _ARMS
    + "setups:\n  pcmk_san_ha:\n    arm: pcmk-ubuntu\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    qm: { name: QMPCMK, vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
)


def _probe(states):
    lines = [" Id   Name        State", "----"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def _seed(monkeypatch, tmp_path, topo=_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def _argvs(runner):
    return [c.argv for c in runner.recorded]


def test_qm_create_runs_playbook_with_qm_extra_vars(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == [
        "ansible-playbook",
        "site-pcmk-qm.yml",
        "-e",
        "qm_name=QMPCMK",
        "-e",
        "qm_vip=10.10.1.200",
        "-e",
        "qm_vip_ext=10.60.0.10",
    ]
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()
    assert not any(a.startswith("svc_conn=") for a in play.argv)  # not set -> not passed


def test_qm_create_passes_dtcc_conn_when_set(monkeypatch, tmp_path):
    topo = (
        "nodes:\n"
        "  san-a:   {nics: {net-mgmt: 10.50.0.5}}\n"
        "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
        + _ARMS
        + "setups:\n  distributed:\n    arm: pcmk-ubuntu\n    groups: [san_a, pcmk_a]\n"
        "    provision: ansible/site-distributed.yml\n"
        "    qm: { name: QMPCMK, vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }\n"
    )
    _seed(monkeypatch, tmp_path, topo)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "distributed"])
    assert result.exit_code == 0
    assert "svc_conn=10.60.0.50" in runner.recorded[-1].argv


def test_qm_create_members_down_exits_3(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a1 not running
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 3
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]  # probe only, no playbook


def test_qm_create_playbook_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult([], exit_code=2),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk_san_ha"])
    assert result.exit_code == 2


def test_qm_destroy_runs_teardown_playbook(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "destroy", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[1] == "site-pcmk-qm-down.yml"


def test_qm_up_runs_pcs_enable_on_first_cluster_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible",
        "pcmk_a[0]",
        "-b",
        "-m",
        "shell",
        "-a",
        "pcs resource enable mq_group",
    ]


def test_qm_down_runs_pcs_disable(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "down", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs resource disable mq_group"


def test_qm_status_runs_pcs_status(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs status resources"


def test_qm_pcs_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=5)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk_san_ha"])
    assert result.exit_code == 5


def test_qm_unknown_setup_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["qm", "create", "nope"])
    assert result.exit_code == 2
    assert "no lab setup" in result.output


def test_qm_setup_without_qm_config_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, topo="groups:\n  g: [h]\nsetups:\n  bare:\n    groups: [g]\n")
    result = CliRunner().invoke(cli.app, ["qm", "up", "bare"])
    assert result.exit_code == 2
    assert "no qm config" in result.output


_RDQM_TOPO = (
    "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
    "groups:\n  rdqm_a: [rdqm-a1]\n"
    "arms:\n  rdqm-rhel:\n    mechanism: rdqm\n    cluster_group: rdqm_a\n    verbs:\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "      qm-create: { script: rdqm-qm-create.sh }\n"
    "setups:\n  rdqm_dist:\n    arm: rdqm-rhel\n    groups: [rdqm_a]\n"
    # no vip_ext: RDQM has one floating IP per QM (#216), spent on the data VIP
    "    qm: { name: QMRDQM, vip: 10.10.1.100 }\n"
)


def test_qm_status_runs_rdqm_cmd_on_cluster_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "rdqm_dist"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible",
        "rdqm_a[0]",
        "-b",
        "-m",
        "shell",
        "-a",
        "/opt/mqm/bin/rdqmstatus -m QMRDQM",
    ]


def test_qm_create_runs_rdqm_script_with_qm_and_vip(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm_dist"])
    assert result.exit_code == 0
    argv = runner.recorded[-1].argv
    assert argv[0] == "bash"
    assert argv[1].endswith("/lab/scripts/rdqm-qm-create.sh")
    # QM, the single data-plane floating IP, counterparty CONNAME ("" when unset).
    # No partner VIP: RDQM allows one floating IP per QM (#216 spike).
    assert argv[2:] == ["QMRDQM", "10.10.1.100", ""]


def test_qm_create_rdqm_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=4)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm_dist"])
    assert result.exit_code == 4
