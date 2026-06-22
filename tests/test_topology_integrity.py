from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.roster import lab_roster
from mqlab.setups import lab_groups, lab_setups


def test_real_topology_renders_without_error():
    out = lab_inventory()  # raises InventoryError on any integrity problem
    assert "[all:vars]" in out
    assert out.endswith("ansible_python_interpreter=/usr/bin/python3\n")


def test_real_topology_renders_roster():
    out = lab_roster()  # raises RosterError on any integrity problem
    assert out.startswith("# salt-ssh roster")
    assert "minion_opts:" in out


def test_every_setup_group_is_defined():
    groups = lab_groups()
    for setup in lab_setups().values():
        for g in setup.groups:
            assert g in groups, f"{setup.name} references undefined group {g}"


def test_pcmk_setups_declare_hacluster_and_mqweb_secrets():
    # every QM-bearing pcmk setup carries the cluster secret AND mqweb_admin_password
    # (REST on every QM — design §1; mqweb is enabled at provision where it's injected)
    setups = lab_setups()
    assert setups["pcmk_san_ha"].secrets == ["pcmk_hacluster_password", "mqweb_admin_password"]
    assert setups["pcmk_san_dr"].secrets == ["pcmk_hacluster_password", "mqweb_admin_password"]
    assert setups["distributed-pcmk-ubuntu"].secrets == [
        "pcmk_hacluster_password",
        "mqweb_admin_password",
    ]


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


def test_distributed_setup_composed():
    """The distributed setup wires our HA QM (site A) to the SVC service VM (#147)."""
    from mqlab.setups import lab_setups

    dist = lab_setups()["distributed-pcmk-ubuntu"]
    assert dist.groups == ["san_a", "pcmk_a", "svc", "app"]
    assert dist.provision == "ansible/site-distributed.yml"
    assert dist.qm is not None and dist.qm.name == "QMPCMK"
    assert dist.qm.svc_conn == "10.60.0.50"


def test_distributed_rdqm_setup_composed():
    """The RDQM arm's distributed setup mirrors pcmk's, over the RDQM substrate (#216)."""
    from mqlab.setups import lab_setups

    s = lab_setups()["distributed-rdqm-rhel"]
    assert s.arm == "rdqm-rhel"
    assert s.groups == ["rdqm_a", "svc", "app"]
    assert s.provision == "ansible/site-rdqm-distributed.yml"
    assert s.qm is not None and s.qm.name == "QMRDQM"
    assert s.qm.svc_conn == "10.60.0.50"
    assert "mqweb_admin_password" in s.secrets
