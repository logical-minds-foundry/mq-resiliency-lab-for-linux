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
from mqlab.paths import lab_script
from mqlab.phases import (
    _DEFAULT_BOOT_BATCH,
    _HOST_RESOLVED_BASE_BOX,
    PHASES,
    _batch_guests,
    _batch_shares_box,
    _boot_batch,
    _guest_box,
    _nic_assure_steps,
    _nic_config_steps,
    build_states,
    first_unsatisfied,
)
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
    "    dr_groups: [pcmk_b]\n"
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


# --- logsearch membership (#1018): its own group, but a CORE observability layer ---
# logsearch is kept as its own group (logsearch_box), NOT folded into the commons
# groups — but it IS included in all_vms so every bootstrap boots + provisions it.
# Alloy ships to it unconditionally, so an opt-in consumer (#832) let the fan-out
# hot-loop into a disk-fill; observability is a requirement, not opt-in.

_LOGSEARCH_TOPO = TOPO.replace(
    "  app-client: {}\n",
    "  app-client: {}\n  logsearch: {nics: {net-mgmt: 10.50.0.4}}\n",
).replace(
    "  app:     [app-client]\n",
    "  app:     [app-client]\n  logsearch_box: [logsearch]\n",
)


def test_logsearch_members_reads_logsearch_box_group(monkeypatch, tmp_path):
    from mqlab.phases import _logsearch_members

    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_LOGSEARCH_TOPO)
    assert _logsearch_members() == ["logsearch"]


def test_logsearch_members_empty_when_group_absent(monkeypatch, tmp_path):
    from mqlab.phases import _logsearch_members

    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # base TOPO has no logsearch_box group
    assert _logsearch_members() == []


def test_logsearch_in_all_vms_but_not_a_commons_member(monkeypatch, tmp_path):
    # logsearch is its own group (never a commons member), yet all_vms includes it so
    # every bootstrap boots + provisions the tier — a core observability layer (#1018).
    from mqlab.phases import _commons_members, all_vms

    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True, exist_ok=True)
    (tmp_path / "lab" / "topology.yaml").write_text(_LOGSEARCH_TOPO)
    assert "logsearch" not in _commons_members()
    assert "logsearch" in all_vms(lab_stacks()["pcmk-ubuntu"])


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


def test_net_build_steps_delegates_to_idempotent_net_up(monkeypatch, tmp_path):
    # The net phase must be idempotent: re-running it against already-existing /
    # already-active networks (e.g. after a base-VM reboot) must NOT error (#974).
    # Idempotency is single-sourced in lab/scripts/net-up.sh — which defines only if
    # absent and starts only if not active — so the phase delegates to it rather than
    # emitting a bare `virsh net-define`/`net-start` triple that fails on a
    # pre-existing net. phases.py is pure, so we assert the delegation structurally.
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[0].build_steps(stack, None)
    assert all(isinstance(s, CommandStep) for s in steps)
    # one step that runs the idempotent script over every lab net (sorted names)
    assert [s.label for s in steps] == ["networks up"]
    argv = steps[0].command.argv
    assert argv[0] == "bash"
    assert argv[1] == str(lab_script("net-up.sh"))
    # the lab nets are passed as args, so net-up.sh acts on exactly the required set
    assert argv[2:] == ["net-data-a", "net-mgmt"]
    # the bug being fixed: NO bare unguarded net-define is emitted by the phase itself
    assert "net-define" not in argv


# The seeded topology's ordered VM list for pcmk-ubuntu: stack members (groups
# [san_a, pcmk_a, pcmk_b]) then commons (obs_box/probe/svc/app), deduped — the exact
# HADR bring-up order the vms phase must batch WITHOUT reordering.
_EXPECTED_VMS = [
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


def _seed_boot_batch(tmp_path, value) -> None:
    """Seed the base topology plus an explicit `boot_batch: <value>` line."""
    _seed(tmp_path)
    (tmp_path / "lab" / "topology.yaml").write_text(f"{TOPO}boot_batch: {value}\n")


def _batch_payload(step) -> list[str]:
    """The guest names of one `vagrant up [--no-parallel] <batch>` step, with the
    optional #859 serialization flag stripped — so the ordering/chunking assertions
    stay independent of whether that batch shares a box."""
    argv = step.command.argv
    assert argv[0] == "vagrant"
    assert argv[1] == "up"
    rest = argv[2:]
    if rest and rest[0] == "--no-parallel":
        rest = rest[1:]
    return rest


def _is_serial(step) -> bool:
    """True iff this batch step carries the #859 --no-parallel serialization flag."""
    return "--no-parallel" in step.command.argv


def _batched_targets(steps) -> list[str]:
    """Flatten the per-batch `vagrant up <batch>` steps back into one ordered list,
    asserting each step is a well-formed vagrant-up call (flag stripped)."""
    flat: list[str] = []
    for step in steps:
        flat.extend(_batch_payload(step))
    return flat


def test_vms_build_steps_vagrant_up_members_and_commons(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    # Batches concatenate back to the full ordered VM set (members + commons), deduped.
    targets = _batched_targets(steps)
    assert "pcmk-a1" in targets
    assert "pcmk-b1" in targets
    assert "obs" in targets
    assert "mon-probe" in targets
    # svc-sim and app-client are shared commons (spec §6); all_vms must include them
    assert "svc-sim" in targets
    assert "app-client" in targets


def test_vms_build_steps_batches_default_n_preserving_order(monkeypatch, tmp_path):
    """The default (unset boot_batch) chunks the ordered list into ceil(L/4) batches,
    each a contiguous slice — no reordering (#638)."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # no boot_batch → default 4
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    # 9 VMs, N=4 → ceil(9/4) = 3 batches, contiguous chunks in order.
    assert [_batch_payload(s) for s in steps] == [
        _EXPECTED_VMS[0:4],
        _EXPECTED_VMS[4:8],
        _EXPECTED_VMS[8:9],
    ]
    # Concatenation preserves the authoritative HADR order exactly.
    assert _batched_targets(steps) == _EXPECTED_VMS
    # These fixture nodes declare no platform, so they all boot the one host-resolved
    # base box: the two multi-guest batches share a box and serialize (#859); the lone
    # trailing guest has no in-batch contention, so it keeps the parallel default.
    assert [_is_serial(s) for s in steps] == [True, True, False]
    # Multi-batch runs carry a [i/n] progress label for a readable transcript.
    assert [s.label for s in steps] == [
        "pcmk-ubuntu vms up [1/3]",
        "pcmk-ubuntu vms up [2/3]",
        "pcmk-ubuntu vms up [3/3]",
    ]


def test_vms_build_steps_custom_boot_batch(monkeypatch, tmp_path):
    """A topology `boot_batch: 2` yields ceil(9/2) = 5 ordered contiguous batches."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_boot_batch(tmp_path, 2)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    assert len(steps) == 5
    assert [_batch_payload(s) for s in steps] == [
        _EXPECTED_VMS[0:2],
        _EXPECTED_VMS[2:4],
        _EXPECTED_VMS[4:6],
        _EXPECTED_VMS[6:8],
        _EXPECTED_VMS[8:9],
    ]
    assert _batched_targets(steps) == _EXPECTED_VMS


def test_vms_build_steps_serial_when_batch_one(monkeypatch, tmp_path):
    """boot_batch: 1 is fully serial — one guest per `vagrant up`, order preserved."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_boot_batch(tmp_path, 1)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    assert len(steps) == len(_EXPECTED_VMS)
    # One guest per batch → no in-batch contention → no serialization flag (#859).
    assert all(_batch_payload(s) == [g] for s, g in zip(steps, _EXPECTED_VMS, strict=True))
    assert not any(_is_serial(s) for s in steps)
    assert _batched_targets(steps) == _EXPECTED_VMS


def test_vms_build_steps_single_batch_when_n_ge_len(monkeypatch, tmp_path):
    """A boot_batch >= the VM count collapses to one `vagrant up` (old all-at-once),
    labelled without the [i/n] progress suffix."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_boot_batch(tmp_path, 99)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[1].build_steps(stack, None)
    assert len(steps) == 1
    assert steps[0].label == "pcmk-ubuntu vms up"
    assert _batch_payload(steps[0]) == _EXPECTED_VMS


@pytest.mark.parametrize("bad", [0, -3, "four", 2.5, True])
def test_boot_batch_rejects_non_positive_int(monkeypatch, tmp_path, bad):
    """A garbled dial fails loud rather than silently dropping/reordering VMs."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_boot_batch(tmp_path, bad)
    with pytest.raises(ValueError, match="boot_batch must be a positive integer"):
        _boot_batch()


def test_boot_batch_defaults_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)  # no boot_batch key
    assert _boot_batch() == _DEFAULT_BOOT_BATCH


@pytest.mark.parametrize(
    ("items", "size", "expected"),
    [
        ([], 4, []),  # empty list → no batches
        (["a"], 4, [["a"]]),  # L < N → one batch
        (["a", "b", "c"], 3, [["a", "b", "c"]]),  # N == L → one batch
        (["a", "b", "c"], 9, [["a", "b", "c"]]),  # N > L → one batch
        (["a", "b", "c"], 1, [["a"], ["b"], ["c"]]),  # N == 1 → serial
        (["a", "b", "c", "d", "e"], 2, [["a", "b"], ["c", "d"], ["e"]]),  # remainder
        (["a", "b", "c", "d"], 2, [["a", "b"], ["c", "d"]]),  # exact multiple
    ],
)
def test_batch_guests_contiguous_chunks(items, size, expected):
    """_batch_guests emits ceil(L/N) contiguous, in-order chunks for every edge case."""
    assert _batch_guests(items, size) == expected


# --- #859: same-box boot serialization ------------------------------------- #

# A topology slice exercising the box-contention key: two fat-box platforms mapping
# to distinct boxes, a shared platform (two guests, one box), and platform-less guests
# (the host-resolved base box). No file I/O — the helpers take a topo dict directly.
_BOX_TOPO = {
    "boxes": {
        "fat-rhel": {"box": "mq-nativeha-rhel9"},
        "fat-ubuntu": {"box": "mq-ubuntu2404"},
    },
    "nodes": {
        "nha-rhel-a1": {"platform": "fat-rhel"},
        "nha-rhel-a2": {"platform": "fat-rhel"},
        "svc-sim": {"platform": "fat-ubuntu"},
        "san-a": {},  # no platform → host-resolved base box
        "san-b": {},  # no platform → host-resolved base box
    },
}


def test_guest_box_resolves_platform_to_box():
    """A guest's contention key is the box its platform clones (via the registry)."""
    assert _guest_box("nha-rhel-a1", _BOX_TOPO) == "mq-nativeha-rhel9"
    assert _guest_box("svc-sim", _BOX_TOPO) == "mq-ubuntu2404"


def test_guest_box_platformless_folds_to_host_resolved_base():
    """Guests with no platform (and group hosts absent from nodes:) all clone the one
    host-resolved base box, so they share the sentinel key."""
    assert _guest_box("san-a", _BOX_TOPO) == _HOST_RESOLVED_BASE_BOX
    assert _guest_box("san-b", _BOX_TOPO) == _HOST_RESOLVED_BASE_BOX
    # A group host not present in nodes: is treated as platform-less, not an error.
    assert _guest_box("not-a-node", _BOX_TOPO) == _HOST_RESOLVED_BASE_BOX


def test_guest_box_unknown_platform_fails_loud():
    """A platform absent from the boxes: registry is a garbled dial — fail loud rather
    than silently mis-group the boot."""
    topo = {"boxes": {}, "nodes": {"x": {"platform": "ghost"}}}
    with pytest.raises(ValueError, match="unknown platform 'ghost'"):
        _guest_box("x", topo)


@pytest.mark.parametrize(
    ("batch", "expected"),
    [
        (["nha-rhel-a1", "nha-rhel-a2"], True),  # same fat box → contends
        (["nha-rhel-a1", "svc-sim"], False),  # distinct boxes → no contention
        (["san-a", "san-b"], True),  # both host-resolved base box → contends
        (["nha-rhel-a1", "svc-sim", "san-a"], False),  # all distinct
        (["nha-rhel-a1", "svc-sim", "nha-rhel-a2"], True),  # one repeat is enough
        (["nha-rhel-a1"], False),  # a lone guest never contends with itself
        ([], False),  # empty batch → nothing to serialize
    ],
)
def test_batch_shares_box(batch, expected):
    """A batch needs --no-parallel iff two+ of its guests clone the same box (#859)."""
    assert _batch_shares_box(batch, _BOX_TOPO) is expected


def test_provision_build_steps_playbook_and_qm_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[2].build_steps(stack, None)
    # DNS comes up first (#478): render the zones, then serve + point resolvers
    assert steps[0].command.argv == ["mqlab", "dns", "render"]
    dns_argv = steps[1].command.argv
    assert dns_argv[0] == "ansible-playbook"
    assert "site-dns.yml" in dns_argv
    assert "--limit" in dns_argv
    # then the stack's own provision playbook
    argv = steps[2].command.argv
    assert argv[0] == "ansible-playbook"
    assert "site-pcmk.yml" in argv  # the stack's provision playbook (basename)
    # #351 QM extra-vars sourced from stack.qm (names DERIVE from short)
    assert "qm_app=PCMKAPP" in argv
    assert "qm_svc=SVCQM" in argv  # single shared counterparty (#446)
    assert "chl_to_svc=PCMKAPP.SVCQM" in argv
    assert "chl_to_app=SVCQM.PCMKAPP" in argv
    assert "svc_req_queue=PCMK.SVC.REQUEST" in argv  # this stack's own queue on SVCQM


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
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
    )
    lab = tmp_path / "lab"
    (lab / "networks").mkdir(parents=True)
    (lab / "topology.yaml").write_text(topo)
    stack = lab_stacks()["nativeha-ubuntu"]
    with pytest.raises(ValueError, match="provision"):
        PHASES[2].build_steps(stack, None)


# A RHEL stack whose group mixes a RHEL node with NICs (guarded), a RHEL node with
# no NICs (skipped), and an Ubuntu node (skipped) — exercises every branch of the
# #860 NIC-assurance guard. Commons are Ubuntu and always skipped.
RHEL_TOPO = (
    "nodes:\n"
    "  rdqm-a1: { platform: mq-rdqm-rhel9, nics: {net-mgmt: 10.50.0.31, net-hb-a: 172.16.1.31} }\n"
    "  rdqm-a2: { platform: mq-rdqm-rhel9, nics: {} }\n"
    "  ubu-1:   { platform: mq-ubuntu2404, nics: { net-mgmt: 10.50.0.99 } }\n"
    "  obs: {}\n"
    "  mon-probe: {}\n"
    "  svc-sim: {}\n"
    "  app-client: {}\n"
    "groups:\n"
    "  rdqm_a:  [rdqm-a1, rdqm-a2, ubu-1]\n"
    "  obs_box: [obs]\n"
    "  probe:   [mon-probe]\n"
    "  svc:     [svc-sim]\n"
    "  app:     [app-client]\n"
    "stacks:\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n"
    "    os: rhel\n"
    "    short: RDQM\n"
    "    groups: [rdqm_a]\n"
    "    provision: ansible/site-rdqm.yml\n"
    "    secrets: [mqweb_admin_password]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9157\n"
    "      exporter_svc_port: 9158\n"
    "      app_unit: app-rdqm\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-status: { rdqmstatus: '-m RDQM' }\n"
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
    "commons:\n"
    "  groups: [obs_box, probe, svc, app]\n"
    "  provision: ansible/site-obs.yml\n"
)


def _seed_rhel(tmp_path):
    lab = tmp_path / "lab"
    nets = lab / "networks"
    nets.mkdir(parents=True)
    (lab / "topology.yaml").write_text(RHEL_TOPO)
    for n in ("net-mgmt", "net-data-a"):
        (nets / f"{n}.xml").write_text(NET_XML.format(name=n))


def test_nic_assure_steps_guards_rhel_nodes_only(monkeypatch, tmp_path):
    """#860: the guard emits an upload + a sudo nic-assure.sh run for each RHEL node
    that declares NICs, passing that node's topology IPs as the expected set — and
    skips Ubuntu nodes (netplan, unaffected) and RHEL nodes with no NICs."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_rhel(tmp_path)
    stack = lab_stacks()["rdqm-rhel"]
    steps = _nic_assure_steps(stack)
    # Only rdqm-a1 is guarded (rdqm-a2 has no NICs; ubu-1 is Ubuntu) → exactly 2 steps.
    dest = "/home/vagrant/nic-assure.sh"
    assert [s.command.argv for s in steps] == [
        ["vagrant", "upload", "scripts/nic-assure.sh", dest, "rdqm-a1"],
        ["vagrant", "ssh", "rdqm-a1", "-c", f"sudo bash {dest} 10.50.0.31 172.16.1.31"],
    ]
    # vagrant commands run from lab/ (the resolved-topology consumer dir).
    assert all(s.command.cwd == tmp_path / "lab" for s in steps)


def test_nic_config_steps_configure_rhel_nodes_only(monkeypatch, tmp_path):
    """#866: the NM-native config step emits an upload + a sudo nic-config.sh run for
    each RHEL node that declares NICs, passing that node's topology IPs — and, like the
    guard, skips Ubuntu nodes (netplan, unaffected) and RHEL nodes with no NICs."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_rhel(tmp_path)
    stack = lab_stacks()["rdqm-rhel"]
    steps = _nic_config_steps(stack)
    # Only rdqm-a1 is configured (rdqm-a2 has no NICs; ubu-1 is Ubuntu) → exactly 2 steps.
    dest = "/home/vagrant/nic-config.sh"
    assert [s.command.argv for s in steps] == [
        ["vagrant", "upload", "scripts/nic-config.sh", dest, "rdqm-a1"],
        ["vagrant", "ssh", "rdqm-a1", "-c", f"sudo bash {dest} 10.50.0.31 172.16.1.31"],
    ]
    # vagrant commands run from lab/ (the resolved-topology consumer dir).
    assert all(s.command.cwd == tmp_path / "lab" for s in steps)


def test_provision_configures_then_assures_before_dns(monkeypatch, tmp_path):
    """#866 + #860: on a RHEL stack the NM-native config step runs first, then the
    assurance guard, then DNS/provision — so every NIC is authoritatively brought up
    (and asserted) before any Ansible-over-net-mgmt play. The guard stays in place as
    defense-in-depth after the root-cause config step."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed_rhel(tmp_path)
    stack = lab_stacks()["rdqm-rhel"]
    steps = PHASES[2].build_steps(stack, None)
    # nic-config (root-cause) precedes nic-assure (backstop): both are (upload, ssh).
    assert steps[0].command.argv == [
        "vagrant",
        "upload",
        "scripts/nic-config.sh",
        "/home/vagrant/nic-config.sh",
        "rdqm-a1",
    ]
    assert steps[1].command.argv[:2] == ["vagrant", "ssh"]
    assert "nic-config.sh" in steps[1].command.argv[-1]
    assert steps[2].command.argv == [
        "vagrant",
        "upload",
        "scripts/nic-assure.sh",
        "/home/vagrant/nic-assure.sh",
        "rdqm-a1",
    ]
    assert steps[3].command.argv[:2] == ["vagrant", "ssh"]
    assert "nic-assure.sh" in steps[3].command.argv[-1]
    # DNS + provision follow the two NIC passes.
    assert steps[4].command.argv == ["mqlab", "dns", "render"]
    assert "site-dns.yml" in steps[5].command.argv
    assert "site-rdqm.yml" in steps[6].command.argv


def test_observe_build_steps_render_and_playbook(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[3].build_steps(stack, None)
    labels = [s.label for s in steps]
    assert any("targets" in lab_ for lab_ in labels)
    # the exporter deployment list is scoped to THIS stack (#503), so observing one stack
    # never deploys another stack's (crash-looping) exporter unit
    assert any(
        s.command.argv == ["mqlab", "obs", "targets", "--stack", "pcmk-ubuntu"] for s in steps
    )
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
    # by absolute path (the service runs with a minimal PATH): the console script
    # beside the interpreter driving this bootstrap — the host venv's own mqlab.
    mqlab_bin = next(a for a in host_argv if a.startswith("mqlab_bin="))
    assert host_argv[host_argv.index(mqlab_bin) - 1] == "-e"
    # NOT .resolve(): resolving the .venv/bin/python3 symlink lands on the base
    # interpreter and the sibling becomes a nonexistent /usr/bin/mqlab (203/EXEC, #984).
    assert mqlab_bin == f"mqlab_bin={Path(sys.executable).parent / 'mqlab'}"
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
        "svc: { short: SVC, conn: 10.60.0.50, exporter_port: 9158 }\n"
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
    targets = _batch_payload(steps[0])
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


# --------------------------------------------------------------------------- #
# --no-dr phase shaping (#188). The seeded pcmk-ubuntu declares dr_groups: [pcmk_b],
# so pcmk-b1 is the DR (site-B) guest the effective-member phases must drop.
# --------------------------------------------------------------------------- #
def test_vms_no_dr_brings_up_site_a_only(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    targets = _batched_targets(PHASES[1].build_steps(stack, None, no_dr=True))
    assert "pcmk-a1" in targets  # site-A HA guests still come up
    assert "pcmk-b1" not in targets  # the DR guest is skipped
    assert "obs" in targets and "svc-sim" in targets  # commons are always included


def test_vms_full_brings_up_both_sites(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    targets = _batched_targets(PHASES[1].build_steps(stack, None, no_dr=False))
    assert "pcmk-b1" in targets  # default keeps the full HADR set


def _provision_step(steps):
    return next(s for s in steps if s.label.endswith("provision"))


def test_provision_no_dr_emits_dr_enabled_false(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[2].build_steps(stack, None, no_dr=True)
    argv = _provision_step(steps).command.argv
    assert "-e" in argv
    assert "dr_enabled=false" in argv
    # the site-dns --limit must also drop the absent DR guest (else UNREACHABLE)
    dns_argv = next(s for s in steps if "site-dns.yml" in s.command.argv).command.argv
    limit = dns_argv[dns_argv.index("--limit") + 1]
    assert "pcmk-a1" in limit and "pcmk-b1" not in limit


def test_provision_full_omits_dr_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[2].build_steps(stack, None, no_dr=False)
    assert not any("dr_enabled" in a for a in _provision_step(steps).command.argv)


def test_observe_no_dr_limits_to_site_a(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stack = lab_stacks()["pcmk-ubuntu"]
    steps = PHASES[3].build_steps(stack, None, no_dr=True)
    obs_argv = next(s.command.argv for s in steps if "observability.yml" in s.command.argv)
    limit = obs_argv[obs_argv.index("--limit") + 1]
    assert "pcmk-a1" in limit
    assert "pcmk-b1" not in limit
