"""Tests for the #350 stack-based `teardown` command (Task 6).

`teardown <stack>` destroys a stack's member VMs. Shared commons VMs
(obs/mon-probe) are torn down only when the last stack goes down — or when
the operator forces it with --commons.

`_other_stacks_up(deps, exclude)` is the reference-count helper: it probes
all stacks OTHER than `exclude` and returns True if any live member is found.
"""

from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

# Seeded topology: same structure as test_cli_bootstrap.py PLUS an rdqm-rhel stack
# and a corresponding rdqm_a group + nodes. Both stacks have cluster_group set so
# neither is "reserved" in the _other_stacks_up sense.
# commons includes svc+app so all_vms covers the shared distributed-path VMs.
TOPO = (
    "nodes:\n"
    "  san-a: {}\n"
    "  pcmk-a1: {}\n"
    "  pcmk-a2: {}\n"
    "  pcmk-a3: {}\n"
    "  pcmk-b1: {}\n"
    "  rdqm-a1: {}\n"
    "  rdqm-a2: {}\n"
    "  rdqm-a3: {}\n"
    "  obs: {}\n"
    "  mon-probe: {}\n"
    "  svc-sim: {}\n"
    "  app-client: {}\n"
    "groups:\n"
    "  san_a:   [san-a]\n"
    "  pcmk_a:  [pcmk-a1, pcmk-a2, pcmk-a3]\n"
    "  pcmk_b:  [pcmk-b1]\n"
    "  rdqm_a:  [rdqm-a1, rdqm-a2, rdqm-a3]\n"
    "  obs_box: [obs]\n"
    "  probe:   [mon-probe]\n"
    "  svc:     [svc-sim]\n"
    "  app:     [app-client]\n"
    "stacks:\n"
    "  pcmk-ubuntu:\n"
    "    mechanism: pacemaker-san\n"
    "    os: ubuntu\n"
    "    short: PCMK\n"
    "    cluster_group: pcmk_a\n"
    "    groups: [san_a, pcmk_a, pcmk_b]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    secrets: [pcmk_hacluster_password, mqweb_admin_password]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9157\n"
    "      exporter_svc_port: 9158\n"
    "      app_unit: app-pcmk\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-status:  { pcs: 'status resources' }\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n"
    "    os: rhel\n"
    "    short: RDQM\n"
    "    cluster_group: rdqm_a\n"
    "    groups: [rdqm_a]\n"
    "    provision: ansible/site-rdqm.yml\n"
    "    secrets: [mqweb_admin_password]\n"
    "    qm: { vip: 10.10.2.200, vip_ext: 10.60.0.20, svc_conn: 10.60.0.60 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9159\n"
    "      exporter_svc_port: 9160\n"
    "      app_unit: app-rdqm\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-status:  { cmd: 'dspmq -m {qm}' }\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    "commons:\n"
    "  groups: [obs_box, probe, svc, app]\n"
    "  provision: ansible/site-obs.yml\n"
)

NET_XML = "<network><name>{name}</name></network>\n"


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    lab = tmp_path / "lab"
    nets = lab / "networks"
    nets.mkdir(parents=True)
    (lab / "topology.yaml").write_text(TOPO)
    for n in ("net-mgmt", "net-data-a"):
        (nets / f"{n}.xml").write_text(NET_XML.format(name=n))


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("teardown", "20260627T000000Z")),
        pauser=_NoPause(),
    )


class _NoPause:
    def wait(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Helper: capture the CommandStep objects run_steps receives
# --------------------------------------------------------------------------- #
def _capture_steps(monkeypatch):
    """Monkeypatch cli.run_steps to capture the steps list without executing it."""
    captured: list = []
    monkeypatch.setattr(cli, "run_steps", lambda steps, **kwargs: captured.extend(steps))
    return captured


# --------------------------------------------------------------------------- #
# Integration tests: teardown command via CliRunner
# --------------------------------------------------------------------------- #
def _stub_probe_states(monkeypatch, domain_states=None):
    """Stub _probe_states to return a fixed domain-states dict (no subprocess call).

    Defaults to showing all topology VMs as running so _plan_destroy emits real
    destroy steps (rather than "already gone" notes for absent domains).
    """
    if domain_states is None:
        # All VMs running — _plan_destroy will plan force-off + undefine for each.
        vms = [
            "san-a",
            "pcmk-a1",
            "pcmk-a2",
            "pcmk-a3",
            "pcmk-b1",
            "rdqm-a1",
            "rdqm-a2",
            "rdqm-a3",
            "obs",
            "mon-probe",
            "svc-sim",
            "app-client",
        ]
        domain_states = {f"lab_{v}": "running" for v in vms}
    monkeypatch.setattr(cli, "_probe_states", lambda deps: domain_states)


def test_teardown_keeps_commons_when_other_stack_up(monkeypatch, tmp_path):
    """When another stack is still up, commons VMs are NOT destroyed (no --commons)."""
    _seed(monkeypatch, tmp_path)
    _stub_probe_states(monkeypatch)
    # Another stack is live -> reference count > 0 -> keep commons
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[])))
    captured = _capture_steps(monkeypatch)

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel"])

    assert result.exit_code == 0
    labels = [s.label for s in captured]
    # No step should carry BOTH "commons" and "destroy" in its label
    commons_destroy = [lbl for lbl in labels if "commons" in lbl and "destroy" in lbl]
    assert not commons_destroy, f"expected no commons-destroy steps, got: {commons_destroy}"


def test_teardown_commons_flag_forces_commons_destroy(monkeypatch, tmp_path):
    """--commons forces commons destruction even when another stack is still up."""
    _seed(monkeypatch, tmp_path)
    _stub_probe_states(monkeypatch)
    # Another stack is live, but --commons overrides the reference count
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[])))
    captured = _capture_steps(monkeypatch)

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel", "--commons"])

    assert result.exit_code == 0
    labels = [s.label for s in captured]
    # Every commons VM must get a "commons destroy:" step (the prefix distinguishes
    # them from stack-member steps); rdqm-rhel's own members are torn down too.
    for host in ("obs", "mon-probe", "svc-sim", "app-client"):
        assert any(lbl.startswith("commons destroy:") and host in lbl for lbl in labels), (
            f"commons VM {host!r} not destroyed under --commons; labels: {labels}"
        )


def test_teardown_last_one_out_destroys_commons(monkeypatch, tmp_path):
    """When no other stack is up, commons VMs are automatically destroyed."""
    _seed(monkeypatch, tmp_path)
    _stub_probe_states(monkeypatch)
    # No other stack running -> last one out -> auto-destroy commons
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: False)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[])))
    captured = _capture_steps(monkeypatch)

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel"])

    assert result.exit_code == 0
    labels = [s.label for s in captured]
    # At least one step must carry BOTH "commons" and "destroy"
    commons_destroy = [lbl for lbl in labels if "commons" in lbl and "destroy" in lbl]
    assert commons_destroy, f"expected commons-destroy steps when last stack out, got: {labels}"


def test_teardown_unknown_stack_exits_2(monkeypatch, tmp_path):
    """An unrecognised stack name must exit 2 without running any steps."""
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["teardown", "bogus-stack"])
    assert result.exit_code == 2
    assert "bogus-stack" in result.output


# --------------------------------------------------------------------------- #
# Unit tests: _other_stacks_up helper
# --------------------------------------------------------------------------- #
def _dom_listing(states):
    lines = [" Id   Name              State", "------------------------------------"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def test_other_stacks_up_returns_true_when_another_live(monkeypatch, tmp_path):
    """Returns True when a member VM of another stack is running."""
    _seed(monkeypatch, tmp_path)
    # pcmk-ubuntu has a running member; we exclude rdqm-rhel, so pcmk-ubuntu is probed
    runner = RecordingRunner(results=[_dom_listing({"pcmk-a1": "running"})])
    deps = _deps(runner)
    result = cli._other_stacks_up(deps, exclude="rdqm-rhel")
    assert result is True


def test_other_stacks_up_returns_false_when_all_others_absent(monkeypatch, tmp_path):
    """Returns False when all members of all other stacks are absent (empty domain list)."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[_dom_listing({})])
    deps = _deps(runner)
    result = cli._other_stacks_up(deps, exclude="rdqm-rhel")
    assert result is False


def test_other_stacks_up_skips_reserved_stack_no_cluster_group(monkeypatch, tmp_path):
    """A stack with no cluster_group is treated as reserved and skipped."""
    # Topology with a reserved stack (no cluster_group) and a real stack.
    # Excluding the real stack -> only the reserved one remains -> skip it -> False.
    topo = (
        "nodes:\n"
        "  rdqm-a1: {}\n"
        "  rdqm-a2: {}\n"
        "  rdqm-a3: {}\n"
        "groups:\n"
        "  rdqm_a: [rdqm-a1, rdqm-a2, rdqm-a3]\n"
        "stacks:\n"
        "  rdqm-rhel:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: RDQM\n"
        "    cluster_group: rdqm_a\n"
        "    groups: [rdqm_a]\n"
        "    provision: ansible/site-rdqm.yml\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
        "  nativeha-ubuntu:\n"
        "    mechanism: native-ha\n"
        "    os: ubuntu\n"
        "    short: NHAU\n"
        "    groups: []\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
        "commons:\n"
        "  groups: []\n"
    )
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    runner = RecordingRunner(results=[_dom_listing({"rdqm-a1": "running"})])
    deps = _deps(runner)
    # Exclude rdqm-rhel -> only nativeha-ubuntu remains (no cluster_group) -> skip -> False
    result = cli._other_stacks_up(deps, exclude="rdqm-rhel")
    assert result is False


def test_other_stacks_up_skips_stack_with_empty_members(monkeypatch, tmp_path):
    """A stack with cluster_group but empty groups (no hosts) is skipped."""
    # Topology with a stack that has cluster_group set but groups: [] -> no members.
    topo = (
        "nodes:\n"
        "  rdqm-a1: {}\n"
        "groups:\n"
        "  rdqm_a: [rdqm-a1]\n"
        "stacks:\n"
        "  rdqm-rhel:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: RDQM\n"
        "    cluster_group: rdqm_a\n"
        "    groups: [rdqm_a]\n"
        "    provision: ansible/site-rdqm.yml\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
        "  empty-stack:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: EMPT\n"
        "    cluster_group: rdqm_a\n"
        "    groups: []\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
        "commons:\n"
        "  groups: []\n"
    )
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    runner = RecordingRunner(results=[_dom_listing({"rdqm-a1": "running"})])
    deps = _deps(runner)
    # Exclude rdqm-rhel -> only empty-stack remains (groups: [] -> no members) -> skip -> False
    result = cli._other_stacks_up(deps, exclude="rdqm-rhel")
    assert result is False


def test_teardown_absent_vms_emit_notes(monkeypatch, tmp_path):
    """When VMs are absent, _plan_destroy emits notes (exercises the notes loop)."""
    _seed(monkeypatch, tmp_path)
    # All domains absent: _plan_destroy will produce notes, not steps
    _stub_probe_states(monkeypatch, domain_states={})
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: False)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[])))
    captured = _capture_steps(monkeypatch)

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel"])

    assert result.exit_code == 0
    # All VMs absent -> no destroy steps, only notes emitted (no steps to capture)
    assert captured == []


class _FailRunner:
    """Runner that always returns a non-zero exit code to trigger StepFailedError."""

    def run(self, command, on_line):  # noqa: ARG002
        return 1


class _NoTTYPauser:
    def wait(self) -> None:
        from mqlab.pauser import NoTTYError

        raise NoTTYError("no tty for --step")


def _deps_with_runner(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("teardown", "20260627T000000Z")),
        pauser=_NoPause(),
    )


def test_teardown_step_failure_exits_with_step_exit_code(monkeypatch, tmp_path):
    """A step failure (StepFailedError) propagates the step's exit code."""
    _seed(monkeypatch, tmp_path)
    # Members are running so _plan_destroy emits real steps; the runner fails them.
    _stub_probe_states(monkeypatch)
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps_with_runner(_FailRunner()))

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel"])

    # _FailRunner returns exit 1 -> StepFailedError -> typer.Exit(1)
    assert result.exit_code == 1


def test_teardown_no_tty_under_step_exits_2(monkeypatch, tmp_path):
    """A NoTTYError (--step with no terminal) exits with code 2."""
    _seed(monkeypatch, tmp_path)
    _stub_probe_states(monkeypatch)
    monkeypatch.setattr(cli, "_other_stacks_up", lambda deps, exclude: True)

    def _make_deps(v, t):
        return cli.Deps(
            runner=RecordingRunner(results=[ScriptedResult([]) for _ in range(10)]),
            renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
            transcript=Transcript(transcript_path("teardown", "20260627T000000Z")),
            pauser=_NoTTYPauser(),
        )

    monkeypatch.setattr(cli, "build_deps", _make_deps)

    result = CliRunner().invoke(cli.app, ["teardown", "rdqm-rhel", "--step"])

    assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# #636: teardown forgets Vagrant's per-machine metadata so a stale box_meta
# cannot shadow the fat box on the next `vagrant up`.
# --------------------------------------------------------------------------- #
def _make_machine_dir(tmp_path, guest: str):
    """Create a fake Vagrant per-machine data dir (with a box_meta) for `guest`."""
    d = tmp_path / "build" / "state" / "vagrant" / "machines" / guest / "libvirt"
    d.mkdir(parents=True)
    (d / "box_meta").write_text('{"name":"cloud-image/ubuntu-24.04"}')
    return d


def test_vagrant_machine_dir_points_under_shared_state(monkeypatch, tmp_path):
    """_vagrant_machine_dir resolves to build/state/vagrant/machines/<guest>."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    assert cli._vagrant_machine_dir("rdqm-a1") == (
        tmp_path / "build" / "state" / "vagrant" / "machines" / "rdqm-a1"
    )


def test_forget_machine_step_removes_the_machine_dir(monkeypatch, tmp_path):
    """_forget_machine_step builds an `rm -rf <machine dir>` command."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    step = cli._forget_machine_step("rdqm-a1")
    machine_dir = str(tmp_path / "build" / "state" / "vagrant" / "machines" / "rdqm-a1")
    assert step.command.argv == ["rm", "-rf", machine_dir]
    assert step.label == "rdqm-a1 forget vagrant machine"


def test_plan_destroy_forgets_metadata_for_running_guest(monkeypatch, tmp_path):
    """A running guest whose metadata survives gets force-off + undefine + forget."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _make_machine_dir(tmp_path, "rdqm-a1")
    steps, notes = cli._plan_destroy(["rdqm-a1"], {"lab_rdqm-a1": "running"})
    assert [s.label for s in steps] == [
        "rdqm-a1 force-off",
        "rdqm-a1 undefine",
        "rdqm-a1 forget vagrant machine",
    ]
    assert notes == []


def test_plan_destroy_forgets_metadata_for_shutoff_guest(monkeypatch, tmp_path):
    """A shut-off guest is undefined directly, then its metadata is forgotten."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _make_machine_dir(tmp_path, "rdqm-a1")
    steps, _ = cli._plan_destroy(["rdqm-a1"], {"lab_rdqm-a1": "shut off"})
    assert [s.label for s in steps] == [
        "rdqm-a1 undefine",
        "rdqm-a1 forget vagrant machine",
    ]


def test_plan_destroy_forgets_stale_metadata_of_absent_domain(monkeypatch, tmp_path):
    """The #636 bug scenario: the domain is already gone but its box_meta lingers.
    teardown must still forget it (a note AND a forget step, no destroy steps)."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _make_machine_dir(tmp_path, "rdqm-a1")
    steps, notes = cli._plan_destroy(["rdqm-a1"], {})
    assert [s.label for s in steps] == ["rdqm-a1 forget vagrant machine"]
    assert notes == ["rdqm-a1: already gone"]


def test_plan_destroy_no_forget_step_when_no_metadata(monkeypatch, tmp_path):
    """A running guest with no surviving metadata gets no forget step (quiet path)."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    steps, _ = cli._plan_destroy(["rdqm-a1"], {"lab_rdqm-a1": "running"})
    assert [s.label for s in steps] == ["rdqm-a1 force-off", "rdqm-a1 undefine"]
