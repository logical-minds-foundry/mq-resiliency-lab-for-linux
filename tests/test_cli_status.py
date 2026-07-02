"""Tests for the #350 stack-wide `status` command (Task 7).

`status [<stack>]` shows per-phase satisfied/unsatisfied (✓/✗) for one stack
or all non-reserved stacks. Reuses `_probe_all` (the live state-gathering seam)
and `PHASES[i].satisfied(stack, states)`.
"""

from __future__ import annotations

from typer.testing import CliRunner

from mqlab import cli
from mqlab.phases import build_states

# Topology mirroring test_cli_bootstrap.py: two stacks, one reserved.
# commons includes svc+app so all_vms covers the shared distributed-path VMs.
TOPO = (
    "nodes:\n"
    "  san-a: {}\n"
    "  pcmk-a1: {}\n"
    "  pcmk-a2: {}\n"
    "  pcmk-a3: {}\n"
    "  pcmk-b1: {}\n"
    "  obs: {}\n"
    "  mon-probe: {}\n"
    "  svc-sim: {}\n"
    "  app-client: {}\n"
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
    "  nativeha-ubuntu:\n"
    "    mechanism: native-ha\n"
    "    os: ubuntu\n"
    "    short: NHAU\n"
    "    cluster_group: null\n"
    "    groups: []\n"
    "    provision: null\n"
    "    secrets: []\n"
    "    qm: {}\n"
    "    alloc: {}\n"
    "    verbs: {}\n"
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


# --------------------------------------------------------------------------- #
# single-stack: phase completion rendering
# --------------------------------------------------------------------------- #


def test_status_shows_phase_completion(monkeypatch, tmp_path):
    """The brief's canonical test: provision unsatisfied → ✗ in output."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert "provision" in result.output and "✗" in result.output


def test_status_shows_check_for_satisfied_phases(monkeypatch, tmp_path):
    """Phases satisfied → ✓ appears; unsatisfied → ✗."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    # net + vms satisfied → ✓ present
    assert "✓" in result.output
    # provision + observe unsatisfied → ✗ present
    assert "✗" in result.output
    # all four phases named
    for phase_name in ("net", "vms", "provision", "observe"):
        assert phase_name in result.output


def test_status_all_satisfied_shows_only_checks(monkeypatch, tmp_path):
    """When all phases are satisfied every row is ✓."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=True, observe=True),
    )
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert "✓" in result.output
    assert "✗" not in result.output


def test_status_none_satisfied_shows_only_crosses(monkeypatch, tmp_path):
    """When nothing is satisfied every row is ✗."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=False, vms=False, provision=False, observe=False),
    )
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert "✗" in result.output
    assert "✓" not in result.output


def test_status_probes_once_per_stack(monkeypatch, tmp_path):
    """_probe_all must be called exactly once for a single-stack invocation."""
    _seed(monkeypatch, tmp_path)
    probe_calls: list[str] = []

    def _counting_probe(deps, stack):
        probe_calls.append(stack.name)
        return _states(net=True, vms=True, provision=False, observe=False)

    monkeypatch.setattr(cli, "_probe_all", _counting_probe)
    result = CliRunner().invoke(cli.app, ["status", "pcmk-ubuntu"])
    assert result.exit_code == 0
    assert probe_calls == ["pcmk-ubuntu"]


# --------------------------------------------------------------------------- #
# single-stack: unknown name exits 2
# --------------------------------------------------------------------------- #


def test_status_unknown_stack_exits_2(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    result = CliRunner().invoke(cli.app, ["status", "no-such-stack"])
    assert result.exit_code == 2
    assert "no-such-stack" in result.output


# --------------------------------------------------------------------------- #
# all-stacks view (no argument)
# --------------------------------------------------------------------------- #


def test_status_no_arg_shows_all_non_reserved_stacks(monkeypatch, tmp_path):
    """Without a stack arg, status iterates all non-reserved stacks."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    # the non-reserved stack is shown
    assert "pcmk-ubuntu" in result.output
    # phase names appear
    assert "provision" in result.output


def test_status_no_arg_skips_reserved_stack(monkeypatch, tmp_path):
    """Reserved stacks (cluster_group=None) must not appear in the all-stacks view."""
    _seed(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: _states(net=True, vms=True, provision=False, observe=False),
    )
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    # nativeha-ubuntu is reserved → must not appear in the all-stacks output
    assert "nativeha-ubuntu" not in result.output


def test_status_no_arg_probes_each_non_reserved_stack_once(monkeypatch, tmp_path):
    """_probe_all must be called once per non-reserved stack in all-stacks mode."""
    _seed(monkeypatch, tmp_path)
    probe_calls: list[str] = []

    def _counting_probe(deps, stack):
        probe_calls.append(stack.name)
        return _states()

    monkeypatch.setattr(cli, "_probe_all", _counting_probe)
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 0
    # only the non-reserved stack is probed
    assert probe_calls == ["pcmk-ubuntu"]


# --------------------------------------------------------------------------- #
# reserved-stack handling when named explicitly
# --------------------------------------------------------------------------- #


def test_status_reserved_stack_named_explicitly_does_not_crash(monkeypatch, tmp_path):
    """Naming a reserved stack by name must not crash — show a reserved note."""
    _seed(monkeypatch, tmp_path)
    # _probe_all should NOT be called for a reserved stack (no provision, no qm-status).
    # The command should detect cluster_group=None and short-circuit gracefully.
    probe_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_probe_all",
        lambda deps, stack: probe_calls.append(stack.name) or _states(),
    )
    result = CliRunner().invoke(cli.app, ["status", "nativeha-ubuntu"])
    assert result.exit_code == 0
    # Must not traceback; must emit something about "reserved"
    assert "reserved" in result.output.lower()
    # Should NOT probe a reserved stack
    assert "nativeha-ubuntu" not in probe_calls
