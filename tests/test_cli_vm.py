from __future__ import annotations

import io

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.cli import _ensure_local_boxes as _real_ensure_local_boxes  # captured before the stub
from mqlab.cli import _sweep_orphan_volumes as _real_sweep_orphan_volumes
from mqlab.hostfacts import AARCH64, X86_64, HostFacts
from mqlab.pauser import NoTTYError
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]


@pytest.fixture(autouse=True)
def prereq_calls(monkeypatch):
    # The provision verbs ensure fresh-volume prerequisites (galaxy + MQ + PKI) via
    # _ensure_prereqs (#343). Stub it by default (so unit tests never fetch/run ansible)
    # and record the setups it was asked to ensure, for tests that assert the call.
    calls: list[str] = []
    monkeypatch.setattr(cli, "_ensure_prereqs", lambda setup, **k: calls.append(setup))
    return calls


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


def _probe(states):
    """A scripted `virsh list --all` result that puts each guest in a given state.

    `states` maps guest name -> virsh state string ("running", "shut off", …).
    Guests omitted entirely have no domain (absent).
    """
    lines = [" Id   Name              State", "------------------------------------"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def _argvs(runner):
    return [c.argv for c in runner.recorded]


# --- create: absent guests get vagrant up; existing guests are skipped with a note ---


def test_vm_create_runs_vagrant_up_only_for_absent_guests(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1", "rdqm-a1"])
    # rdqm-a1 already exists (running); node-a1 is absent.
    runner = RecordingRunner(results=[_probe({"rdqm-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],  # the state probe
        ["vagrant", "up", "node-a1"],  # only the absent guest
    ]
    assert all(str(c.cwd).endswith("/lab") for c in runner.recorded[1:])


def test_vm_create_starts_a_created_but_stopped_guest(monkeypatch, tmp_path):
    # create means "ensure created AND running": a created-but-stopped guest is
    # started, not left for a separate `vm up`. (#339)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "shut off"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "start", "lab_node-a1"],  # stopped -> bring it up
    ]


def test_vm_create_running_guest_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "running"})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]  # already running, nothing to do


def test_vm_create_by_setup_name_resolves_members_in_order(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  pcmk-a2: {}\n"
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1, pcmk-a2]\n"
        "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    )
    runner = RecordingRunner(results=[_probe({}), *(ScriptedResult([]) for _ in range(3))])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert [c.argv[-1] for c in runner.recorded[1:]] == ["san-a", "pcmk-a1", "pcmk-a2"]


# --- up: off guests get virsh start; running/absent are skipped with a note ---


def test_vm_up_runs_virsh_start_only_on_off_domains(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1", "pcmk-a2"])
    # pcmk-a1 is off (start it); pcmk-a2 already running (skip).
    runner = RecordingRunner(
        results=[_probe({"pcmk-a1": "shut off", "pcmk-a2": "running"}), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "pcmk"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "start", "lab_pcmk-a1"],
    ]


def test_vm_up_absent_guest_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[_probe({})])  # not created
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]  # no virsh start


def test_vm_up_without_pattern_is_a_usage_error():
    result = CliRunner().invoke(cli.app, ["vm", "up"])
    assert result.exit_code == 2


def test_vm_no_match_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "zzz"])
    assert result.exit_code == 2
    assert "no lab guest matches" in result.output


# --- down: running guests get virsh shutdown; off/absent are skipped ---


def test_vm_down_runs_virsh_shutdown_only_on_running(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "down", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "shutdown", "lab_node-a1"],
    ]


def test_vm_down_already_off_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "shut off"})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "down", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]


def test_vm_down_absent_guest_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({})])  # never created
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "down", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]


# --- destroy: off -> undefine; running -> force-off then undefine; absent -> skip ---


def test_vm_destroy_off_guest_undefines_with_storage_and_nvram(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "shut off"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "undefine", "lab_node-a1", "--remove-all-storage", "--nvram"],
    ]


def test_vm_destroy_running_guest_force_offs_then_undefines(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(
        results=[_probe({"node-a1": "running"}), ScriptedResult([]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "destroy", "lab_node-a1"],  # force off first
        [*_VIRSH, "undefine", "lab_node-a1", "--remove-all-storage", "--nvram"],
    ]


def test_vm_destroy_paused_guest_force_offs_then_undefines(monkeypatch, tmp_path):
    # A paused domain still holds a live qemu process, so it must be force-off'd
    # before undefine --remove-all-storage (which needs a stopped domain). (#339)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(
        results=[_probe({"node-a1": "paused"}), ScriptedResult([]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [
        [*_VIRSH, "list", "--all"],
        [*_VIRSH, "destroy", "lab_node-a1"],  # paused -> force off first
        [*_VIRSH, "undefine", "lab_node-a1", "--remove-all-storage", "--nvram"],
    ]


def test_vm_destroy_absent_guest_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]


def test_vm_action_step_failure_propagates_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    # Probe says off; the undefine action then fails -> exit code propagates.
    runner = RecordingRunner(
        results=[_probe({"node-a1": "shut off"}), ScriptedResult([], exit_code=1)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "all"])
    assert result.exit_code == 1


# --- status (pass-through; not state-planned) ---


def test_vm_status_reads_virsh_and_joins_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[ScriptedResult([" -  lab_pcmk-a1  shut off"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv == [*_VIRSH, "list", "--all"]


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
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
        "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    )
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert runner.recorded[0].argv[-1] == "--all"  # still virsh list --all (the source)


def test_vm_inventory_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "setups:\n  pcmk_san_ha: {groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "inventory"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "work" / "inventory.ini").read_text()
    assert "[san_a]" in written
    assert "san-a ansible_host=10.50.0.5" in written
    assert "[pcmk_san_ha:children]" in written


def test_vm_roster_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n"
        "groups:\n  san_a: [san-a]\n"
        "setups:\n  pcmk_san_ha: {groups: [san_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "roster"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "work" / "salt" / "roster").read_text()
    assert "san-a:" in written
    assert "host: 10.50.0.5" in written
    assert "roster_groups:\n      - san_a" in written
    assert "roster_setups:\n      - pcmk_san_ha" in written


def test_vm_up_step_without_tty_exits_two(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1", "node-a2"])  # 2 start steps -> pause after step 1
    runner = RecordingRunner(
        results=[
            _probe({"node-a1": "shut off", "node-a2": "shut off"}),
            ScriptedResult([]),
            ScriptedResult([]),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _FailPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "all", "--step"])
    assert result.exit_code == 2


def test_advisory_notes_name_fully_qualified_mqlab_commands():
    # mqlab's own guidance points at the wrapper's own commands, fully qualified
    # with the `mqlab` prefix — never bare subcommands or the raw virsh/vagrant
    # invocation. This keeps mqlab's vocabulary distinct from the wrapped tool's.
    _, create_notes = cli._plan_create(["g"], {"lab_g": "running"})  # already running
    assert any("mqlab vm destroy" in n for n in create_notes)
    _, up_notes = cli._plan_up(["g"], {})  # absent -> guide to create
    assert any("mqlab vm create" in n for n in up_notes)


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


_PCMK_TOPO = (
    "nodes:\n  san-a: {nics: {net-mgmt: 10.50.0.5}}\n  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n"
    "setups:\n  pcmk_san_ha:\n    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n    secrets: [pcmk_hacluster_password]\n"
)


def test_vm_provision_sources_secret_renders_inventory_runs_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult(["s3cr3t"]),  # lab-secret.sh
            ScriptedResult([]),  # ansible-playbook
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == [*_VIRSH, "list", "--all"]
    assert argvs[1][0] == "bash" and argvs[1][2] == "pcmk_hacluster_password"
    assert "lab-secret.sh" in argvs[1][1]
    play = runner.recorded[-1]
    assert play.argv == ["ansible-playbook", "site-pcmk.yml"]
    assert str(play.cwd).endswith("/ansible")
    assert play.env == {"PCMK_HACLUSTER_PASSWORD": "s3cr3t"}  # secret injected on the subprocess
    assert (tmp_path / "build" / "work" / "inventory.ini").read_text().startswith("[san_a]")


def test_vm_provision_ensures_prereqs(monkeypatch, tmp_path, prereq_calls):
    # provision ensures the setup's fresh-volume prerequisites before the playbook (#343).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult(["s3cr3t"]),  # lab-secret.sh
            ScriptedResult([]),  # ansible-playbook
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 0
    assert "pcmk_san_ha" in prereq_calls


def test_vm_provision_members_down_advises_and_exits_3(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(results=[_probe({"san-a": "running"})])  # pcmk-a1 not created
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 3
    assert [c.argv for c in runner.recorded] == [[*_VIRSH, "list", "--all"]]  # probe only


def test_vm_provision_no_secret_setup_runs_playbook_without_sourcing(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31}}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "setups:\n  rdqm_ha:\n    groups: [rdqm_a]\n    provision: ansible/site-rdqm.yml\n"
    )
    runner = RecordingRunner(results=[_probe({"rdqm-a1": "running"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "rdqm_ha"])
    assert result.exit_code == 0
    argvs = [c.argv for c in runner.recorded]
    assert argvs == [[*_VIRSH, "list", "--all"], ["ansible-playbook", "site-rdqm.yml"]]
    assert runner.recorded[-1].env is None  # no secrets -> no injected env


def test_vm_provision_unknown_setup_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text("setups: {}\n")
    result = CliRunner().invoke(cli.app, ["vm", "provision", "nope"])
    assert result.exit_code == 2
    assert "no lab setup" in result.output


def test_vm_provision_setup_without_playbook_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "groups:\n  g: [h]\nsetups:\n  bare:\n    groups: [g]\n"
    )
    result = CliRunner().invoke(cli.app, ["vm", "provision", "bare"])
    assert result.exit_code == 2
    assert "no provision playbook" in result.output


def test_vm_provision_lab_secret_failure_exits_2(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult([], exit_code=1),
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 2


def test_vm_provision_playbook_failure_propagates_exit_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_PCMK_TOPO)
    runner = RecordingRunner(
        results=[
            _probe({"san-a": "running", "pcmk-a1": "running"}),
            ScriptedResult(["s"]),
            ScriptedResult([], exit_code=4),  # ansible fails
        ]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "provision", "pcmk_san_ha"])
    assert result.exit_code == 4


# --- host-arch gating (#276): only the vagrant-loading verbs run _prepare_lab ---
def test_vm_up_runs_prepare_lab(monkeypatch, tmp_path, prepare_lab_calls):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[_probe({"pcmk-a1": "shut off"}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "up", "pcmk-a1"])
    assert result.exit_code == 0
    assert prepare_lab_calls == ["prepare"]  # the vagrant-loading verb gated


def test_vm_status_does_not_gate(monkeypatch, tmp_path, prepare_lab_calls):
    # status shells virsh, not vagrant: it must survive without KVM / a resolved file,
    # so it must NOT run _prepare_lab (a regression guard, #276).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["pcmk-a1"])
    runner = RecordingRunner(results=[ScriptedResult([" -  lab_pcmk-a1  shut off"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "status"])
    assert result.exit_code == 0
    assert prepare_lab_calls == []


def test_fetch_mq_tarball_delegates_to_download(monkeypatch, tmp_path):
    # the manifest fetch callback now auto-downloads (no-auth CDN) instead of raising (#276)
    calls = {}
    monkeypatch.setattr(
        cli, "download_mq_tarball", lambda name, dest: calls.update(name=name, dest=dest)
    )
    name = "9.4.5.0-IBM-MQ-Advanced-for-Developers-UbuntuLinuxARM64.tar.gz"
    cli._fetch_mq_tarball(name, tmp_path / "t")
    assert calls == {"name": name, "dest": tmp_path / "t"}


# --- local box auto-build (#276/#291): build/register the RHEL box on a fresh box ---
def test_parse_box_list():
    txt = "rhel/9.6-x86_64          (libvirt, 0, (arm64))\ncloud-image/ubuntu-24.04 (libvirt, 1)\n"
    assert cli.parse_box_list(txt) == {
        "rhel/9.6-x86_64": "(libvirt, 0, (arm64))",
        "cloud-image/ubuntu-24.04": "(libvirt, 1)",
    }


def test_parse_box_list_no_boxes():
    assert cli.parse_box_list("There are no installed boxes!\n") == {}


def _seed_resolved(tmp_path, body):
    (tmp_path / "build" / "work" / "lab").mkdir(parents=True)
    (tmp_path / "build" / "work" / "lab" / "topology.resolved.yaml").write_text(body)


def test_box_build_steps_passes_kvm_args(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert [s.command.argv for s in steps] == [
        [
            "bash",
            str(tmp_path / "lab/boxes/rhel96/build-box.sh"),
            "--domain-type",
            "kvm",
            "--cpu-mode",
            "host-passthrough",
        ],
    ]


def test_box_build_steps_passes_tcg_args_on_arm(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    facts = HostFacts(arch=AARCH64, kvm=True, distro_family="apt", in_vergil=True)
    steps = cli._box_build_steps({"rhel/9.6-x86_64": "lab/boxes/rhel96/build-box.sh"}, {}, facts)
    assert steps[0].command.argv[-4:] == ["--domain-type", "qemu", "--cpu-mode", "maximum"]


def test_ensure_local_boxes_builds_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64}\n")
    monkeypatch.setattr(
        cli, "probe", lambda: HostFacts(arch=X86_64, kvm=True, distro_family="dnf", in_vergil=True)
    )
    runner = RecordingRunner(
        results=[ScriptedResult(["There are no installed boxes!"]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["rdqm-a1"])
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == ["vagrant", "box", "list"]
    assert argvs[1] == [
        "bash",
        str(tmp_path / "lab/boxes/rhel96/build-box.sh"),
        "--domain-type",
        "kvm",
        "--cpu-mode",
        "host-passthrough",
    ]


def test_ensure_local_boxes_noop_when_box_present(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64}\n")
    runner = RecordingRunner(results=[ScriptedResult(["rhel/9.6-x86_64  (libvirt, 0)"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["rdqm-a1"])
    assert [c.argv for c in runner.recorded] == [["vagrant", "box", "list"]]  # probe only, no build


def test_ensure_local_boxes_noop_when_no_local_box(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  obs: {box: cloud-image/ubuntu-24.04}\n")
    _real_ensure_local_boxes(["obs"])  # no local box needed -> returns before any command


def test_ensure_local_boxes_build_failure_exits(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64}\n")
    runner = RecordingRunner(
        results=[ScriptedResult(["There are no installed boxes!"]), ScriptedResult([], exit_code=1)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    with pytest.raises(typer.Exit):
        _real_ensure_local_boxes(["rdqm-a1"])


def test_vm_create_runs_ensure_local_boxes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["rdqm-a1"])
    called = {}
    monkeypatch.setattr(
        cli, "_ensure_local_boxes", lambda guests: called.setdefault("guests", guests)
    )
    runner = RecordingRunner(results=[_probe({}), ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "rdqm-a1"])
    assert result.exit_code == 0
    assert called["guests"] == ["rdqm-a1"]


def test_guests_need_dvd(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(
        tmp_path,
        "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64, dvd: /pool/rhel.iso}\n"
        "  obs: {box: cloud-image/ubuntu-24.04}\n",
    )
    assert cli._guests_need_dvd(["rdqm-a1"]) is True
    assert cli._guests_need_dvd(["obs"]) is False


def test_ensure_local_boxes_stages_dvd_when_box_present(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_resolved(tmp_path, "nodes:\n  rdqm-a1: {box: rhel/9.6-x86_64, dvd: /pool/rhel.iso}\n")
    # box already installed -> no build; but the DVD must still be staged
    runner = RecordingRunner(
        results=[ScriptedResult(["rhel/9.6-x86_64  (libvirt, 0)"]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["rdqm-a1"])
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == ["vagrant", "box", "list"]
    assert argvs[1] == ["bash", str(tmp_path / "lab/scripts/stage-rhel-iso.sh")]


def test_ensure_local_boxes_dvd_only_skips_box_probe(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # a (hypothetical) cloud box with a dvd -> no local box, but the dvd staging runs
    _seed_resolved(tmp_path, "nodes:\n  n1: {box: cloud-image/ubuntu-24.04, dvd: /pool/x.iso}\n")
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_ensure_local_boxes(["n1"])
    assert [c.argv for c in runner.recorded] == [
        ["bash", str(tmp_path / "lab/scripts/stage-rhel-iso.sh")]
    ]  # no `vagrant box list` probe


# --- destroy sweeps orphaned volumes from partial-failed creates (#276) ---
_VOLS = """\
 Name                      Path
------------------------------------------
 lab_rdqm-a1-vdb.qcow2     /var/lib/libvirt/images/lab_rdqm-a1-vdb.qcow2
 lab_rdqm-a1.img           /var/lib/libvirt/images/lab_rdqm-a1.img
 lab_rdqm-a10-vdb.qcow2    /var/lib/libvirt/images/lab_rdqm-a10-vdb.qcow2
 rhel-9.6-x86_64-dvd.iso   /var/lib/libvirt/images/rhel-9.6-x86_64-dvd.iso
"""


def test_parse_vol_list_skips_chrome():
    vols = cli.parse_vol_list(_VOLS)
    assert "lab_rdqm-a1-vdb.qcow2" in vols
    assert "Name" not in vols and "------" not in "".join(vols)


def test_orphan_volumes_matches_guest_not_prefix_collision():
    vols = cli.parse_vol_list(_VOLS)
    # rdqm-a1 owns its .img and -vdb, but NOT rdqm-a10's volume (prefix-collision guard)
    assert sorted(cli._orphan_volumes(["rdqm-a1"], vols)) == [
        "lab_rdqm-a1-vdb.qcow2",
        "lab_rdqm-a1.img",
    ]


def test_sweep_orphan_volumes_deletes_matches(monkeypatch):
    # probe + two deletes (the -vdb and the .img both belong to rdqm-a1)
    runner = RecordingRunner(
        results=[ScriptedResult(_VOLS.splitlines()), ScriptedResult([]), ScriptedResult([])]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_sweep_orphan_volumes(["rdqm-a1"])
    argvs = [c.argv for c in runner.recorded]
    assert argvs[0] == [*_VIRSH, "vol-list", "default"]
    assert [*_VIRSH, "vol-delete", "--pool", "default", "lab_rdqm-a1-vdb.qcow2"] in argvs


def test_sweep_orphan_volumes_noop_when_none(monkeypatch):
    runner = RecordingRunner(results=[ScriptedResult([" rhel-9.6-x86_64-dvd.iso  /var/x.iso"])])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    _real_sweep_orphan_volumes(["rdqm-a1"])  # no lab_rdqm-a1 volumes -> only the probe
    assert [c.argv for c in runner.recorded] == [[*_VIRSH, "vol-list", "default"]]


def test_sweep_orphan_volumes_delete_failure_exits(monkeypatch):
    runner = RecordingRunner(
        results=[ScriptedResult(_VOLS.splitlines()), ScriptedResult([], exit_code=1)]
    )
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    with pytest.raises(typer.Exit):
        _real_sweep_orphan_volumes(["rdqm-a1"])


def test_vm_destroy_runs_sweep(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["rdqm-a1"])
    called = {}
    monkeypatch.setattr(cli, "_sweep_orphan_volumes", lambda guests: called.setdefault("g", guests))
    runner = RecordingRunner(results=[_probe({})])  # absent -> no undefine steps
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "destroy", "rdqm-a1"])
    assert result.exit_code == 0
    assert called["g"] == ["rdqm-a1"]
