from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.roster import lab_roster
from mqlab.stacks import (
    lab_stacks,
    stack_dr_hosts,
    stack_members,
    stack_members_effective,
)


def _lab_groups() -> dict[str, list[str]]:
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return {g: list(h) for g, h in (topo.get("groups") or {}).items()}


def test_real_topology_renders_without_error():
    out = lab_inventory()  # raises InventoryError on any integrity problem
    assert "[all:vars]" in out
    assert out.endswith("ansible_python_interpreter=/usr/bin/python3\n")


def test_real_topology_renders_roster():
    out = lab_roster()  # raises RosterError on any integrity problem
    assert out.startswith("# salt-ssh roster")
    assert "minion_opts:" in out


def test_every_stack_group_is_defined():
    groups = _lab_groups()
    for stack in lab_stacks().values():
        for g in stack.groups:
            assert g in groups, f"{stack.name} references undefined group {g}"


def test_pcmk_stack_declares_hacluster_and_mqweb_secrets():
    # the QM-bearing pcmk stack carries the cluster secret AND mqweb_admin_password
    # (REST on every QM — design §1; mqweb is enabled at provision where it's injected)
    pcmk = lab_stacks()["pcmk-ubuntu"]
    assert pcmk.secrets == ["pcmk_hacluster_password", "mqweb_admin_password"]


def test_real_topology_renders_scrape_targets():
    import json

    from mqlab.scrape import lab_scrape_targets

    entries = json.loads(lab_scrape_targets())  # raises ScrapeError on any missing mgmt IP
    hosts = {e["labels"]["host"] for e in entries}
    assert {"obs", "mon-probe"} <= hosts
    assert all(e["targets"][0].endswith(":9100") for e in entries)


def test_real_topology_renders_a_valid_dashboard():
    import json

    from mqlab.dashboard import DASHBOARD_UID, lab_dashboard

    dash = json.loads(lab_dashboard())  # raises DashboardError on an unknown ROWS group
    assert dash["uid"] == DASHBOARD_UID
    row_titles = [p["title"] for p in dash["panels"] if p["type"] == "row"]
    # every curated VM row is present against the real groups (SAN folds into PCMK)
    assert "VMs · PCMK · A" in row_titles and "VMs · RDQM · B" in row_titles


def test_network_sections_cover_exactly_the_declared_networks():
    from mqlab.dashboard import NET_SECTIONS
    from mqlab.netsel import lab_net_names

    curated = {net for _, nets in NET_SECTIONS for net in nets}
    assert curated == set(lab_net_names()), "NET_SECTIONS must match lab/networks/net-*.xml"


def test_pcmk_nodes_attach_to_net_ext():
    """Both Pacemaker sites carry a net-ext NIC so the partner VIP (mq_vip_ext)
    can bind on the inter-business WAN (#146)."""
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    nodes = topo["nodes"]
    expected = {
        "pcmk-a1": "10.60.0.51",
        "pcmk-a2": "10.60.0.52",
        "pcmk-a3": "10.60.0.53",
        "pcmk-b1": "10.60.0.61",
        "pcmk-b2": "10.60.0.62",
        "pcmk-b3": "10.60.0.63",
    }
    for host, ip in expected.items():
        assert nodes[host]["nics"].get("net-ext") == ip, f"{host} missing net-ext {ip}"


def test_svc_sim_on_net_ext():
    """The SVC service VM joins net-ext so its QM can reach our partner VIP and be
    reached across the inter-business WAN (#147)."""
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    assert topo["nodes"]["svc-sim"]["nics"].get("net-ext") == "10.60.0.50"


def test_pcmk_stack_composed():
    """The pcmk-ubuntu stack is full HADR; its QM wires to the SVC counterparty (#147)."""
    dist = lab_stacks()["pcmk-ubuntu"]
    assert dist.groups == ["san_a", "pcmk_a", "san_b", "pcmk_b"]
    assert dist.provision == "ansible/site-pcmk.yml"
    assert dist.qm.qm_app == "PCMKAPP"  # short-derived (#351)
    assert dist.qm.qm_svc == "SVCQM"  # single shared counterparty (#446)
    assert dist.qm.chl_to_svc == "PCMKAPP.SVCQM"
    assert dist.qm.req_queue == "PCMK.SVC.REQUEST"  # this stack's own queue on SVCQM
    assert dist.qm.svc_conn == "10.60.0.50"


def test_rdqm_stack_composed():
    """The RDQM stack mirrors pcmk's QM wiring, over the RDQM substrate (#216)."""
    s = lab_stacks()["rdqm-rhel"]
    assert s.mechanism == "rdqm"
    assert s.groups == ["rdqm_a", "rdqm_b"]
    assert s.provision == "ansible/site-rdqm.yml"
    assert s.qm.qm_app == "RDQMAPP"  # (#351)
    assert s.qm.qm_svc == "SVCQM"  # single shared counterparty (#446)
    assert s.qm.req_queue == "RDQM.SVC.REQUEST"
    assert s.qm.svc_conn == "10.60.0.50"
    assert "mqweb_admin_password" in s.secrets


def test_rdqm_rhel_declares_its_dr_groups():
    # #188: the dr_groups marker names the DR (site-B) group `bootstrap --no-dr` skips.
    # Its presence is the signal that this stack supports HA-only bring-up.
    assert lab_stacks()["rdqm-rhel"].dr_groups == ["rdqm_b"]


def test_rdqm_rhel_dr_hosts_are_site_b():
    # #188: the dr_groups marker resolves to exactly the three site-B guests.
    assert stack_dr_hosts("rdqm-rhel") == ["rdqm-b1", "rdqm-b2", "rdqm-b3"]


def test_rdqm_rhel_effective_members_drop_site_b_under_no_dr():
    # #188: --no-dr brings up the site-A members only; a full bootstrap is unchanged.
    full = stack_members("rdqm-rhel")
    assert full is not None
    effective = stack_members_effective("rdqm-rhel", no_dr=True)
    assert effective is not None
    assert effective == [m for m in full if not m.startswith("rdqm-b")]
    assert "rdqm-b1" not in effective
    assert stack_members_effective("rdqm-rhel", no_dr=False) == full


def test_logsearch_node_present_and_mgmt_only():
    """The logsearch node (#830, epic .github#149): boots the baked
    logsearch-ubuntu2404 box, sized for the OpenSearch/Data Prepper stack, and
    lives on the management plane ONLY (one net-mgmt NIC — it ingests logs from
    Alloy over mgmt, no data/hb/san/ext exposure)."""
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    node = topo["nodes"]["logsearch"]
    assert node["platform"] == "logsearch-ubuntu2404"
    assert node["memory"] >= 6144
    assert set(node["nics"]) == {"net-mgmt"}


def test_logsearch_box_group_renders_but_is_not_a_commons_group():
    """The logsearch node lives in the `logsearch_box` group (#832) so the inventory
    renders it (site-logsearch.yml `hosts: logsearch` + the `mqlab logsearch` ad-hoc
    calls resolve it). It is NOT a commons group: logsearch is wired into commons
    up/status/down explicitly, but kept out of the per-stack all_vms boot set."""
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    assert topo["groups"]["logsearch_box"] == ["logsearch"]
    assert "logsearch_box" not in topo["commons"]["groups"]
    # the inventory (a pure function of topology) renders the host under the group
    inv = lab_inventory()
    assert "[logsearch_box]" in inv
    assert "logsearch ansible_host=10.50.0.4" in inv


def test_dns_infra_nodes_present_and_attached():
    """The DNS infra nodes (#474): infra-client is authoritative-to-be for
    client.com and reaches our guests on the mgmt + both data planes; infra-svc
    is the mock service.com nameserver on the inter-business WAN. Both join the
    `infra` group, which commons brings up with the shared set."""
    import yaml

    from mqlab.paths import repo_root

    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    nodes = topo["nodes"]
    assert nodes["infra-client"]["nics"] == {
        "net-mgmt": "10.50.0.8",
        "net-data-a": "10.10.1.8",
        "net-data-b": "10.10.2.8",
        "net-ext": "10.60.0.8",
    }
    assert nodes["infra-svc"]["nics"] == {
        "net-mgmt": "10.50.0.9",
        "net-ext": "10.60.0.9",
    }
    assert topo["groups"]["infra"] == ["infra-client", "infra-svc"]
    assert "infra" in topo["commons"]["groups"]
