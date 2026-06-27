from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.roster import lab_roster
from mqlab.stacks import lab_stacks


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
    assert dist.qm.qm_app == "PCMKAPP" and dist.qm.qm_svc == "PCMKSVC"  # short-derived (#351)
    assert dist.qm.chl_to_svc == "PCMKAPP.PCMKSVC"
    assert dist.qm.svc_conn == "10.60.0.50"


def test_rdqm_stack_composed():
    """The RDQM stack mirrors pcmk's QM wiring, over the RDQM substrate (#216)."""
    s = lab_stacks()["rdqm-rhel"]
    assert s.mechanism == "rdqm"
    assert s.groups == ["rdqm_a", "rdqm_b"]
    assert s.provision == "ansible/site-rdqm.yml"
    assert s.qm.qm_app == "RDQMAPP" and s.qm.qm_svc == "RDQMSVC"  # (#351)
    assert s.qm.svc_conn == "10.60.0.50"
    assert "mqweb_admin_password" in s.secrets
