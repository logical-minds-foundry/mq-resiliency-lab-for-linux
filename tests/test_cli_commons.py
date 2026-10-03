"""Tests for `commons up/status/down` — Task 8 of the #350 CLI refactor.

`commons up`    — bring up all commons VMs + provision observability (site-obs.yml).
`commons down`  — destroy the commons VMs (_plan_destroy over _commons_members()).
`commons status`— show commons health (vm_status_core over _commons_members()).

Scope note: commons up brings up the shared infra VMs *and* provisions observability.
The per-stack svc QM and app instance (MQ workload on svc-sim / app-client) are
provisioned by a stack's bootstrap provision phase — NOT by commons up.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mqlab import cli
from mqlab.render import Renderer
from mqlab.transcript import Transcript, transcript_path
from tests.fakes import RecordingRunner, ScriptedResult

# ---------------------------------------------------------------------------
# Topology: a full commons topology (obs_box + probe + svc + app groups).
# Must include all groups referenced in dashboard.ROWS (pcmk_a, san_a, pcmk_b,
# san_b, rdqm_a, rdqm_b, app, svc, obs_box, probe) so lab_dashboard() doesn't
# raise DashboardError("unknown group in ROWS: …") during _obs_up_steps render.
# Nodes require nics.net-mgmt so render_inventory doesn't raise.
# ---------------------------------------------------------------------------
TOPO = (
    "nodes:\n"
    "  san-a:      {box: base, nics: {net-mgmt: 10.50.0.5}}\n"
    "  san-b:      {box: base, nics: {net-mgmt: 10.50.0.6}}\n"
    "  pcmk-a1:    {box: pcmk, nics: {net-mgmt: 10.50.0.51}}\n"
    "  pcmk-b1:    {box: pcmk, nics: {net-mgmt: 10.50.0.61}}\n"
    "  rdqm-a1:    {box: mq-rdqm, nics: {net-mgmt: 10.50.0.31}}\n"
    "  rdqm-b1:    {box: mq-rdqm, nics: {net-mgmt: 10.50.0.41}}\n"
    "  obs:        {box: obs, nics: {net-mgmt: 10.50.0.2}}\n"
    "  mon-probe:  {box: mq-client, nics: {net-mgmt: 10.50.0.3}}\n"
    "  svc-sim:    {box: mq-client, nics: {net-mgmt: 10.50.0.50}}\n"
    "  app-client: {box: mq-client, nics: {net-mgmt: 10.50.0.60}}\n"
    "groups:\n"
    "  san_a:   [san-a]\n"
    "  san_b:   [san-b]\n"
    "  pcmk_a:  [pcmk-a1]\n"
    "  pcmk_b:  [pcmk-b1]\n"
    "  rdqm_a:  [rdqm-a1]\n"
    "  rdqm_b:  [rdqm-b1]\n"
    "  obs_box: [obs]\n"
    "  probe:   [mon-probe]\n"
    "  svc:     [svc-sim]\n"
    "  app:     [app-client]\n"
    # Stack-role nodes run their stack's OS, so the version layer needs each one owned
    # by exactly one stack (epic .github#280).
    "stacks:\n"
    "  pcmk-ubuntu:\n"
    "    mechanism: pacemaker-san\n    os_family: ubuntu\n    short: PCMK\n"
    "    groups: [san_a, pcmk_a, san_b, pcmk_b]\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n    os_family: rhel\n    short: RDQM\n    groups: [rdqm_a, rdqm_b]\n"
    "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    "commons:\n"
    "  groups: [obs_box, probe, svc, app]\n"
    "  provision: ansible/site-obs.yml\n"
)


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(TOPO)


class _NoPause:
    def wait(self) -> None:
        return None


def _deps(runner):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("commons", "20260627T000000Z")),
        pauser=_NoPause(),
    )


@pytest.fixture(autouse=True)
def stub_ensure_prereqs(monkeypatch):
    """commons up calls _ensure_prereqs_for_commons — stub it so no real I/O."""
    monkeypatch.setattr(cli, "_ensure_prereqs_for_commons", lambda **k: None)


def _recorded_steps(runner):
    """Convenience: return the Command objects the runner saw (aliased for clarity)."""
    return runner.recorded


# ---------------------------------------------------------------------------
# commons up
# ---------------------------------------------------------------------------


def test_commons_up_provisions_shared_host(monkeypatch, tmp_path):
    """commons up must run site-obs.yml (the observability provision step)."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    assert any("site-obs.yml" in " ".join(s.argv) for s in _recorded_steps(runner))


def test_commons_up_host_collector_passes_no_mqlab_path(monkeypatch, tmp_path):
    """commons up runs host-obs.yml as a plain connection=local play (#1252).

    It used to pass `mqlab_bin=<checkout>/.venv/bin/mqlab` (#950) for the root
    lab-net-state service; that service is retired (#1253) and mqlab is never invoked
    by an absolute venv path, so no host play is handed one.
    """
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    host_obs = next(s for s in _recorded_steps(runner) if "host-obs.yml" in s.argv)
    assert host_obs.argv == [
        "ansible-playbook",
        "host-obs.yml",
        "-c",
        "local",
        "-i",
        "localhost,",
    ]


def test_commons_up_publishes_net_state_after_the_host_collector(monkeypatch, tmp_path):
    """commons up publishes lab_network_state once host-obs.yml made the drop zone (#1253).

    The polling lab-net-state probe is retired; after this one-shot publish the state
    only changes via net-up.sh / net-down.sh, which republish it themselves.
    """
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(12)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    argvs = [s.argv for s in _recorded_steps(runner)]
    host_idx = next(i for i, a in enumerate(argvs) if "host-obs.yml" in a)
    publish = argvs[host_idx + 1]
    assert publish[0] == "bash"
    assert publish[1].endswith("lab/scripts/net-state-publish.sh")


def test_commons_up_vagrant_ups_all_commons_hosts(monkeypatch, tmp_path):
    """commons up must issue vagrant up for every commons host, not just obs/probe."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    all_argv = [" ".join(s.argv) for s in _recorded_steps(runner)]
    vagrant_ups = [a for a in all_argv if "vagrant" in a and "up" in a]
    # All four commons members must appear in vagrant up commands
    all_hosts_covered = all(
        any(host in up for up in vagrant_ups)
        for host in ("obs", "mon-probe", "svc-sim", "app-client")
    )
    assert all_hosts_covered, f"not all commons hosts covered by vagrant up; recorded: {all_argv}"


def test_commons_up_also_renders_artifacts(monkeypatch, tmp_path):
    """commons up renders the inventory/targets/dashboards as part of _obs_up_steps."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    assert (tmp_path / "build" / "work" / "inventory.ini").exists()
    assert (tmp_path / "build" / "work" / "prometheus" / "targets" / "node.json").exists()


def test_commons_up_propagates_step_failure(monkeypatch, tmp_path):
    """A step failure during commons up propagates the step's exit code."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult(["boom"], exit_code=5)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 5


def test_commons_up_calls_prepare_lab(monkeypatch, tmp_path, prepare_lab_calls):
    """commons up shells vagrant — it must gate via _prepare_lab (#276)."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    assert prepare_lab_calls == ["prepare"]


def test_commons_up_ensures_commons_prereqs(monkeypatch, tmp_path):
    """commons up ensures the commons fresh-volume prerequisites before running steps."""
    _seed(monkeypatch, tmp_path)
    prereq_calls: list[bool] = []
    monkeypatch.setattr(cli, "_ensure_prereqs_for_commons", lambda **k: prereq_calls.append(True))
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    assert prereq_calls == [True]


def test_commons_up_no_extra_step_when_only_obs_probe(monkeypatch, tmp_path):
    """When commons groups contain only obs_box + probe, _commons_up_steps emits no
    extra vagrant-up step (the `if extra` branch is False)."""
    # Minimal topology: commons has only the two obs VMs — no svc/app in commons.
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n"
        "  san-a:      {box: base, nics: {net-mgmt: 10.50.0.5}}\n"
        "  san-b:      {box: base, nics: {net-mgmt: 10.50.0.6}}\n"
        "  pcmk-a1:    {box: pcmk, nics: {net-mgmt: 10.50.0.51}}\n"
        "  pcmk-b1:    {box: pcmk, nics: {net-mgmt: 10.50.0.61}}\n"
        "  rdqm-a1:    {box: mq-rdqm, nics: {net-mgmt: 10.50.0.31}}\n"
        "  rdqm-b1:    {box: mq-rdqm, nics: {net-mgmt: 10.50.0.41}}\n"
        "  svc-sim:    {box: mq-client, nics: {net-mgmt: 10.50.0.50}}\n"
        "  app-client: {box: mq-client, nics: {net-mgmt: 10.50.0.60}}\n"
        "  obs:        {box: obs, nics: {net-mgmt: 10.50.0.2}}\n"
        "  mon-probe:  {box: mq-client, nics: {net-mgmt: 10.50.0.3}}\n"
        "groups:\n"
        "  san_a: [san-a]\n  san_b: [san-b]\n"
        "  pcmk_a: [pcmk-a1]\n  pcmk_b: [pcmk-b1]\n"
        "  rdqm_a: [rdqm-a1]\n  rdqm_b: [rdqm-b1]\n"
        "  svc: [svc-sim]\n  app: [app-client]\n"
        "  obs_box: [obs]\n  probe: [mon-probe]\n"
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
        "commons:\n"
        "  groups: [obs_box, probe]\n"  # only obs/probe — no extra
        "  provision: ansible/site-obs.yml\n"
    )
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    all_argv = [" ".join(s.argv) for s in _recorded_steps(runner)]
    # No extra vagrant up beyond the obs monitoring-create step
    extra_ups = [a for a in all_argv if "vagrant" in a and "svc-sim" in a]
    assert not extra_ups, f"unexpected extra vagrant up step(s): {extra_ups}"


# ---------------------------------------------------------------------------
# commons up — log-search tier consolidated onto obs (#1179): folded into site-obs.yml,
# no separate node bring-up; fan-out gate still rendered (endpoint = obs Data Prepper).
# ---------------------------------------------------------------------------


def test_commons_up_folds_logsearch_into_site_obs_no_separate_play(monkeypatch, tmp_path):
    """The log-search tier rides obs now (#1179): commons up runs site-obs.yml (which
    carries the folded log-stack configure) and no standalone site-logsearch.yml / separate
    `vagrant up logsearch` survives."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(14)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    all_argv = [" ".join(s.argv) for s in _recorded_steps(runner)]
    assert any("site-obs.yml" in a for a in all_argv)
    assert not any("site-logsearch.yml" in a for a in all_argv)
    assert not any("vagrant" in a and "logsearch" in a for a in all_argv)


def test_commons_up_renders_fanout_gate_at_obs_endpoint(monkeypatch, tmp_path):
    """commons up renders build/work/logsearch/fanout.json enabling fleet-wide fan-out at
    the topology-derived Data Prepper endpoint — now the OBS node's mgmt IP (#1179)."""
    import json

    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(14)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    gate = tmp_path / "build" / "work" / "logsearch" / "fanout.json"
    assert gate.exists(), "fan-out gate file was not rendered"
    body = json.loads(gate.read_text())
    assert body == {"enabled": True, "endpoint": "10.50.0.2:21892"}


def test_commons_up_site_obs_passes_snapshot_state_dir(monkeypatch, tmp_path):
    """site-obs.yml must receive opensearch_snapshot_state_dir resolved to the host-durable
    state bucket (build/state/logsearch) — the folded OpenSearch configure's restore seam."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(14)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    obs_step = next(s for s in runner.recorded if "site-obs.yml" in s.argv)
    joined = " ".join(obs_step.argv)
    expected = str(tmp_path / "build" / "state" / "logsearch")
    assert f"opensearch_snapshot_state_dir={expected}" in joined, joined


# ---------------------------------------------------------------------------
# commons status
# ---------------------------------------------------------------------------


def test_commons_status_runs_virsh_for_commons_hosts(monkeypatch, tmp_path):
    """commons status uses vm_status_core, which issues virsh list --all."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([" -  lab_obs  running"])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "status"])

    assert result.exit_code == 0
    assert runner.recorded[0].argv == ["virsh", "-c", "qemu:///system", "list", "--all"]


def test_commons_status_surfaces_obs_not_a_separate_logsearch(monkeypatch, tmp_path):
    """commons status surfaces obs (which carries the consolidated log-search tier, #1179);
    there is no separate `logsearch` node to list."""
    _seed(monkeypatch, tmp_path)
    captured: dict[str, list[str]] = {}

    def _fake_status(runner, renderer, transcript, *, guests):  # noqa: ANN001, ARG001
        captured["guests"] = guests
        return 0

    monkeypatch.setattr(cli, "vm_status_core", _fake_status)
    runner = RecordingRunner(results=[ScriptedResult([])])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "status"])

    assert result.exit_code == 0
    assert "obs" in captured["guests"]
    assert "logsearch" not in captured["guests"]


def test_commons_status_nonzero_exit_propagates(monkeypatch, tmp_path):
    """A virsh failure during commons status propagates the exit code."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([], exit_code=1)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "status"])

    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# commons down
# ---------------------------------------------------------------------------


def _dom_listing(states: dict[str, str]) -> ScriptedResult:
    """Build a scripted virsh list --all result placing each guest in a given state."""
    lines = [" Id   Name              State", "------------------------------------"]
    lines += [f" -    lab_{g}    {st}" for g, st in states.items()]
    return ScriptedResult(lines)


def test_commons_down_destroys_commons_vms(monkeypatch, tmp_path):
    """commons down plans destroy steps over the commons members."""
    _seed(monkeypatch, tmp_path)
    # Provide virsh output showing all commons VMs running so _plan_destroy emits real
    # steps.  _execute_stateful's prober default is bound at def-time, so we feed the
    # real _probe_states through the runner rather than monkeypatching the default arg.
    commons_hosts = ["obs", "mon-probe", "svc-sim", "app-client"]
    virsh_result = _dom_listing(dict.fromkeys(commons_hosts, "running"))

    captured: list = []
    monkeypatch.setattr(cli, "run_steps", lambda steps, **kw: captured.extend(steps))
    monkeypatch.setattr(
        cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[virsh_result]))
    )

    result = CliRunner().invoke(cli.app, ["commons", "down"])

    assert result.exit_code == 0
    labels = [s.label for s in captured]
    # Every commons host must appear in some step label
    for host in commons_hosts:
        assert any(host in lbl for lbl in labels), (
            f"{host!r} not found in any step label; labels={labels}"
        )


def test_commons_down_removes_fanout_gate(monkeypatch, tmp_path):
    """commons down removes the fan-out gate file so fleet-wide fan-out reverts to
    inert (no fan-out pointed at a torn-down endpoint on the next observe run)."""
    _seed(monkeypatch, tmp_path)
    gate = tmp_path / "build" / "work" / "logsearch" / "fanout.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text('{"enabled": true, "endpoint": "10.50.0.2:21892"}\n')
    virsh_result = _dom_listing({})

    monkeypatch.setattr(cli, "run_steps", lambda steps, **kw: None)
    monkeypatch.setattr(
        cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[virsh_result]))
    )

    result = CliRunner().invoke(cli.app, ["commons", "down"])

    assert result.exit_code == 0
    assert not gate.exists(), "fan-out gate file must be removed on commons down"


def test_commons_down_absent_vms_emit_no_steps(monkeypatch, tmp_path):
    """When all commons VMs are absent, commons down completes with no destroy steps."""
    _seed(monkeypatch, tmp_path)
    # Empty virsh output: all VMs absent -> _plan_destroy emits notes, not steps
    virsh_result = _dom_listing({})

    captured: list = []
    monkeypatch.setattr(cli, "run_steps", lambda steps, **kw: captured.extend(steps))
    monkeypatch.setattr(
        cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[virsh_result]))
    )

    result = CliRunner().invoke(cli.app, ["commons", "down"])

    assert result.exit_code == 0
    assert captured == []


def test_commons_down_step_failure_propagates_exit_code(monkeypatch, tmp_path):
    """A step failure during commons down propagates the step's exit code."""
    _seed(monkeypatch, tmp_path)
    commons_hosts = ["obs", "mon-probe", "svc-sim", "app-client"]
    virsh_result = _dom_listing(dict.fromkeys(commons_hosts, "running"))

    class _FailRunner:
        def run(self, command, on_line):  # noqa: ARG002
            # First call returns the virsh output (probe); all subsequent calls fail
            if not hasattr(self, "_probed"):
                self._probed = True
                for line in virsh_result.lines:
                    on_line(line)
                return 0
            return 3

    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(_FailRunner()))

    result = CliRunner().invoke(cli.app, ["commons", "down"])

    assert result.exit_code == 3


# ---------------------------------------------------------------------------
# Bad subcommand: missing / wrong verb
# ---------------------------------------------------------------------------


def test_commons_bad_subcommand_shows_help(monkeypatch, tmp_path):
    """An unrecognised (or missing) subcommand exits non-zero and shows usage."""
    _seed(monkeypatch, tmp_path)

    result = CliRunner().invoke(cli.app, ["commons", "fly"])

    assert result.exit_code != 0


def test_commons_no_subcommand_shows_help():
    """Invoking `commons` with no subcommand exits non-zero (no_args_is_help=True)."""
    result = CliRunner().invoke(cli.app, ["commons"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Coverage for the shared step-runner branches commons reuses (after the #350
# vm/net/obs command removal, commons up/down are the surviving exercisers).
# ---------------------------------------------------------------------------


class _FailPause:
    def wait(self) -> None:
        raise cli.NoTTYError("no tty")


def _deps_pauser(runner, pauser):
    return cli.Deps(
        runner=runner,
        renderer=Renderer(Console(file=io.StringIO(), force_terminal=False, width=80)),
        transcript=Transcript(transcript_path("commons", "20260627T000000Z")),
        pauser=pauser,
    )


def test_commons_up_without_tty_exits_two(monkeypatch, tmp_path):
    """commons up --step with no TTY: _execute surfaces NoTTYError as exit 2."""
    _seed(monkeypatch, tmp_path)
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps_pauser(runner, _FailPause()))

    result = CliRunner().invoke(cli.app, ["commons", "up", "--step"])

    assert result.exit_code == 2


def test_commons_down_without_tty_exits_two(monkeypatch, tmp_path):
    """commons down --step with no TTY: _execute_stateful surfaces NoTTYError as exit 2."""
    _seed(monkeypatch, tmp_path)
    commons_hosts = ["obs", "mon-probe", "svc-sim", "app-client"]
    virsh_result = _dom_listing(dict.fromkeys(commons_hosts, "running"))
    runner = RecordingRunner(results=[virsh_result, *(ScriptedResult([]) for _ in range(10))])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps_pauser(runner, _FailPause()))

    result = CliRunner().invoke(cli.app, ["commons", "down", "--step"])

    assert result.exit_code == 2


def test_commons_down_shut_off_vm_undefines_directly(monkeypatch, tmp_path):
    """A shut-off (not live) commons VM hits _plan_destroy's direct-undefine branch."""
    _seed(monkeypatch, tmp_path)
    virsh_result = _dom_listing({"obs": "shut off"})  # off, not live -> undefine directly

    captured: list = []
    monkeypatch.setattr(cli, "run_steps", lambda steps, **kw: captured.extend(steps))
    monkeypatch.setattr(
        cli, "build_deps", lambda v, t: _deps(RecordingRunner(results=[virsh_result]))
    )

    result = CliRunner().invoke(cli.app, ["commons", "down"])

    assert result.exit_code == 0
    labels = [s.label for s in captured]
    # exactly one step for obs: the undefine (no force-off, since it is not live)
    obs_steps = [lbl for lbl in labels if "obs" in lbl]
    assert obs_steps == ["obs undefine"]


def test_commons_up_threads_obs_manifest_overlay(monkeypatch, tmp_path):
    """When the shared obs manifest exists, _obs_manifest_args adds an -e @overlay arg
    to the site-obs.yml provision step (the surviving exerciser is commons up)."""
    _seed(monkeypatch, tmp_path)
    shared = tmp_path / "manifests" / "_shared" / "observability.yaml"
    shared.parent.mkdir(parents=True)
    shared.write_text(
        "prometheus: p\nnode_exporter: ne\nloki: l\nalloy: a\ngrafana: g\n"
        "mq_metric_samples_ref: r\nopensearch: os\nopensearch_dashboards: osd\n"
        "data_prepper: dp\n"
    )
    runner = RecordingRunner(results=[ScriptedResult([]) for _ in range(10)])
    monkeypatch.setattr(cli, "build_deps", lambda v, t: _deps(runner))

    result = CliRunner().invoke(cli.app, ["commons", "up"])

    assert result.exit_code == 0
    obs_step = next(s for s in runner.recorded if "site-obs.yml" in s.argv)
    overlay = tmp_path / "build" / "work" / "manifests" / "_obs.overlay.json"
    # The overlay `-e @…` pair must appear consecutively (the snapshot-state-dir `-e` the
    # log-search consolidation added, #1179, also trails site-obs.yml, so it need not be last).
    argv = obs_step.argv
    pairs = [argv[i : i + 2] for i in range(len(argv) - 1)]
    assert ["-e", f"@{overlay}"] in pairs
