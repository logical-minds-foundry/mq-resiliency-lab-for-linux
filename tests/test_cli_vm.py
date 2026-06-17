from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.pauser import NoTTYError
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

_VIRSH = ["virsh", "-c", "qemu:///system"]


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


def test_vm_create_all_already_created_is_a_no_op(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_topology(tmp_path, ["node-a1"])
    runner = RecordingRunner(results=[_probe({"node-a1": "shut off"})])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "create", "all"])
    assert result.exit_code == 0
    assert _argvs(runner) == [[*_VIRSH, "list", "--all"]]  # probe only, no vagrant up


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
    written = (tmp_path / "build" / "inventory.ini").read_text()
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
    written = (tmp_path / "build" / "salt" / "roster").read_text()
    assert "san-a:" in written
    assert "host: 10.50.0.5" in written
    assert "roster_groups:\n      - san_a" in written
    assert "roster_setups:\n      - pcmk_san_ha" in written


def test_vm_hosts_writes_and_echoes(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  rdqm-a1: {nics: {net-mgmt: 10.50.0.31, net-wan: 10.99.0.31}}\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "setups:\n  rdqm_dr: {groups: [rdqm_a]}\n"
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda verb, ts: _deps(runner, _NoPause()))
    result = CliRunner().invoke(cli.app, ["vm", "hosts"])
    assert result.exit_code == 0
    written = (tmp_path / "build" / "hosts").read_text()
    assert "10.50.0.31 rdqm-a1 rdqm-a1-mgmt" in written  # bare name + mgmt alias
    assert "10.99.0.31 rdqm-a1-wan" in written  # per-plane alias


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
    _, create_notes = cli._plan_create(["g"], {"lab_g": "running"})  # already exists
    assert any("mqlab vm up" in n and "mqlab vm destroy" in n for n in create_notes)
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
    assert (tmp_path / "build" / "inventory.ini").read_text().startswith("[san_a]")
    assert "san-a san-a-mgmt" in (tmp_path / "build" / "hosts").read_text()  # hosts rendered too


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
