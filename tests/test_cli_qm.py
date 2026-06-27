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


# qm dispatch reads the stack's verbs + cluster_group + qm (short-derived names) (#350).
_PCMK_VERBS = (
    "    verbs:\n"
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
    "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n    os: ubuntu\n    short: PCMK\n"
    "    cluster_group: pcmk_a\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n" + _PCMK_VERBS
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
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 0
    play = runner.recorded[-1]
    assert play.argv == [
        "ansible-playbook",
        "site-pcmk-qm.yml",
        "-e",
        "qm_name=PCMKAPP",
        "-e",
        "qm_vip=10.10.1.200",
        "-e",
        "qm_vip_ext=10.60.0.10",
        "-e",
        "qm_app=PCMKAPP",
        "-e",
        "qm_svc=PCMKSVC",
        "-e",
        "chl_to_svc=PCMKAPP.PCMKSVC",
        "-e",
        "chl_to_app=PCMKSVC.PCMKAPP",
    ]
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()
    assert not any(a.startswith("svc_conn=") for a in play.argv)  # not set -> not passed


def test_qm_create_passes_svc_conn_when_set(monkeypatch, tmp_path):
    topo = (
        "nodes:\n"
        "  san-a:   {nics: {net-mgmt: 10.50.0.5}}\n"
        "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
        "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n    os: ubuntu\n    short: PCMK\n"
        "    cluster_group: pcmk_a\n    groups: [san_a, pcmk_a]\n"
        "    provision: ansible/site-pcmk.yml\n"
        "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }\n" + _PCMK_VERBS
    )
    _seed(monkeypatch, tmp_path, topo)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert "svc_conn=10.60.0.50" in runner.recorded[-1].argv


def test_qm_create_members_down_exits_3(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a1 not running
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
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
    result = CliRunner().invoke(cli.app, ["qm", "create", "pcmk-ubuntu"])
    assert result.exit_code == 2


def test_qm_destroy_runs_teardown_playbook(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(
        results=[_probe({"san-a": "running", "pcmk-a1": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "destroy", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[1] == "site-pcmk-qm-down.yml"


def test_qm_up_runs_pcs_enable_on_first_cluster_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk-ubuntu"])
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
    result = CliRunner().invoke(cli.app, ["qm", "down", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs resource disable mq_group"


def test_qm_status_runs_pcs_status(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv[-1] == "pcs status resources"


def test_qm_pcs_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=5)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "up", "pcmk-ubuntu"])
    assert result.exit_code == 5


def test_qm_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["qm", "create", "nope"])
    assert result.exit_code == 2
    assert "no stack" in result.output


def test_qm_stack_without_qm_config_exits_2(monkeypatch, tmp_path):
    _seed(
        monkeypatch,
        tmp_path,
        topo=(
            "groups:\n  g: [h]\n"
            "stacks:\n  bare:\n    mechanism: m\n    os: o\n    short: ''\n    groups: [g]\n"
            "    qm: {}\n    verbs: {}\n"
        ),
    )
    result = CliRunner().invoke(cli.app, ["qm", "up", "bare"])
    assert result.exit_code == 2
    assert "no qm config" in result.output


def test_qm_stack_missing_verb_exits_2(monkeypatch, tmp_path):
    # rdqm stack implements no qm-down verb -> clean exit 2 naming the missing verb
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    result = CliRunner().invoke(cli.app, ["qm", "down", "rdqm-rhel"])
    assert result.exit_code == 2
    assert "does not implement" in result.output


_RDQM_TOPO = (
    "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
    "groups:\n  rdqm_a: [rdqm-a1]\n"
    "stacks:\n  rdqm-rhel:\n    mechanism: rdqm\n    os: rhel\n    short: RDQM\n"
    "    cluster_group: rdqm_a\n    groups: [rdqm_a]\n"
    # no vip_ext: RDQM has one floating IP per QM (#216), spent on the data VIP
    "    qm: { vip: 10.10.1.100 }\n"
    "    verbs:\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "      qm-create: { script: rdqm-qm-create.sh }\n"
)


def test_qm_status_runs_rdqm_cmd_on_cluster_node(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "status", "rdqm-rhel"])
    assert result.exit_code == 0
    assert runner.recorded[-1].argv == [
        "ansible",
        "rdqm_a[0]",
        "-b",
        "-m",
        "shell",
        "-a",
        "/opt/mqm/bin/rdqmstatus -m RDQMAPP",
    ]


def test_qm_create_runs_rdqm_script_with_qm_and_vip(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm-rhel"])
    assert result.exit_code == 0
    argv = runner.recorded[-1].argv
    assert argv[0] == "bash"
    assert argv[1].endswith("/lab/scripts/rdqm-qm-create.sh")
    # QM, the single data-plane floating IP, counterparty CONNAME ("" when unset).
    # No partner VIP: RDQM allows one floating IP per QM (#216 spike).
    assert argv[2:] == ["RDQMAPP", "10.10.1.100", ""]


def test_qm_create_rdqm_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=4)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm-rhel"])
    assert result.exit_code == 4
