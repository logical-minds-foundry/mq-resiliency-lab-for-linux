"""CLI tests for `mqlab dr cutover|failback` — the DR *operation* verb (#867, #1424).

Asserts the verb's argument/dispatch/env wiring at the unit level: it resolves the
stack, resolves the stack's own declared dr-cutover / dr-failback verb (kind `script` or
`playbook`, optional `rpo0: true`), refuses --rpo0-drill on a verb that does not declare
it, renders the inventory the verb's ansible reads, and runs it with the right direction,
QM, cwd, and (opt-in) drill signal — for rdqm-rhel, lab/scripts/rdqm-dr-cutover.sh with
the RPO0_DRILL env. Live cutover behaviour is out of scope here — the script's
own contract/parser tests live in tests/test_rdqm_dr_cutover.py, and a real seed→cut→
retrieve RPO-0 run is a deferred follow-on that needs the live rdqm DR arm up.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli, instances
from mqlab.instances import InstanceRecord
from mqlab.render import Renderer
from mqlab.stacks import lab_stacks
from mqlab.transcript import Transcript, transcript_path
from mqlab.versions import OsRef
from tests.fakes import RecordingRunner, ScriptedResult

REPO = Path(__file__).resolve().parent.parent


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("dr", "20260801T000000Z")),
        pauser=_NoPause(),
    )


# The live rdqm-rhel arm: one cluster group, a short (so QM names derive: RDQMAPP), and a
# data VIP. The DR script defaults are fine; the verb passes direction + stack.qm.qm_app.
_RDQM_TOPO = (
    "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
    "groups:\n  rdqm_a: [rdqm-a1]\n"
    "stacks:\n  rdqm-rhel:\n    mechanism: rdqm\n    os_family: rhel\n    short: RDQM\n"
    "    cluster_group: rdqm_a\n    groups: [rdqm_a]\n"
    "    qm: { vip: 10.10.1.100, vip_b: 10.10.2.100 }\n"
    "    verbs:\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "      dr-cutover: { script: rdqm-dr-cutover.sh, rpo0: true }\n"
    "      dr-failback: { script: rdqm-dr-cutover.sh, rpo0: true }\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)

# A stack (pacemaker-san) that has a QM but declares no dr-cutover / dr-failback verb.
_PCMK_TOPO = (
    "nodes:\n  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  pcmk_a: [pcmk-a1]\n"
    "stacks:\n  pcmk-ubuntu:\n    mechanism: pacemaker-san\n"
    "    os_family: ubuntu\n    short: PCMK\n"
    "    cluster_group: pcmk_a\n    groups: [pcmk_a]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10 }\n"
    "    verbs: { qm-status: { pcs: status resources } }\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
)


def _seed(monkeypatch, tmp_path, topo=_RDQM_TOPO):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)


def test_dr_cutover_shells_rdqm_script_a2b(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel"])
    assert result.exit_code == 0
    cmd = runner.recorded[-1]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("/lab/scripts/rdqm-dr-cutover.sh")
    # cutover -> a2b (site A -> B); the QM is the stack's short-derived app QM.
    assert cmd.argv[2:] == ["a2b", "RDQMAPP"]
    assert str(cmd.cwd).endswith("/ansible")
    # no drill unless asked -> no RPO0_DRILL env injected.
    assert cmd.env is None
    # the script's ansible calls read the rendered inventory.
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_dr_failback_shells_rdqm_script_b2a(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "failback", "rdqm-rhel"])
    assert result.exit_code == 0
    # failback -> b2a (site B -> A); the mirror direction.
    assert runner.recorded[-1].argv[2:] == ["b2a", "RDQMAPP"]


def test_dr_cutover_rpo0_drill_sets_env(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel", "--rpo0-drill"])
    assert result.exit_code == 0
    # --rpo0-drill flips the script's opt-in RPO-0 message-survival assertion on via env.
    assert runner.recorded[-1].env == {"RPO0_DRILL": "1"}


def test_dr_failback_rpo0_drill_sets_env(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "failback", "rdqm-rhel", "--rpo0-drill"])
    assert result.exit_code == 0
    assert runner.recorded[-1].env == {"RPO0_DRILL": "1"}


def test_dr_script_failure_propagates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=7)])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel"])
    # a non-zero script exit surfaces as the CLI's exit code — an RPO-0 violation
    # (the script's own `return 1`) fails the verb loud, never a silent pass.
    assert result.exit_code == 7


def test_dr_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "nope"])
    assert result.exit_code == 2
    assert "no stack" in result.output


@pytest.mark.parametrize("verb", ["cutover", "failback"])
def test_dr_stack_without_dr_verb_exits_2(monkeypatch, tmp_path, verb):
    _seed(monkeypatch, tmp_path, _PCMK_TOPO)
    result = CliRunner().invoke(cli.app, ["dr", verb, "pcmk-ubuntu"])
    assert result.exit_code == 2
    assert f"pcmk-ubuntu does not declare a dr-{verb} verb" in result.output


# --- per-stack DR verb dispatch (#1424, epic .github#310 Task 6a) ----------------------

_PDRBD_TOPO = (
    "nodes:\n  pdrbd-a1: {nics: {net-mgmt: 10.50.0.71}}\n"
    "groups:\n  pdrbd_a: [pdrbd-a1]\n"
    "stacks:\n  pcmk-drbd-ubuntu:\n    mechanism: pacemaker-drbd\n"
    "    os_family: ubuntu\n    short: PDRBD\n"
    "    cluster_group: pdrbd_a\n    groups: [pdrbd_a]\n"
    "    qm: { vip: 10.10.1.170, vip_b: 10.10.2.170 }\n"
    "    verbs:\n"
    "      dr-cutover: { playbook: site-pdrbd-dr-switch.yml, rpo0: true }\n"
    "      dr-failback: { playbook: site-pdrbd-dr-switch.yml, rpo0: true }\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
)

# A stack whose DR verb exists but does NOT implement the RPO-0 drill.
_NORPO_TOPO = _PDRBD_TOPO.replace(", rpo0: true", "")


def test_dr_cutover_runs_playbook_verb_with_direction(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PDRBD_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "pcmk-drbd-ubuntu"])
    assert result.exit_code == 0, result.output
    cmd = runner.recorded[-1]
    # the Task 6 contract: exactly these three extra-vars, run from ansible/.
    assert cmd.argv == [
        "ansible-playbook",
        "site-pdrbd-dr-switch.yml",
        "-e",
        "dr_direction=a2b",
        "-e",
        "qm_name=PDRBDAPP",
        "-e",
        "rpo0_drill=false",
    ]
    assert str(cmd.cwd).endswith("/ansible")
    assert cmd.env is None
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()


def test_dr_failback_playbook_rpo0_drill(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PDRBD_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "failback", "pcmk-drbd-ubuntu", "--rpo0-drill"])
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv[-4:] == ["-e", "qm_name=PDRBDAPP", "-e", "rpo0_drill=true"]
    assert "dr_direction=b2a" in runner.recorded[-1].argv


@pytest.mark.parametrize("verb", ["cutover", "failback"])
def test_dr_rpo0_drill_refused_when_verb_does_not_declare_it(monkeypatch, tmp_path, verb):
    _seed(monkeypatch, tmp_path, _NORPO_TOPO)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", verb, "pcmk-drbd-ubuntu", "--rpo0-drill"])
    assert result.exit_code == 2
    assert "does not implement the RPO-0 drill" in result.output
    # never cut over without the check the operator asked for.
    assert runner.recorded == []


def test_dr_without_drill_runs_on_a_verb_without_rpo0(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _NORPO_TOPO)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "pcmk-drbd-ubuntu"])
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv[-1] == "rpo0_drill=false"


def test_dr_unknown_verb_kind_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path, _PDRBD_TOPO.replace("playbook: site-pdrbd", "pcs: site-pdrbd"))
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "pcmk-drbd-ubuntu"])
    assert result.exit_code == 2
    assert "unsupported dr-cutover kind 'pcs'" in result.output
    assert runner.recorded == []


@pytest.mark.parametrize("verb", ["dr-cutover", "dr-failback"])
def test_real_topology_rdqm_declares_its_script_with_rpo0(verb):
    # The real lab/topology.yaml keeps rdqm-rhel on the exact pre-#1424 flow: the
    # rdqm-dr-cutover.sh script, which implements the RPO-0 drill.
    stack = lab_stacks()["rdqm-rhel"]
    assert stack.verbs[verb] == {"script": "rdqm-dr-cutover.sh", "rpo0": True}
    assert (REPO / "lab" / "scripts" / "rdqm-dr-cutover.sh").is_file()


@pytest.mark.parametrize("impl", ["{ rpo0: true }", "{ script: a.sh, playbook: b.yml }"])
def test_dr_verb_without_exactly_one_kind_exits_2(monkeypatch, tmp_path, impl):
    topo = _PDRBD_TOPO.replace("{ playbook: site-pdrbd-dr-switch.yml, rpo0: true }", impl)
    _seed(monkeypatch, tmp_path, topo)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "pcmk-drbd-ubuntu"])
    assert result.exit_code == 2
    assert "must declare exactly one of script / playbook" in result.output
    assert runner.recorded == []


def test_dr_no_subcommand_is_help(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["dr"])
    # no_args_is_help -> usage, non-zero, never a no-op success.
    assert result.exit_code != 0
    assert "cutover" in result.output and "failback" in result.output


# --- the instance-record gate (epic .github#280, T3) -----------------------------------


@pytest.mark.parametrize("verb", ["cutover", "failback"])
def test_dr_verb_refuses_a_live_stack_without_record(monkeypatch, tmp_path, verb):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    result = CliRunner().invoke(cli.app, ["dr", verb, "rdqm-rhel"])
    assert result.exit_code == 2
    assert (
        "rdqm-rhel is running without a version record; run `mqlab teardown rdqm-rhel` "
        "and re-bootstrap"
    ) in result.stderr
    assert runner.recorded == []  # the cutover script never ran


def test_dr_verb_runs_on_a_live_recorded_stack(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, ts: _deps(runner))
    monkeypatch.setattr(cli, "_stack_live", lambda name: True)
    instances.write_record(InstanceRecord("rdqm-rhel", OsRef("rhel", 9), None, "t"))
    result = CliRunner().invoke(cli.app, ["dr", "cutover", "rdqm-rhel"])
    assert result.exit_code == 0, result.output
    assert runner.recorded[-1].argv[2:] == ["a2b", "RDQMAPP"]
