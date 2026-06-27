"""Tests for the #350 stack-based `bootstrap` phase sequencer (Task 4).

`bootstrap <stack>` runs the four bring-up phases (net -> vms -> provision ->
observe) from the first unsatisfied one, so a re-run resumes. These tests MOCK
`cli._probe_all` (the live state-gathering seam) and drive the sequencer with a
RecordingRunner; a separate block unit-tests `_probe_all` itself by scripting the
runner that probes virsh / the qm-status verb / Prometheus.
"""

from __future__ import annotations

import io
import json

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.phases import build_states
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

# A seeded topology mirroring tests/test_phases.py: nodes + groups + a stacks:
# block + commons + two lab networks (for the net phase to enumerate).
TOPO = (
    "nodes:\n"
    "  san-a: {}\n"
    "  pcmk-a1: {}\n"
    "  pcmk-a2: {}\n"
    "  pcmk-a3: {}\n"
    "  pcmk-b1: {}\n"
    "  obs: {}\n"
    "  mon-probe: {}\n"
    "groups:\n"
    "  san_a:   [san-a]\n"
    "  pcmk_a:  [pcmk-a1, pcmk-a2, pcmk-a3]\n"
    "  pcmk_b:  [pcmk-b1]\n"
    "  obs_box: [obs]\n"
    "  probe:   [mon-probe]\n"
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
    "commons:\n"
    "  groups: [obs_box, probe]\n"
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


def _states(*, net=True, vms=True, provision=False, observe=False):
    nets = {"net-mgmt": "active", "net-data-a": "active"} if net else {}
    guests = ["san-a", "pcmk-a1", "pcmk-a2", "pcmk-a3", "pcmk-b1", "obs", "mon-probe"]
    domains = {f"lab_{g}": "running" for g in guests} if vms else {}
    return build_states(nets=nets, domains=domains, qm_up=provision, observe=observe)


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("bootstrap", "20260627T000000Z")),
        pauser=_NoPause(),
    )


class _NoPause:
    def wait(self) -> None:
        return None


class _FailPause:
    def wait(self) -> None:
        from mqlab.pauser import NoTTYError

        raise NoTTYError("no tty for --step")


def _deps_pauser(runner, pauser):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("bootstrap", "20260627T000000Z")),
        pauser=pauser,
    )


def _recorded_steps(runner):
    """The CommandSteps the sequencer ran, reconstructed from recorded commands.

    RecordingRunner records Command objects; run_steps labels them. We expose the
    labels the sequencer attached by tracking the step labels it built — but since
    the runner only sees Commands, the bootstrap tests assert on the per-phase
    label set the sequencer emits, captured here from the recorded command argv.
    """
    return runner.recorded


# --------------------------------------------------------------------------- #
# sequencer selection
# --------------------------------------------------------------------------- #
def test_bootstrap_runs_from_first_unsatisfied(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    # net+vms satisfied -> bootstrap runs provision then observe (4 steps: 1
    # provision + 3 observe). Script enough empty results for every step.
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(8)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu"])
    assert result.exit_code == 0
    argv0s = [c.argv[0] for c in runner.recorded]
    # net phase (virsh net-*) skipped; provision + observe ran
    assert "ansible-playbook" in argv0s  # provision + observe playbooks
    assert "mqlab" in argv0s  # observe render steps
    assert not any(a == "virsh" for a in argv0s)  # net phase satisfied -> skipped


def test_bootstrap_only_runs_one_phase(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 0
    argv0s = [c.argv[0] for c in runner.recorded]
    assert argv0s == ["ansible-playbook"]  # exactly the provision step, nothing else


def test_bootstrap_from_runs_phase_onward(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(8)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--from", "provision"])
    assert result.exit_code == 0
    argv0s = [c.argv[0] for c in runner.recorded]
    assert "ansible-playbook" in argv0s  # provision + observe
    assert "mqlab" in argv0s  # observe renders
    assert "virsh" not in argv0s and "vagrant" not in argv0s  # net+vms skipped


def test_bootstrap_all_satisfied_is_a_noop(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=True, observe=True),
    )
    runner = RecordingRunner(results=[])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert runner.recorded == []  # nothing to do


# --------------------------------------------------------------------------- #
# failure -> resume hint
# --------------------------------------------------------------------------- #
def test_bootstrap_failure_prints_resume_hint(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    # The single provision step fails (exit 2).
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=2)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 2
    assert "mqlab bootstrap pcmk-ubuntu --from provision" in result.output


def test_bootstrap_no_tty_under_step_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    # observe phase has 3 steps; --step pauses between them and the pauser has no TTY.
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(4)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps_pauser(runner, _FailPause()))
    result = CliRunner().invoke(
        cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "observe", "--step"]
    )
    assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def test_bootstrap_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["bootstrap", "nope"])
    assert result.exit_code == 2
    assert "nope" in result.output


def test_bootstrap_unknown_from_phase_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--from", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_bootstrap_unknown_only_phase_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "bogus"])
    assert result.exit_code == 2
    assert "bogus" in result.output


def test_lookup_stack_or_exit_returns_stack(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    assert stack.name == "pcmk-ubuntu"


def test_lookup_stack_or_exit_unknown(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    with pytest.raises(typer.Exit) as exc:
        cli._lookup_stack_or_exit("nope")
    assert exc.value.exit_code == 2


# --------------------------------------------------------------------------- #
# _probe_all — the live state-gathering seam (mock the runner / probe inputs)
# --------------------------------------------------------------------------- #
def _net_listing(states):
    lines = [" Name        State", "----------------------"]
    lines += [f" {n}   {st}" for n, st in states.items()]
    return ScriptedResult(lines)


def _dom_listing(states):
    lines = [" Id   Name              State", "------------------------------------"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def _targets_json(port, health="up"):
    """A Prometheus /api/v1/targets response with one active target on `port`."""
    target = {"scrapeUrl": f"http://x:{port}/metrics", "health": health}
    return ScriptedResult([json.dumps({"data": {"activeTargets": [target]}})])


def test_probe_all_builds_states_dict(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    # 1: net-list, 2: list --all, 3: qm-status probe, 4: prometheus targets probe
    runner = RecordingRunner(
        results=[
            _net_listing({"net-mgmt": "active", "net-data-a": "active"}),
            _dom_listing(
                dict.fromkeys(
                    ["san-a", "pcmk-a1", "pcmk-a2", "pcmk-a3", "pcmk-b1", "obs", "mon-probe"],
                    "running",
                )
            ),
            # qm-status succeeds (emits a line, exercising the sink-less tee path) -> qm_up
            ScriptedResult(["pcmk-a1 | CHANGED | rc=0 >>", "mq_group  Started"], exit_code=0),
            _targets_json(9157),  # this stack's exporter is a healthy target
        ]
    )
    deps = _deps(runner)
    states = cli._probe_all(deps, stack)
    assert states["nets"]["net-mgmt"] == "active"
    assert states["domains"]["lab_pcmk-a1"] == "running"
    assert states["qm_up"] is True
    assert states["observe"] is True
    # The qm-status probe MUST target the cluster node group (pcmk_a), not
    # groups[0] which is san_a — the SAN host that has no Pacemaker/MQ tooling.
    # This assertion would FAIL against the old code that used stack.groups[0].
    qm_probe_cmd = runner.recorded[2]  # command 3 is the ansible qm-status call
    assert "pcmk_a[0]" in qm_probe_cmd.argv, (
        f"qm-status probe must target pcmk_a[0] (cluster group), "
        f"not san_a[0] (SAN host); got argv={qm_probe_cmd.argv}"
    )


def test_probe_all_qm_down_when_status_nonzero(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    runner = RecordingRunner(
        results=[
            _net_listing({}),
            _dom_listing({}),
            ScriptedResult([], exit_code=1),  # qm-status fails -> not provisioned
            ScriptedResult([], exit_code=1),  # prometheus unreachable -> observe False
        ]
    )
    states = cli._probe_all(_deps(runner), stack)
    assert states["qm_up"] is False
    assert states["observe"] is False


def test_probe_all_observe_false_when_port_absent(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    runner = RecordingRunner(
        results=[
            _net_listing({}),
            _dom_listing({}),
            ScriptedResult([], exit_code=0),
            # targets respond but this stack's exporter port (9157) is not present
            _targets_json(9999),
        ]
    )
    states = cli._probe_all(_deps(runner), stack)
    assert states["observe"] is False


def test_probe_all_qm_down_when_no_status_verb(monkeypatch, tmp_path):
    # A stack with no qm-status verb (reserved) cannot be probed -> qm_up False,
    # and with no exporter port -> observe False, with no extra runner calls.
    topo = (
        "nodes: {}\n"
        "groups: {}\n"
        "stacks:\n"
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
    )
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    stack = cli._lookup_stack_or_exit("nativeha-ubuntu")
    runner = RecordingRunner(results=[_net_listing({}), _dom_listing({})])
    states = cli._probe_all(_deps(runner), stack)
    assert states["qm_up"] is False
    assert states["observe"] is False
    # only the two virsh probes ran — no qm-status / prometheus probe
    assert len(runner.recorded) == 2


def test_probe_all_qm_down_when_status_verb_but_no_cluster_group(monkeypatch, tmp_path):
    # A stack that declares a qm-status verb but omits cluster_group (the second
    # early-return guard in _probe_qm_up) returns False with no runner call for
    # the qm-status step. This exercises the `if not stack.cluster_group` branch.
    topo = (
        "nodes: {}\n"
        "groups: {}\n"
        "stacks:\n"
        "  edge-stack:\n"
        "    mechanism: pacemaker-san\n"
        "    os: ubuntu\n"
        "    short: EDGE\n"
        "    groups: []\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs:\n"
        "      qm-status: { pcs: 'status resources' }\n"
    )
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    stack = cli._lookup_stack_or_exit("edge-stack")
    assert stack.cluster_group is None  # no cluster_group declared
    runner = RecordingRunner(results=[_net_listing({}), _dom_listing({})])
    states = cli._probe_all(_deps(runner), stack)
    assert states["qm_up"] is False
    # only the two virsh probes ran — cluster_group guard prevents the ansible call
    assert len(runner.recorded) == 2
