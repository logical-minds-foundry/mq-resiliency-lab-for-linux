"""Tests for the Stack model (mqlab.stacks) introduced in #350 Task 1a.

The seeded topology mirrors the real stacks: block but is self-contained
so the tests run without a real lab checkout.
"""

from __future__ import annotations

from mqlab.stacks import lab_stacks, stack_members

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


def test_nativeha_ubuntu_is_reserved(monkeypatch, tmp_path):
    """nativeha-ubuntu is reserved: groups=[], provision=None."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    nhu = stacks["nativeha-ubuntu"]
    assert nhu.groups == []
    assert nhu.provision is None


def test_qm_names_derive_from_short(monkeypatch, tmp_path):
    """Each stack's QmConfig derives names from short (#351)."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    stacks = lab_stacks()
    assert stacks["pcmk-ubuntu"].qm.qm_app == "PCMKAPP"
    assert stacks["pcmk-ubuntu"].qm.qm_svc == "PCMKSVC"
    assert stacks["rdqm-rhel"].qm.qm_app == "RDQMAPP"
    assert stacks["rdqm-rhel"].qm.qm_svc == "RDQMSVC"
    assert stacks["nativeha-rhel"].qm.qm_app == "NHARAPP"
    assert stacks["nativeha-rhel"].qm.qm_svc == "NHARSVC"
    assert stacks["nativeha-ubuntu"].qm.qm_app == "NHAUAPP"
    assert stacks["nativeha-ubuntu"].qm.qm_svc == "NHAUSVC"
