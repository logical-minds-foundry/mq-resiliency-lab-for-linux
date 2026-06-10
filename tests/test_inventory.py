from __future__ import annotations

import pytest

from mqlab.inventory import InventoryError, render_inventory

TOPO = {
    "nodes": {
        "san-a": {"nics": {"net-mgmt": "10.50.0.5"}},
        "pcmk-a1": {"nics": {"net-mgmt": "10.50.0.51"}},
        "pcmk-a2": {"nics": {"net-mgmt": "10.50.0.52"}},
    },
    "groups": {
        "san_a": ["san-a"],
        "pcmk_a": ["pcmk-a1", "pcmk-a2"],
    },
    "setups": {
        "pcmk_san_ha": {"groups": ["san_a", "pcmk_a"], "provision": "ansible/site-pcmk.yml"},
    },
}


def test_render_emits_groups_children_and_vars_in_order():
    out = render_inventory(TOPO)
    assert out == (
        "[san_a]\n"
        "san-a ansible_host=10.50.0.5\n"
        "[pcmk_a]\n"
        "pcmk-a1 ansible_host=10.50.0.51\n"
        "pcmk-a2 ansible_host=10.50.0.52\n"
        "[pcmk_san_ha:children]\n"
        "san_a\n"
        "pcmk_a\n"
        "[all:vars]\n"
        "ansible_user=vagrant\n"
        "ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key\n"
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no\n"
        "ansible_python_interpreter=/usr/bin/python3\n"
    )


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"san-a": {"nics": {}}}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(InventoryError, match="no net-mgmt IP: san-a"):
        render_inventory(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"san_a": ["san-a"]}, "setups": {}}
    with pytest.raises(InventoryError, match="undefined host: san-a"):
        render_inventory(topo)


def test_setup_referencing_undefined_group_raises():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "setups": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(InventoryError, match="undefined group: nope"):
        render_inventory(topo)
