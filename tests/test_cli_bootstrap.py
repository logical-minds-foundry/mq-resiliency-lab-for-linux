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
import os

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.phases import PHASES, build_states
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

# A seeded topology mirroring tests/test_phases.py: nodes + groups + a stacks:
# block + commons + two lab networks (for the net phase to enumerate).
# commons includes svc+app so all_vms covers the shared distributed-path VMs.
TOPO = (
    # nodes carry a net-mgmt IP so lab_inventory() renders ansible_host (the
    # bootstrap now refreshes the inventory — #377).
    "nodes:\n"
    "  san-a: {nics: {net-mgmt: 10.50.0.10}}\n"
    "  pcmk-a1: {nics: {net-mgmt: 10.50.0.51}}\n"
    "  pcmk-a2: {nics: {net-mgmt: 10.50.0.52}}\n"
    "  pcmk-a3: {nics: {net-mgmt: 10.50.0.53}}\n"
    "  pcmk-b1: {nics: {net-mgmt: 10.50.0.61}}\n"
    "  obs: {nics: {net-mgmt: 10.50.0.2}}\n"
    "  mon-probe: {nics: {net-mgmt: 10.50.0.3}}\n"
    "  svc-sim: {nics: {net-mgmt: 10.50.0.50}}\n"
    "  app-client: {nics: {net-mgmt: 10.50.0.40}}\n"
    "groups:\n"
    "  san_a:   [san-a]\n"
    "  pcmk_a:  [pcmk-a1, pcmk-a2, pcmk-a3]\n"
    "  pcmk_b:  [pcmk-b1]\n"
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
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
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


def _states(*, net=True, vms=True, provision=False, observe=False):
    nets = {"net-mgmt": "active", "net-data-a": "active"} if net else {}
    guests = [
        "san-a",
        "pcmk-a1",
        "pcmk-a2",
        "pcmk-a3",
        "pcmk-b1",
        "obs",
        "mon-probe",
        "svc-sim",
        "app-client",
    ]
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
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    _stub_ensure(monkeypatch)  # selection test: prereq-ensure is a separate concern
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
    _stub_ensure(monkeypatch)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 0
    argv0s = [c.argv[0] for c in runner.recorded]
    assert argv0s == ["ansible-playbook"]  # exactly the provision step, nothing else


def test_bootstrap_from_runs_phase_onward(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    _stub_ensure(monkeypatch)
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
# per-phase prerequisite ensures (#350 Task 5)
#
# Each selected phase ensures its declared prerequisites BEFORE its steps run.
# We mock the cli-level ensure (cli._ensure_prereqs_for_stack) — phases.py stays
# pure — and assert it is invoked once per selected phase, with that phase, and
# only for phases actually selected this run.
# --------------------------------------------------------------------------- #
def _stub_ensure(monkeypatch):
    """Neutralize the per-phase prereq-ensure AND the provision secret-sourcing so a
    sequencer-selection test stays focused on phase selection (each is covered by its
    own tests below)."""
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"secret-{name}")
    monkeypatch.setattr(cli, "_render_inventory", lambda deps: None)


def _record_ensures(monkeypatch):
    ensured: list[str] = []
    monkeypatch.setattr(
        cli,
        "_ensure_prereqs_for_stack",
        lambda stack, phase, *, step: ensured.append(phase.name),
    )
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"secret-{name}")
    monkeypatch.setattr(cli, "_render_inventory", lambda deps: None)
    return ensured


def test_bootstrap_ensures_prereqs_per_selected_phase(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    ensured = _record_ensures(monkeypatch)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu"])
    assert result.exit_code == 0
    # net+vms satisfied -> only provision + observe selected -> ensured for each
    assert ensured == ["provision", "observe"]


def test_bootstrap_only_observe_ensures_only_observe(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    ensured = _record_ensures(monkeypatch)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "observe"])
    assert result.exit_code == 0
    assert ensured == ["observe"]


def test_bootstrap_only_net_ensures_nothing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states(net=False))
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    calls: list[str] = []
    # The net phase declares no prereqs, so dispatch must run no per-kind helper.
    monkeypatch.setattr(cli, "_ensure_mq_artifacts_for_stack", lambda s: calls.append("mq"))
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: calls.append("boxes"))
    monkeypatch.setattr(cli, "_galaxy_install_step", lambda: calls.append("galaxy"))  # type: ignore[arg-type]
    monkeypatch.setattr(cli, "_pki_ensure_step", lambda: calls.append("pki"))  # type: ignore[arg-type]
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "net"])
    assert result.exit_code == 0
    assert calls == []  # net phase has no prerequisites


# --------------------------------------------------------------------------- #
# provision secret injection (#373: the #350 cutover dropped it -> hacluster
# password came up empty and chpasswd failed on the cluster nodes)
#
# The provision playbook's roles read secrets from the environment (e.g.
# PCMK_HACLUSTER_PASSWORD via lookup('env', ...)). The sequencer resolves
# stack.secrets via lab-secret.sh and exports them (uppercased) before provision.
# --------------------------------------------------------------------------- #
def test_provision_injects_stack_secrets(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    sourced: list[str] = []

    def fake_source(deps, name):
        sourced.append(name)
        return f"v-{name}"

    monkeypatch.setattr(cli, "_source_secret", fake_source)
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(2)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 0
    # both of the stack's secrets are sourced, in order, and exported uppercased
    assert sourced == ["pcmk_hacluster_password", "mqweb_admin_password"]
    for name in sourced:  # exported under the uppercased name with the sourced value
        assert os.environ[name.upper()] == f"v-{name}"
        monkeypatch.delenv(name.upper(), raising=False)


def test_non_provision_phase_sources_no_secrets(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    sourced: list[str] = []
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: sourced.append(name) or "x")
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "observe"])
    assert result.exit_code == 0
    assert sourced == []  # observe doesn't touch the cluster/QM secrets


def test_source_secret_returns_trimmed_value(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["  topsecret  "])])
    value = cli._source_secret(_deps(runner), "pcmk_hacluster_password")
    assert value == "topsecret"
    assert runner.recorded[0].argv[-1] == "pcmk_hacluster_password"  # lab-secret.sh <name>


def test_source_secret_exits_loud_on_failure(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=1)])
    with pytest.raises(typer.Exit):
        cli._source_secret(_deps(runner), "pcmk_hacluster_password")


# --------------------------------------------------------------------------- #
# inventory refresh (#377: provision ran against a STALE inventory missing the
# #350 stack-aggregate groups -> hosts: pcmk_ubuntu plays silently no-op'd)
# --------------------------------------------------------------------------- #
def test_bootstrap_renders_inventory_with_aggregate_group(monkeypatch, tmp_path):
    from mqlab.inventory import inventory_path

    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    monkeypatch.setattr(cli, "_ensure_prereqs_for_stack", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_source_secret", lambda deps, name: f"secret-{name}")
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(2)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 0
    # the bootstrap must (re)render a fresh inventory carrying the stack-aggregate
    # group the cutover playbooks target (the stale-inventory bug fixed by #377)
    assert "[pcmk_ubuntu:children]" in inventory_path().read_text()


# --------------------------------------------------------------------------- #
# _ensure_prereqs_for_stack dispatch — which prereq kinds each phase pulls in
# --------------------------------------------------------------------------- #
def _phase(name):
    return next(p for p in PHASES if p.name == name)


def test_ensure_for_stack_vms_pulls_boxes_and_mq(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    calls: list[str] = []
    monkeypatch.setattr(cli, "_ensure_mq_artifacts_for_stack", lambda s: calls.append("mq"))
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: calls.append("boxes"))
    monkeypatch.setattr(cli, "_galaxy_install_step", lambda: pytest.fail("galaxy not for vms"))
    monkeypatch.setattr(cli, "_pki_ensure_step", lambda: pytest.fail("pki not for vms"))
    cli._ensure_prereqs_for_stack(stack, _phase("vms"), step=False)
    assert sorted(calls) == ["boxes", "mq"]


def _capture_execute(monkeypatch):
    """Capture the step labels the dispatch passes to _execute (the step-runner seam)."""
    labels: list[str] = []
    monkeypatch.setattr(
        cli,
        "_execute",
        lambda verb, steps, *, step_mode: labels.extend(s.label for s in steps),
    )
    return labels


def test_ensure_for_stack_provision_pulls_galaxy_mq_pki(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    mq: list[str] = []
    monkeypatch.setattr(cli, "_ensure_mq_artifacts_for_stack", lambda s: mq.append("mq"))
    monkeypatch.setattr(cli, "_render_pki_entities", lambda: tmp_path / "pki.json")
    labels = _capture_execute(monkeypatch)
    cli._ensure_prereqs_for_stack(stack, _phase("provision"), step=False)
    assert mq == ["mq"]
    # galaxy + PKI run through the step runner, galaxy before PKI (PKI needs crypto)
    assert labels == ["ansible collections", "pki ensure"]


def test_ensure_for_stack_observe_pulls_only_pki(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    monkeypatch.setattr(
        cli, "_ensure_mq_artifacts_for_stack", lambda s: pytest.fail("no mq for observe")
    )
    monkeypatch.setattr(cli, "_galaxy_install_step", lambda: pytest.fail("no galaxy for observe"))
    monkeypatch.setattr(cli, "_render_pki_entities", lambda: tmp_path / "pki.json")
    labels = _capture_execute(monkeypatch)
    cli._ensure_prereqs_for_stack(stack, _phase("observe"), step=False)
    assert labels == ["pki ensure"]  # exporter PKI only


def test_ensure_for_stack_net_pulls_nothing(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: pytest.fail("net runs no ensure steps"))
    monkeypatch.setattr(
        cli, "_ensure_mq_artifacts_for_stack", lambda s: pytest.fail("net runs no mq ensure")
    )
    monkeypatch.setattr(cli, "_ensure_local_boxes", lambda g: pytest.fail("net runs no box ensure"))
    cli._ensure_prereqs_for_stack(stack, _phase("net"), step=False)  # no-op, no failures


def test_stack_mq_platforms_resolves_cluster_node_platforms(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    # All pcmk-ubuntu cluster nodes resolve to the host-default ubuntu platform.
    plats = cli._stack_mq_platforms(stack)
    assert plats  # non-empty: the QM hosts need an MQ tarball
    assert all(p.startswith("ubuntu") for p in plats)


def test_stack_mq_platforms_includes_commons_svc_app(monkeypatch, tmp_path):
    """The stack's provision installs MQ on the commons svc/app too (every provision
    playbook imports site-distributed-shared.yml → mq-install on svc/app), so their
    platform's tarball must be ensured even when no cluster node shares it. Regression
    for the cold-cache miss on svc-sim's UbuntuLinuxX64 tarball (#407)."""
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    # A platform no cluster node uses — only the commons set contributes it.
    monkeypatch.setattr(cli, "_commons_mq_platforms", lambda: {"commons-only-platform"})
    plats = cli._stack_mq_platforms(stack)
    assert "commons-only-platform" in plats


def test_ensure_mq_artifacts_for_stack_delegates(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    stack = cli._lookup_stack_or_exit("pcmk-ubuntu")
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "_stack_mq_platforms", lambda s: {"ubuntu2404-arm64"})
    monkeypatch.setattr(cli, "mq_cache_dir", lambda: tmp_path / "mqcache")
    monkeypatch.setattr(
        cli,
        "ensure_mq_tarballs_for_platforms",
        lambda platforms, version, build_dir, *, fetch: captured.update(
            platforms=platforms, version=version, build_dir=build_dir, fetch=fetch
        ),
    )
    cli._ensure_mq_artifacts_for_stack(stack)
    assert captured["platforms"] == {"ubuntu2404-arm64"}
    assert captured["version"] == cli.DEFAULT_MQ_VERSION
    assert captured["build_dir"] == tmp_path / "mqcache"
    assert captured["fetch"] is cli._fetch_mq_tarball


# --------------------------------------------------------------------------- #
# failure -> resume hint
# --------------------------------------------------------------------------- #
def test_bootstrap_failure_prints_resume_hint(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    # The single provision step fails (exit 2).
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=2)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))
    _stub_ensure(monkeypatch)
    result = CliRunner().invoke(cli.app, ["bootstrap", "pcmk-ubuntu", "--only", "provision"])
    assert result.exit_code == 2
    assert "mqlab bootstrap pcmk-ubuntu --from provision" in result.output


def test_bootstrap_no_tty_under_step_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_probe_all", lambda deps, stack: _states())
    # observe phase has 3 steps; --step pauses between them and the pauser has no TTY.
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps_pauser(runner, _FailPause()))
    _stub_ensure(monkeypatch)
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
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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
