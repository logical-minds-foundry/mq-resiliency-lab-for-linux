from __future__ import annotations

from mqlab.inventory import lab_inventory
from mqlab.setups import lab_groups, lab_setups


def test_real_topology_renders_without_error():
    out = lab_inventory()  # raises InventoryError on any integrity problem
    assert "[all:vars]" in out
    assert out.endswith("ansible_python_interpreter=/usr/bin/python3\n")


def test_every_setup_group_is_defined():
    groups = lab_groups()
    for setup in lab_setups().values():
        for g in setup.groups:
            assert g in groups, f"{setup.name} references undefined group {g}"


def test_pcmk_setups_declare_the_hacluster_secret():
    setups = lab_setups()
    assert setups["pcmk_san_ha"].secrets == ["pcmk_hacluster_password"]
    assert setups["pcmk_san_dr"].secrets == ["pcmk_hacluster_password"]
    assert setups["standalone"].secrets == ["mqweb_admin_password"]


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
