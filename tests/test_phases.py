"""Tests for the phase registry (mqlab.phases) — #350 Task 3.

phases.py is PURE: every `satisfied` predicate reads an already-gathered
`states` dict (Task 4's `_probe_all` in cli.py builds the live one). No
subprocess, no virsh/vagrant/prometheus calls here — so these tests drive the
registry with plain dicts and assert the emitted CommandStep argv/labels.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mqlab.orchestrator import CommandStep
from mqlab.phases import PHASES, build_states, first_unsatisfied
from mqlab.scrape import mq_exporters_path
from mqlab.stacks import lab_stacks

# Mirror tests/test_stacks.py's seeded topology, plus the lab networks the net
# phase enumerates and the commons (obs_box/probe/svc/app) groups the vms phase
# needs. svc-sim and app-client are shared commons (spec §6): they must appear
# in all_vms so the vms phase brings them up alongside the obs pair.
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
    "  groups: [obs_box, probe, svc, app]\n"
    "  provision: ansible/site-obs.yml\n"
)

# Two lab networks so the net phase has something concrete to require.
NET_XML = "<network><name>{name}</name></network>\n"


def _seed(tmp_path):
    lab = tmp_path / "lab"
    nets = lab / "networks"
    nets.mkdir(parents=True)
    (lab / "topology.yaml").write_text(TOPO)
    for n in ("net-mgmt", "net-data-a"):
        (nets / f"{n}.xml").write_text(NET_XML.format(name=n))


def _fake_states(net=True, vms=True, provision=False, observe=False):
    """Produce the states dict the satisfied predicates consume.

    Shape (the contract Task 4's _probe_all fills):
      nets:    {net_name: virsh-net state} for classify_net
      domains: {lab_<guest>: virsh state}  for classify
      qm_up:   bool — stack QM provisioned + running (provision phase)
      observe: bool — this stack's exporter responds + targets registered
    """
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
    return {"nets": nets, "domains": domains, "qm_up": provision, "observe": observe}


def test_phase_order():
    assert [p.name for p in PHASES] == ["net", "vms", "provision", "observe"]


def test_first_unsatisfied_resumes_from_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    states = _fake_states(net=True, vms=True, provision=False, observe=False)
    assert first_unsatisfied(stack, states) == 2  # provision


def test_all_satisfied_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    assert first_unsatisfied(stack, _fake_states(True, True, True, True)) is None


def test_first_unsatisfied_net_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    assert first_unsatisfied(stack, _fake_states(net=False)) == 0


def test_first_unsatisfied_vms_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    assert first_unsatisfied(stack, _fake_states(net=True, vms=False)) == 1


def test_first_unsatisfied_observe_gap(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    states = _fake_states(net=True, vms=True, provision=True, observe=False)
    assert first_unsatisfied(stack, states) == 3


# --- satisfied predicate edge branches ---


def test_net_unsatisfied_when_one_net_inactive(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    states = _fake_states()
    states["nets"]["net-data-a"] = "inactive"
    net_phase = PHASES[0]
    assert net_phase.satisfied(stack, states) is False


def test_vms_unsatisfied_when_commons_down(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    states = _fake_states()
    del states["domains"]["lab_obs"]  # commons VM not running
    vms_phase = PHASES[1]
    assert vms_phase.satisfied(stack, states) is False


def test_vms_satisfied_when_all_running(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    assert PHASES[1].satisfied(stack, _fake_states()) is True


# --- build_steps: argv/label assertions for pcmk-ubuntu ---


def test_net_build_steps_define_autostart_start(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[0].build_steps(stack, None)
    assert all(isinstance(s, CommandStep) for s in steps)
    by_label = {s.label: s for s in steps}
    # each lab net gets define + autostart + start
    assert "net-mgmt define" in by_label
    assert "net-mgmt autostart" in by_label
    assert "net-mgmt start" in by_label
    assert "net-data-a define" in by_label
    # the define step runs `virsh net-define <xml>`; autostart/start run net-autostart/net-start
    assert by_label["net-mgmt define"].command.argv[:4] == [
        "virsh",
        "-c",
        "qemu:///system",
        "net-define",
    ]
    assert by_label["net-mgmt autostart"].command.argv[-2:] == ["net-autostart", "net-mgmt"]
    assert by_label["net-mgmt start"].command.argv[-2:] == ["net-start", "net-mgmt"]


def test_vms_build_steps_vagrant_up_members_and_commons(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    argv = steps[0].command.argv
    assert argv[0] == "vagrant"
    assert argv[1] == "up"
    targets = argv[2:]
    # stack members + commons VMs (obs_box/probe/svc/app), in order, deduped
    assert "pcmk-a1" in targets
    assert "pcmk-b1" in targets
    assert "obs" in targets
    assert "mon-probe" in targets
    # svc-sim and app-client are shared commons (spec §6); all_vms must include them
    assert "svc-sim" in targets
    assert "app-client" in targets


def test_provision_build_steps_playbook_and_qm_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[2].build_steps(stack, None)
    argv = steps[0].command.argv
    assert argv[0] == "ansible-playbook"
    assert "site-pcmk.yml" in argv  # the stack's provision playbook (basename)
    # #351 QM extra-vars sourced from stack.qm (names DERIVE from short)
    assert "qm_app=PCMKAPP" in argv
    assert "qm_svc=PCMKSVC" in argv
    assert "chl_to_svc=PCMKAPP.PCMKSVC" in argv
    assert "chl_to_app=PCMKSVC.PCMKAPP" in argv


def test_provision_build_steps_raises_when_no_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # nativeha-ubuntu is reserved: provision: null — build_steps must fail loud.
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
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    stack = lab_stacks()["nativeha-ubuntu"]
    with pytest.raises(ValueError, match="provision"):
        PHASES[2].build_steps(stack, None)


def test_observe_build_steps_render_and_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[3].build_steps(stack, None)
    labels = [s.label for s in steps]
    assert any("targets" in lab_ for lab_ in labels)
    # net-reach peers are rendered before observability.yml consumes them (#381)
    assert any(s.command.argv == ["mqlab", "obs", "reach-peers"] for s in steps)
    playbooks = [s.command.argv for s in steps if s.command.argv[0] == "ansible-playbook"]
    names = {argv[1] for argv in playbooks}
    # site-obs.yml (obs box) + observability.yml (cluster nodes, #382) +
    # host-obs.yml (libvirt host: node-exporter for virbr-* throughput +
    # host-net-state for lab_network_health, #383).
    assert names == {"site-obs.yml", "observability.yml", "host-obs.yml"}
    # the QM-bearing playbooks carry the #351 QM extra-vars; host-obs.yml is
    # host-side/net-agnostic and runs connection=local instead.
    for argv in playbooks:
        if argv[1] in ("site-obs.yml", "observability.yml"):
            assert "qm_app=PCMKAPP" in argv
    # site-obs.yml loops the mq-exporter role over the `mq_exporters` extra-var; it
    # is a JSON list, so it is passed as a file (`-e @<mq_exporters_path>`). #423 wired
    # this only into the `obs up` call site — the observe phase must carry it too or
    # mon-probe fails on an undefined `mq_exporters` (#434). Pins the two call sites.
    site_argv = next(a for a in playbooks if a[1] == "site-obs.yml")
    exporters_ref = f"@{mq_exporters_path()}"
    assert exporters_ref in site_argv
    assert site_argv[site_argv.index(exporters_ref) - 1] == "-e"
    host_argv = next(a for a in playbooks if a[1] == "host-obs.yml")
    assert host_argv[2:6] == ["-c", "local", "-i", "localhost,"]
    # host-obs.yml is passed the host-runnable mqlab the net-state service calls
    # by absolute path — beside the running interpreter, never the repo .venv (#398).
    mqlab_bin = next(a for a in host_argv if a.startswith("mqlab_bin="))
    assert host_argv[host_argv.index(mqlab_bin) - 1] == "-e"
    assert mqlab_bin == f"mqlab_bin={Path(sys.executable).resolve().parent / 'mqlab'}"
    obs_argv = next(a for a in playbooks if a[1] == "observability.yml")
    # observability.yml is `hosts: all`, so it must be --limited to THIS stack's
    # nodes (cluster + commons) — a cluster node + a commons node both appear.
    limit = obs_argv[obs_argv.index("--limit") + 1]
    assert "pcmk-a1" in limit and "svc-sim" in limit
    # site-obs bounces grafana -> heal the relay, then fail-loud verify the
    # workstation-facing endpoint actually serves (#264/#383).
    argvs = [s.command.argv for s in steps]
    assert any(a[:3] == ["sudo", "systemctl", "restart"] for a in argvs)
    assert any(a[0] == "curl" and a[-1].endswith("/api/health") for a in argvs)


def test_all_vms_includes_svc_and_app(monkeypatch, tmp_path):
    """all_vms must include svc-sim and app-client for any stack.

    svc-sim/app-client live in the shared commons (groups svc/app). Before the
    topology fix they were in NEITHER the stack groups NOR the old commons groups
    ([obs_box, probe]), so all_vms omitted them and the vms phase never vagrant-up'd
    them — the distributed message path never started. This test fails against the
    old commons groups and passes with the corrected [obs_box, probe, svc, app].
    """
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    from mqlab.phases import all_vms

    stack = lab_stacks()["pcmk-ubuntu"]
    vms = all_vms(stack)
    assert "svc-sim" in vms, "svc-sim must be in all_vms (shared commons svc group)"
    assert "app-client" in vms, "app-client must be in all_vms (shared commons app group)"


def test_all_vms_dedupes_overlap(monkeypatch, tmp_path):
    """A host shared between a commons group AND a stack group is listed once.

    Exercises both dedup branches: commons groups overlap (obs in two commons
    groups) and a stack member that is also a commons member.
    """
    topo = (
        "nodes:\n"
        "  h1: {}\n"
        "  obs: {}\n"
        "groups:\n"
        "  stack_grp: [h1, obs]\n"  # obs is both a stack member and commons
        "  obs_box:   [obs]\n"
        "  probe:     [obs]\n"  # obs again -> commons-group overlap
        "stacks:\n"
        "  my-stack:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: TEST\n"
        "    groups: [stack_grp]\n"
        "    provision: ansible/site-x.yml\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
        "commons:\n"
        "  groups: [obs_box, probe]\n"
        "  provision: ansible/site-obs.yml\n"
    )
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    stack = lab_stacks()["my-stack"]
    steps = PHASES[1].build_steps(stack, None)
    targets = steps[0].command.argv[2:]
    assert targets.count("obs") == 1  # listed once despite three appearances
    assert targets == ["h1", "obs"]


def test_build_states_helper_shape():
    """build_states is a pure test/fixture helper exported for Task 4 parity."""
    s = build_states(nets={"n": "active"}, domains={"lab_h": "running"}, qm_up=True, observe=False)
    assert s == {
        "nets": {"n": "active"},
        "domains": {"lab_h": "running"},
        "qm_up": True,
        "observe": False,
    }
