"""Topology coverage for the Native HA stacks — nativeha-rhel (#246, #267, #350) and
its OS-as-only-variable peer nativeha-ubuntu (#417).

Each stack is ONE consolidated full-HADR stack (not the older _ha/_dr triple), and —
unlike RDQM/Pacemaker — Native HA has no floating VIP, so the QM must parse without one.
The two arms share the mqmonitor@ verbs/mechanism; only OS, groups, QM names, and the
provision playbook differ.
"""

import pathlib

import yaml

from mqlab.stacks import lab_stacks


def _topology() -> dict:
    return yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())


def test_nativeha_rhel_stack_uses_mqmonitor_verbs():
    stack = lab_stacks()["nativeha-rhel"]
    assert stack.mechanism == "native-ha"
    # lifecycle is the mqmonitor@ systemd unit, NOT endmqm/strmqm/pcs
    assert "mqmonitor@" in stack.verbs["qm-up"]["cmd"]
    assert "mqmonitor@" in stack.verbs["qm-down"]["cmd"]
    # CRR / DR verbs (Phase 3): planned cross-site switchover
    assert stack.verbs["dr-cutover"]["playbook"] == "site-nativeha-switchover.yml"
    assert stack.verbs["dr-failback"]["playbook"] == "site-nativeha-switchover.yml"
    # diagnostics capture (runmqras, read-only)
    assert "runmqras" in stack.verbs["diagnostics"]["cmd"]


def test_one_consolidated_full_hadr_stack():
    stacks = lab_stacks()
    s = stacks["nativeha-rhel"]
    # the keystone: both sites (HA + DR) in one stack (#267); commons (svc/app) are
    # shared and provisioned separately, so they are NOT in the stack's groups (#350)
    assert set(s.groups) == {"nha_rhel_a", "nha_rhel_b"}
    # no partial throwaway stacks
    assert "nativeha_ha" not in stacks
    assert "nativeha_dr" not in stacks


def test_platform_qualified_node_groups():
    g = _topology()["groups"]
    assert set(g["nha_rhel_a"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}
    assert set(g["nha_rhel_b"]) == {"nha-rhel-b1", "nha-rhel-b2", "nha-rhel-b3"}


def test_nativeha_rhel_nodes_boot_the_baked_fat_box():
    # #88/#668: the six nha-rhel-* nodes boot the baked mq-nativeha-rhel9 fat box
    # (not the bare rhel96-x86_64), so a bootstrap skips the MQ install. No kernel pin
    # and no extra_disk — Native HA replicates in MQ's raft log, not DRBD.
    nodes = _topology()["nodes"]
    for h in (
        "nha-rhel-a1",
        "nha-rhel-a2",
        "nha-rhel-a3",
        "nha-rhel-b1",
        "nha-rhel-b2",
        "nha-rhel-b3",
    ):
        assert nodes[h]["platform"] == "mq-nativeha-rhel9"
        assert "extra_disk" not in nodes[h]


def test_stack_parses_without_a_vip():
    # Native HA has no floating VIP (multi-instance CONNAME list instead);
    # QmConfig.vip must be optional for the stack to parse.
    s = lab_stacks()["nativeha-rhel"]
    assert s.qm.qm_app == "NHARAPP"  # short-derived (#351)
    assert s.qm.qm_svc == "SVCQM"  # single shared counterparty (#446)
    assert s.qm.req_queue == "NHAR.SVC.REQUEST"
    assert s.qm.vip == ""


# --- nativeha-ubuntu (#417): the Ubuntu peer arm. Same mechanism/verbs as RHEL,
# only the OS, groups, QM names, and provision playbook differ. ---


def test_nativeha_ubuntu_stack_uses_mqmonitor_verbs():
    stack = lab_stacks()["nativeha-ubuntu"]
    assert stack.mechanism == "native-ha"
    assert stack.os == "ubuntu"
    # same lifecycle as the RHEL arm: the mqmonitor@ systemd unit
    assert "mqmonitor@" in stack.verbs["qm-up"]["cmd"]
    assert "mqmonitor@" in stack.verbs["qm-down"]["cmd"]
    # CRR / DR verbs route to the Ubuntu switchover playbook
    assert stack.verbs["dr-cutover"]["playbook"] == "site-nativeha-ubuntu-switchover.yml"
    assert stack.verbs["dr-failback"]["playbook"] == "site-nativeha-ubuntu-switchover.yml"
    assert "runmqras" in stack.verbs["diagnostics"]["cmd"]


def test_nativeha_ubuntu_one_consolidated_full_hadr_stack():
    stacks = lab_stacks()
    s = stacks["nativeha-ubuntu"]
    # both sites (HA + DR) in one stack (#267); commons (svc/app) are shared (#350)
    assert set(s.groups) == {"nha_ubuntu_a", "nha_ubuntu_b"}
    assert s.cluster_group == "nha_ubuntu_a"
    assert s.provision == "ansible/site-nativeha-ubuntu.yml"


def test_nativeha_ubuntu_node_groups_and_host_resolved_platform():
    topo = _topology()
    g = topo["groups"]
    assert set(g["nha_ubuntu_a"]) == {"nha-ubuntu-a1", "nha-ubuntu-a2", "nha-ubuntu-a3"}
    assert set(g["nha_ubuntu_b"]) == {"nha-ubuntu-b1", "nha-ubuntu-b2", "nha-ubuntu-b3"}
    # The nodes now boot the baked mq-nativeha-ubuntu fat box (#103 T6), but host-arch
    # resolution is PRESERVED — it just moved to the box layer: unlike the x86-pinned
    # mq-nativeha-rhel9 box, the mq-nativeha-ubuntu box carries NO `arch:` pin, so it
    # tracks the host arch (native arm64 on the Mac) and can coexist with pcmk-ubuntu.
    assert "arch" not in topo["boxes"]["mq-nativeha-ubuntu"]


def test_nativeha_ubuntu_nodes_boot_the_baked_fat_box():
    # #103 T6: the six nha-ubuntu-* nodes boot the baked, host-resolved mq-nativeha-ubuntu
    # fat box (not the bare host-resolved Ubuntu base), so a bootstrap skips the MQ install.
    # No extra_disk — Native HA replicates in MQ's raft log, not DRBD (mirrors the RHEL arm).
    nodes = _topology()["nodes"]
    for h in (
        "nha-ubuntu-a1",
        "nha-ubuntu-a2",
        "nha-ubuntu-a3",
        "nha-ubuntu-b1",
        "nha-ubuntu-b2",
        "nha-ubuntu-b3",
    ):
        assert nodes[h]["platform"] == "mq-nativeha-ubuntu"
        assert "extra_disk" not in nodes[h]


def test_nativeha_ubuntu_stack_parses_without_a_vip():
    s = lab_stacks()["nativeha-ubuntu"]
    assert s.qm.qm_app == "NHAUAPP"  # short-derived (#351)
    assert s.qm.qm_svc == "SVCQM"  # single shared counterparty (#446)
    assert s.qm.req_queue == "NHAU.SVC.REQUEST"
    assert s.qm.vip == ""


def test_nativeha_arms_use_collision_free_resources():
    """The two coexisting stacks must not share exporter ports, app_unit, or node IPs."""
    stacks = lab_stacks()
    rhel, ubuntu = stacks["nativeha-rhel"], stacks["nativeha-ubuntu"]
    # distinct app exporter ports + scrape unit so both can run at once (#417). The
    # svc exporter is now a single shared SVCQM target (#446), so there is no per-stack
    # svc port to collide on.
    assert ubuntu.alloc["exporter_app_port"] != rhel.alloc["exporter_app_port"]
    assert "exporter_svc_port" not in ubuntu.alloc and "exporter_svc_port" not in rhel.alloc
    assert ubuntu.alloc["app_unit"] != rhel.alloc["app_unit"]
    # no IP collision across ALL node NICs in the topology (the pcmk-ubuntu arm runs too)
    topo = _topology()
    nodes = topo["nodes"]
    ubuntu_hosts = set(topo["groups"]["nha_ubuntu_a"]) | set(topo["groups"]["nha_ubuntu_b"])
    ubuntu_ips: set[str] = set()
    other_ips: set[str] = set()
    for host, spec in nodes.items():
        for ip in (spec.get("nics") or {}).values():
            (ubuntu_ips if host in ubuntu_hosts else other_ips).add(ip)
    assert ubuntu_ips.isdisjoint(other_ips)
