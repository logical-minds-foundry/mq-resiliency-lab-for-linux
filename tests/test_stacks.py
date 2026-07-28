"""Tests for the Stack model (mqlab.stacks) introduced in #350 Task 1a.

The seeded topology mirrors the real stacks: block but is self-contained
so the tests run without a real lab checkout.
"""

from __future__ import annotations

import pytest

from mqlab.stacks import (
    QmConfig,
    _svc_identity,
    dashboard_folder_for,
    lab_stacks,
    stack_members,
    stack_san_targets,
)


def test_qmconfig_derives_app_svc_and_channel_pair() -> None:
    # svc is the single shared counterparty (SVCQM), not a per-stack {short}SVC (#446)
    qm = QmConfig(
        name="PCMKAPP", short="PCMK", vip="10.10.1.200", vip_ext="10.60.0.10", svc="SVCQM"
    )
    assert qm.qm_app == "PCMKAPP"
    assert qm.qm_svc == "SVCQM"
    assert qm.chl_to_svc == "PCMKAPP.SVCQM"
    assert qm.chl_to_app == "SVCQM.PCMKAPP"


def test_qmconfig_svc_defaults_empty() -> None:
    qm = QmConfig(name="PCMKAPP")
    assert qm.svc == ""  # no retired-name default
    assert qm.qm_svc == ""


def test_qmconfig_req_queue_derives_from_short() -> None:
    qm = QmConfig(name="PCMKAPP", short="PCMK", svc="SVCQM", svc_conn="10.60.0.50")
    assert qm.req_queue == "PCMK.SVC.REQUEST"  # this stack's own queue on SVCQM (1b)


def test_qmconfig_req_queue_empty_without_short() -> None:
    assert QmConfig(name="PCMKAPP").req_queue == ""


def test_svc_identity_reads_the_svc_block() -> None:
    topo = {"svc": {"short": "SVC", "conn": "10.60.0.50"}}
    assert _svc_identity(topo) == ("SVCQM", "10.60.0.50")


def test_svc_identity_fail_loud_when_incomplete() -> None:
    with pytest.raises(ValueError, match="svc"):
        _svc_identity({"svc": {"short": "SVC"}})  # no conn


# Minimal seeded topology covering all 4 stacks + the groups they reference.
TOPO = (
    "nodes:\n"
    "  san-a: {}\n"
    "  san-b: {}\n"
    "  pcmk-a1: {}\n"
    "  pcmk-a2: {}\n"
    "  pcmk-a3: {}\n"
    "  pcmk-b1: {}\n"
    "  pcmk-b2: {}\n"
    "  pcmk-b3: {}\n"
    "  rdqm-a1: {}\n"
    "  rdqm-a2: {}\n"
    "  rdqm-a3: {}\n"
    "  rdqm-b1: {}\n"
    "  rdqm-b2: {}\n"
    "  rdqm-b3: {}\n"
    "  nha-rhel-a1: {}\n"
    "  nha-rhel-a2: {}\n"
    "  nha-rhel-a3: {}\n"
    "  nha-rhel-b1: {}\n"
    "  nha-rhel-b2: {}\n"
    "  nha-rhel-b3: {}\n"
    "groups:\n"
    "  san_a:      [san-a]\n"
    "  san_b:      [san-b]\n"
    "  pcmk_a:     [pcmk-a1, pcmk-a2, pcmk-a3]\n"
    "  pcmk_b:     [pcmk-b1, pcmk-b2, pcmk-b3]\n"
    "  rdqm_a:     [rdqm-a1, rdqm-a2, rdqm-a3]\n"
    "  rdqm_b:     [rdqm-b1, rdqm-b2, rdqm-b3]\n"
    "  nha_rhel_a: [nha-rhel-a1, nha-rhel-a2, nha-rhel-a3]\n"
    "  nha_rhel_b: [nha-rhel-b1, nha-rhel-b2, nha-rhel-b3]\n"
    "stacks:\n"
    "  pcmk-ubuntu:\n"
    "    mechanism: pacemaker-san\n"
    "    os: ubuntu\n"
    "    short: PCMK\n"
    "    cluster_group: pcmk_a\n"
    "    groups: [san_a, pcmk_a, san_b, pcmk_b]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "    secrets: [pcmk_hacluster_password, mqweb_admin_password]\n"
    "    qm: { vip: 10.10.1.200, vip_ext: 10.60.0.10, svc_conn: 10.60.0.50 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9157\n"
    "      exporter_svc_port: 9158\n"
    "      app_unit: app-pcmk\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-create:  { playbook: site-pcmk-qm.yml }\n"
    "      qm-destroy: { playbook: site-pcmk-qm-down.yml }\n"
    "      qm-up:      { pcs: 'resource enable mq_group' }\n"
    "      qm-down:    { pcs: 'resource disable mq_group' }\n"
    "      qm-status:  { pcs: 'status resources' }\n"
    "  rdqm-rhel:\n"
    "    mechanism: rdqm\n"
    "    os: rhel\n"
    "    short: RDQM\n"
    "    cluster_group: rdqm_a\n"
    "    groups: [rdqm_a, rdqm_b]\n"
    "    provision: ansible/site-rdqm.yml\n"
    "    secrets: [mqweb_admin_password]\n"
    "    qm: { vip: 10.10.1.100, svc_conn: 10.60.0.50 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9159\n"
    "      exporter_svc_port: 9160\n"
    "      app_unit: app-rdqm\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-create: { script: rdqm-qm-create.sh }\n"
    "      qm-status: { cmd: '/opt/mqm/bin/rdqmstatus -m {qm}' }\n"
    "      qm-up:     { cmd: \"su mqm -c '/opt/mqm/bin/strmqm {qm}'\" }\n"
    "      qm-down:   { cmd: \"su mqm -c '/opt/mqm/bin/endmqm -w {qm}'\" }\n"
    "  nativeha-rhel:\n"
    "    mechanism: native-ha\n"
    "    os: rhel\n"
    "    short: NHAR\n"
    "    cluster_group: nha_rhel_a\n"
    "    groups: [nha_rhel_a, nha_rhel_b]\n"
    "    provision: ansible/site-nativeha.yml\n"
    "    secrets: [mqweb_admin_password]\n"
    "    qm: { svc_conn: 10.60.0.50 }\n"
    "    alloc:\n"
    "      exporter_app_port: 9161\n"
    "      exporter_svc_port: 9162\n"
    "      app_unit: app-nhar\n"
    "      svc_port: 1414\n"
    "    verbs:\n"
    "      qm-create:   { playbook: site-nativeha.yml }\n"
    "      qm-status:   { cmd: \"su - mqm -c '/opt/mqm/bin/dspmq -m {qm} -o nativeha -x'\" }\n"
    "      qm-up:       { cmd: 'systemctl start mqmonitor@{qm}' }\n"
    "      qm-down:     { cmd: 'systemctl stop mqmonitor@{qm}' }\n"
    "      dr-cutover:  { playbook: site-nativeha-switchover.yml }\n"
    "      dr-failback: { playbook: site-nativeha-switchover.yml }\n"
    "      diagnostics: { cmd: \"su - mqm -c '/opt/mqm/bin/runmqras -qmlist {qm}'\" }\n"
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
    "svc: { short: SVC, conn: 10.60.0.50, listener_port: 1414, exporter_port: 9158 }\n"
)


def _seed(tmp_path):
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(TOPO)


def test_four_canonical_stacks(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    assert set(stacks) == {"pcmk-ubuntu", "rdqm-rhel", "nativeha-rhel", "nativeha-ubuntu"}
    pu = stacks["pcmk-ubuntu"]
    assert pu.mechanism == "pacemaker-san"
    assert pu.os == "ubuntu"
    assert pu.short == "PCMK"
    assert pu.qm.qm_app == "PCMKAPP"  # name derives from short (#351)
    assert "pcmk_a" in pu.groups and "pcmk_b" in pu.groups  # full HADR shape


def test_stack_members_flattens_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    members = stack_members("pcmk-ubuntu")
    assert members is not None
    assert "pcmk-a1" in members
    assert "pcmk-b1" in members  # site B in canonical shape


def test_stack_members_unknown_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert stack_members("no-such-stack") is None


def test_stack_members_skips_unknown_group(monkeypatch, tmp_path):
    """A group name in stacks: that is not in groups: is silently skipped (returns [])."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    # Topology with a stack that references a group not in groups: block.
    topo = (
        "nodes:\n"
        "  h1: {}\n"
        "groups:\n"
        "  real_group: [h1]\n"
        "stacks:\n"
        "  my-stack:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: TEST\n"
        "    groups: [real_group, phantom_group]\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
    )
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)
    members = stack_members("my-stack")
    # phantom_group absent from groups: — only real_group's hosts appear
    assert members == ["h1"]


def test_stack_members_dedupes_host_in_multiple_groups(monkeypatch, tmp_path):
    """A host appearing in more than one of a stack's groups is listed only once."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    topo = (
        "nodes:\n"
        "  h1: {}\n"
        "  h2: {}\n"
        "groups:\n"
        "  grp_a: [h1, h2]\n"
        "  grp_b: [h1]\n"
        "stacks:\n"
        "  my-stack:\n"
        "    mechanism: rdqm\n"
        "    os: rhel\n"
        "    short: TEST\n"
        "    groups: [grp_a, grp_b]\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
    )
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)
    members = stack_members("my-stack")
    assert members == ["h1", "h2"]  # h1 appears in both grp_a and grp_b — listed once


def test_stack_san_targets_returns_san_hosts_for_pcmk(monkeypatch, tmp_path):
    """The pacemaker-san stack's SAN targets are its san_* group hosts, in order."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert stack_san_targets("pcmk-ubuntu") == ["san-a", "san-b"]


def test_stack_san_targets_empty_for_non_san_stacks(monkeypatch, tmp_path):
    """A stack with no san_* groups (rdqm / native-ha) has no SAN targets to pre-cache."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert stack_san_targets("rdqm-rhel") == []
    assert stack_san_targets("nativeha-rhel") == []


def test_stack_san_targets_unknown_stack_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert stack_san_targets("no-such-stack") == []


def test_stack_san_targets_dedupes_and_skips_phantom_groups(monkeypatch, tmp_path):
    """A san_* group missing from groups: is skipped; a host in two san groups is listed once."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    topo = (
        "nodes:\n"
        "  san-x: {}\n"
        "groups:\n"
        "  san_a: [san-x]\n"
        "  san_b: [san-x]\n"
        "stacks:\n"
        "  my-stack:\n"
        "    mechanism: pacemaker-san\n"
        "    os: ubuntu\n"
        "    short: TEST\n"
        "    groups: [san_a, san_b, san_phantom, pcmk_a]\n"
        "    provision: null\n"
        "    secrets: []\n"
        "    qm: {}\n"
        "    alloc: {}\n"
        "    verbs: {}\n"
    )
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(topo)
    # san-x is in both san_a and san_b (listed once); san_phantom is skipped; the
    # non-san pcmk_a group is ignored even though it is in the stack's groups.
    assert stack_san_targets("my-stack") == ["san-x"]


def test_cluster_group_populated_for_real_stacks(monkeypatch, tmp_path):
    """cluster_group is set to the cluster node group, not the SAN or other first group."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    # pcmk-ubuntu: groups[0] is san_a (SAN host), cluster_group must be pcmk_a
    assert stacks["pcmk-ubuntu"].cluster_group == "pcmk_a"
    # rdqm-rhel and nativeha-rhel also carry their cluster_group
    assert stacks["rdqm-rhel"].cluster_group == "rdqm_a"
    assert stacks["nativeha-rhel"].cluster_group == "nha_rhel_a"


def test_nativeha_ubuntu_is_reserved(monkeypatch, tmp_path):
    """nativeha-ubuntu is reserved: groups=[], provision=None, cluster_group=None."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    nhu = stacks["nativeha-ubuntu"]
    assert nhu.groups == []
    assert nhu.provision is None
    # Reserved stack has no cluster_group — omitting the key from topology yields None.
    assert nhu.cluster_group is None


def test_qm_names_derive_from_short(monkeypatch, tmp_path):
    """Each stack's app QM derives from short (#351); the svc QM is the single shared
    SVCQM for every stack (#446)."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    assert stacks["pcmk-ubuntu"].qm.qm_app == "PCMKAPP"
    assert stacks["rdqm-rhel"].qm.qm_app == "RDQMAPP"
    assert stacks["nativeha-rhel"].qm.qm_app == "NHARAPP"
    assert stacks["nativeha-ubuntu"].qm.qm_app == "NHAUAPP"
    # svc is shared across all stacks
    assert {s.qm.qm_svc for s in stacks.values()} == {"SVCQM"}
    # each stack still owns a distinct request queue on that shared SVCQM
    assert stacks["pcmk-ubuntu"].qm.req_queue == "PCMK.SVC.REQUEST"
    assert stacks["nativeha-rhel"].qm.req_queue == "NHAR.SVC.REQUEST"


def test_dashboard_folder_derives_from_mechanism_and_os(monkeypatch, tmp_path):
    # Each stack folders under a "<Mechanism> (<OS>)" label derived from mechanism+os,
    # so a new stack needs no separate folder literal (#59).
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    assert stacks["pcmk-ubuntu"].dashboard_folder == "PCMK (Ubuntu)"
    assert stacks["rdqm-rhel"].dashboard_folder == "RDQM (RHEL)"
    assert stacks["nativeha-rhel"].dashboard_folder == "Native HA (RHEL)"
    assert stacks["nativeha-ubuntu"].dashboard_folder == "Native HA (Ubuntu)"


def test_dashboard_folder_for_fails_loud_on_unlabelled_mechanism_or_os():
    # A mechanism or OS with no folder label is a loud error, never a silent mis-folder.
    with pytest.raises(ValueError, match="mechanism"):
        dashboard_folder_for("no-such-mechanism", "ubuntu")
    with pytest.raises(ValueError, match="os"):
        dashboard_folder_for("native-ha", "no-such-os")
