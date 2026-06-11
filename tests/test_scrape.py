from __future__ import annotations

import json

import pytest

from mqlab.scrape import ScrapeError, render_scrape_targets

TOPO = {
    "nodes": {
        "obs": {"nics": {"net-mgmt": "10.50.0.2"}},
        "qm-main": {"nics": {"net-mgmt": "10.50.0.10"}},
        "rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}},
    },
    "groups": {
        "obs": ["obs"],
        "qm": ["qm-main"],
        "rdqm_a": ["rdqm-a1"],
    },
}


def test_render_emits_file_sd_one_entry_per_node_on_9100():
    from mqlab.scrape import HYPERVISOR_MGMT_IP

    out = json.loads(render_scrape_targets(TOPO))
    assert out == [
        {"targets": ["10.50.0.2:9100"], "labels": {"host": "obs", "groups": "obs"}},
        {"targets": ["10.50.0.10:9100"], "labels": {"host": "qm-main", "groups": "qm"}},
        {"targets": ["10.50.0.31:9100"], "labels": {"host": "rdqm-a1", "groups": "rdqm_a"}},
        {
            "targets": [f"{HYPERVISOR_MGMT_IP}:9100"],
            "labels": {"host": "hypervisor", "groups": "hypervisor"},
        },
    ]


def test_includes_the_hypervisor_host_target():
    from mqlab.scrape import HYPERVISOR_MGMT_IP

    out = json.loads(render_scrape_targets({"nodes": {}, "groups": {}}))
    assert {
        "targets": [f"{HYPERVISOR_MGMT_IP}:9100"],
        "labels": {"host": "hypervisor", "groups": "hypervisor"},
    } in out


def test_node_in_multiple_groups_joins_labels_sorted():
    topo = {
        "nodes": {"n1": {"nics": {"net-mgmt": "10.50.0.9"}}},
        "groups": {"z_grp": ["n1"], "a_grp": ["n1"]},
    }
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"]["groups"] == "a_grp,z_grp"


def test_node_in_no_group_gets_empty_groups_label():
    topo = {"nodes": {"lonely": {"nics": {"net-mgmt": "10.50.0.8"}}}, "groups": {}}
    out = json.loads(render_scrape_targets(topo))
    assert out[0]["labels"] == {"host": "lonely", "groups": ""}


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"bad": {"nics": {}}}, "groups": {}}
    with pytest.raises(ScrapeError, match="no net-mgmt IP: bad"):
        render_scrape_targets(topo)
