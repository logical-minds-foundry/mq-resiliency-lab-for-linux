from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, instances
from mqlab.instances import InstanceRecord
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import OsRef
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
    "  san-a:   {box: san, nics: {net-mgmt: 10.50.0.5}}\n"
    "  pcmk-a1: {box: pcmk, nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n"
    "    os_family: ubuntu\n    short: PCMK\n"
    "    cluster_group: pcmk_a\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
    + _PCMK_VERBS
    + "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
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
        "qm_svc=SVCQM",
        "-e",
        "chl_to_svc=PCMKAPP.SVCQM",
        "-e",
        "chl_to_app=SVCQM.PCMKAPP",
        # each stack owns its own request queue on the shared SVCQM (#446)
        "-e",
        "svc_req_queue=PCMK.SVC.REQUEST",
        # svc is now the shared counterparty; its CONNAME comes from the svc: block
        # and is threaded for every stack (#446).
        "-e",
        "svc_conn=10.60.0.50",
    ]
    assert str(play.cwd).endswith("/ansible")
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_qm_create_passes_svc_conn_from_the_shared_svc_block(monkeypatch, tmp_path):
    # Post-#446 the counterparty CONNAME is a property of the shared svc: block, not
    # the per-stack qm: block — it is threaded for every stack that has one.
    topo = (
        "nodes:\n"
        "  san-a:   {box: san, nics: {net-mgmt: 10.50.0.5}}\n"
        "  pcmk-a1: {box: pcmk, nics: {net-mgmt: 10.50.0.51}}\n"
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
        "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n"
        "    os_family: ubuntu\n    short: PCMK\n"
        "    cluster_group: pcmk_a\n    groups: [san_a, pcmk_a]\n"
        "    provision: ansible/site-pcmk.yml\n"
        "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
        + _PCMK_VERBS
        + "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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
            "stacks:\n  bare:\n    mechanism: m\n    os_family: o\n    short: ''\n    groups: [g]\n"
            "    qm: {}\n    verbs: {}\n"
            "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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
    "stacks:\n  rdqm-rhel:\n    mechanism: rdqm\n    os_family: rhel\n    short: RDQM\n"
    "    cluster_group: rdqm_a\n    groups: [rdqm_a]\n"
    # no vip_ext: RDQM has one floating IP per QM (#216), spent on the data VIP
    "    qm: { vip: 10.10.1.100 }\n"
    "    verbs:\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "      qm-create: { script: rdqm-qm-create.sh }\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
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
    # QM, the single data-plane floating IP, the shared counterparty CONNAME (from the
    # svc: block), the shared counterparty QM (SVCQM), and this stack's request queue
    # on it (#446). No partner VIP: RDQM allows one floating IP per QM.
    assert argv[2:] == ["RDQMAPP", "10.10.1.100", "10.60.0.50", "SVCQM", "RDQM.SVC.REQUEST"]


def test_qm_create_rdqm_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _RDQM_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=4)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "create", "rdqm-rhel"])
    assert result.exit_code == 4


# --- the instance-record gate (epic .github#280, T3) -----------------------------------
_QM_VERBS = ["create", "destroy", "up", "down", "status"]


@pytest.mark.parametrize("verb", _QM_VERBS)
def test_qm_verb_refuses_a_live_stack_without_record(monkeypatch, tmp_path, verb):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    result = CliRunner().invoke(cli.app, ["qm", verb, "pcmk-ubuntu"])
    assert result.exit_code == 2
    assert (
        "pcmk-ubuntu is running without a version record; run `mqlab teardown pcmk-ubuntu` "
        "and re-bootstrap"
    ) in result.stderr
    assert runner.recorded == []  # refused before any ansible/virsh step


@pytest.mark.parametrize("verb", ["up", "status"])
def test_qm_verb_runs_on_a_live_recorded_stack(monkeypatch, tmp_path, verb):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    instances.write_record(InstanceRecord("pcmk-ubuntu", OsRef("ubuntu", 24), None, "t"))
    result = CliRunner().invoke(cli.app, ["qm", verb, "pcmk-ubuntu"])
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv[0] == "ansible"


# --- qm e2e: a bounded burst through the stack's flow, from the app host (#1342) -------
# Per-stack QM/CONN/keyrepo derivation against the committed sources is in test_e2e.py;
# these pin the verb's wiring: vagrant ssh to the app host from lab/ with the shared
# dotfile (#355), the record gate, exit propagation, and fail-loud config errors.
_NHA_TOPO = (
    "nodes:\n  nha-ubuntu-a1: {nics: {net-mgmt: 10.50.0.61}}\n"
    "  app-client: {nics: {net-mgmt: 10.50.0.40}}\n"
    "groups:\n  nha_ubuntu_a: [nha-ubuntu-a1]\n  app: [app-client]\n"
    "stacks:\n  nativeha-ubuntu:\n    mechanism: native-ha\n    os_family: ubuntu\n"
    "    short: NHAU\n    cluster_group: nha_ubuntu_a\n    groups: [nha_ubuntu_a]\n"
    "    provision: ansible/site-nativeha-ubuntu.yml\n    qm: {}\n    verbs: {}\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)
_NHA_CONN = "nha-ubuntu-a1-data-a.client.com(1414),nha-ubuntu-a2-data-a.client.com(1414)"


def _seed_e2e(monkeypatch, tmp_path, *, playbook=True):
    _seed(monkeypatch, tmp_path, _NHA_TOPO)
    defaults = tmp_path / "ansible" / "roles" / "app-requester" / "defaults"
    defaults.mkdir(parents=True)
    (defaults / "main.yml").write_text(
        "app_requester_channel: APP.SVRCONN\n"
        "app_requester_keyrepo: /home/vagrant/ssl/key\n"
        "app_requester_certlabel: app-client\n"
    )
    if playbook:
        (tmp_path / "ansible" / "site-nativeha-ubuntu.yml").write_text(
            "- import_playbook: site-distributed-shared.yml\n"
            f"  vars:\n    app_conn: '{_NHA_CONN}'\n    app_tls: true\n"
        )


def test_qm_e2e_runs_the_requester_on_the_app_host(monkeypatch, tmp_path, prepare_lab_calls):
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["20/20 round-trips"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu", "--count", "20"])
    assert result.exit_code == 0, result.output
    [cmd] = runner.recorded
    assert cmd.argv[:4] == ["vagrant", "ssh", "app-client", "-c"]
    assert cmd.argv[4] == (
        "LD_LIBRARY_PATH=/opt/mqm/lib64 "
        "/opt/logical-minds-foundry/mq-resiliency-clients/venv/bin/mq-app-requester "
        f"--qm NHAUAPP --conn '{_NHA_CONN}' --channel APP.SVRCONN --count 20 "
        "--keyrepo /home/vagrant/ssl/key --certlabel app-client"
    )
    assert cmd.cwd == tmp_path / "lab"
    # the shared Vagrant dotfile, resolved the same way every vagrant-driving verb does
    assert cmd.env == {"VAGRANT_DOTFILE_PATH": str((tmp_path / "build/state/vagrant").resolve())}
    assert prepare_lab_calls == ["prepare"]


def test_qm_e2e_count_defaults_to_5(monkeypatch, tmp_path):
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu"])
    assert result.exit_code == 0, result.output
    assert " --count 5 " in runner.recorded[0].argv[4]


def test_qm_e2e_missed_round_trip_exits_nonzero(monkeypatch, tmp_path):
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["MQRC 2381"], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu"])
    assert result.exit_code == 1


def test_qm_e2e_rejects_a_zero_count(monkeypatch, tmp_path):
    # --count 0 would make mq-app-requester stream forever; refuse it at the CLI.
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu", "--count", "0"])
    assert result.exit_code == 2
    assert runner.recorded == []


def test_qm_e2e_config_error_exits_2_before_any_step(monkeypatch, tmp_path, prepare_lab_calls):
    _seed_e2e(monkeypatch, tmp_path, playbook=False)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu"])
    assert result.exit_code == 2
    assert "mqlab qm e2e nativeha-ubuntu: cannot read" in result.stderr
    assert runner.recorded == []
    assert prepare_lab_calls == []


def test_qm_e2e_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed_e2e(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nope"])
    assert result.exit_code == 2
    assert "no stack" in result.output


def test_qm_e2e_refuses_a_live_stack_without_record(monkeypatch, tmp_path):
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu"])
    assert result.exit_code == 2
    assert "nativeha-ubuntu is running without a version record" in result.stderr
    assert runner.recorded == []


def test_qm_e2e_runs_on_a_live_recorded_stack(monkeypatch, tmp_path):
    _seed_e2e(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    instances.write_record(InstanceRecord("nativeha-ubuntu", OsRef("ubuntu", 24), None, "t"))
    result = CliRunner().invoke(cli.app, ["qm", "e2e", "nativeha-ubuntu"])
    assert result.exit_code == 0, result.output
    assert runner.recorded[0].argv[:3] == ["vagrant", "ssh", "app-client"]
