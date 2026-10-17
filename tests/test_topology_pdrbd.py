"""Topology coverage for the pcmk-drbd-ubuntu stack (epic .github#310 T4, #1426).

RDQM-faithful open-source arm: shared-nothing (per-node extra disk, no SAN), one floating
IP per site like RDQM, operator-driven DR. Coexists with the other arms (disjoint octets).
"""

from __future__ import annotations

import ipaddress

import yaml

from mqlab.paths import repo_root
from mqlab.stacks import lab_stacks, stack_dr_hosts, stack_members, stack_members_effective
from mqlab.versions import OsRef, load_catalog

_A = ("pdrbd-a1", "pdrbd-a2", "pdrbd-a3")
_B = ("pdrbd-b1", "pdrbd-b2", "pdrbd-b3")


def _topology() -> dict:
    return yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())


def test_pdrbd_stack_shape():
    s = lab_stacks()["pcmk-drbd-ubuntu"]
    assert (s.mechanism, s.os_family, s.short) == ("pacemaker-drbd", "ubuntu", "PDRBD")
    assert s.qm.qm_app == "PDRBDAPP"
    assert s.cluster_group == "pdrbd_a"
    assert s.groups == ["pdrbd_a", "pdrbd_b"]
    assert s.dr_groups == ["pdrbd_b"]
    assert s.provision == "ansible/site-pdrbd.yml"
    assert s.dashboard_folder == "PCMK-DRBD (Ubuntu)"


def test_pdrbd_verbs():
    v = lab_stacks()["pcmk-drbd-ubuntu"].verbs
    assert v["qm-create"] == {"playbook": "site-pdrbd.yml"}
    assert v["qm-destroy"] == {"playbook": "site-pdrbd-qm-down.yml"}
    assert v["qm-up"] == {"pcs": "resource enable mq_group"}
    assert v["qm-down"] == {"pcs": "resource disable mq_group"}
    # drbdadm status all omits the stacked DR resource (blueprint README §2.1); it stays only
    # as the gate (it fails with no resource up), drbdsetup shows both resources.
    assert "drbdsetup status --verbose" in v["qm-status"]["cmd"]
    assert "drbdadm status all >/dev/null" in v["qm-status"]["cmd"]
    # `mqlab dr` (#1424) dispatches on these; rpo0: the playbook seeds + verifies RPO-0.
    assert v["dr-cutover"] == {"playbook": "site-pdrbd-dr-switch.yml", "rpo0": True}
    assert v["dr-failback"] == {"playbook": "site-pdrbd-dr-switch.yml", "rpo0": True}
    assert "runmqras" in v["diagnostics"]["cmd"]


def test_pdrbd_like_rdqm_has_one_floating_ip_per_site_and_no_ext_vip():
    qm = _topology()["stacks"]["pcmk-drbd-ubuntu"]["qm"]
    assert set(qm) == {"vip", "vip_b"}


def test_pdrbd_nodes_are_shared_nothing_on_the_baked_box():
    nodes = _topology()["nodes"]
    for h in (*_A, *_B):
        assert nodes[h]["box"] == "pdrbd"
        assert nodes[h]["extra_disk"] == 10
        assert not any(n.startswith("net-san") for n in nodes[h]["nics"])


def test_pdrbd_dr_hosts_and_no_dr_members():
    assert stack_dr_hosts("pcmk-drbd-ubuntu") == list(_B)
    assert stack_members("pcmk-drbd-ubuntu") == [*_A, *_B]
    assert stack_members_effective("pcmk-drbd-ubuntu", no_dr=True) == list(_A)


def test_pdrbd_addresses_collide_with_no_other_node():
    seen: dict[str, str] = {}
    for host, spec in _topology()["nodes"].items():
        for ip in (spec.get("nics") or {}).values():
            ipaddress.ip_address(ip)
            assert ip not in seen, f"{ip} on {host} and {seen.get(ip)}"
            seen[ip] = host


def test_pdrbd_vips_and_alloc_are_unique_across_stacks():
    stacks = _topology()["stacks"]
    mine = stacks["pcmk-drbd-ubuntu"]
    for name, other in stacks.items():
        if name == "pcmk-drbd-ubuntu":
            continue
        assert not set(mine["qm"].values()) & set((other.get("qm") or {}).values()), name
        assert mine["alloc"]["exporter_app_port"] != other["alloc"]["exporter_app_port"], name
        assert mine["alloc"]["app_unit"] != other["alloc"]["app_unit"], name


def test_pdrbd_os_catalog_entry():
    cat = load_catalog()
    assert cat.stacks["pcmk-drbd-ubuntu"]["supported"] == [OsRef("ubuntu", 24), OsRef("ubuntu", 26)]
    assert cat.stacks["pcmk-drbd-ubuntu"]["default"] == OsRef("ubuntu", 26)


def test_pdrbd_peer_vars_match_topology():
    peers = yaml.safe_load((repo_root() / "ansible" / "vars" / "pdrbd-peers.yml").read_text())[
        "pdrbd_peers_all"
    ]
    nodes = _topology()["nodes"]
    assert [p["name"] for p in peers] == [*_A, *_B]
    for p in peers:
        nics = nodes[p["name"]]["nics"]
        hb = nics.get("net-hb-a") or nics.get("net-hb-b")
        assert (p["addr_hb"], p["addr_wan"]) == (hb, nics["net-wan"]), p["name"]
    for site in ("a", "b"):  # site-local HA node-ids 0-2, as RDQM numbers each site
        assert sorted(p["node_id"] for p in peers if p["site"] == site) == [0, 1, 2]
