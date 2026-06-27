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
    "stacks": {
        "pcmk-ubuntu": {"groups": ["san_a", "pcmk_a"], "provision": "ansible/site-pcmk.yml"},
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
        # stack-aggregate group: hyphens -> underscores (#350)
        "[pcmk_ubuntu:children]\n"
        "san_a\n"
        "pcmk_a\n"
        "[all:vars]\n"
        "ansible_user=vagrant\n"
        "ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key\n"
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no\n"
        "ansible_python_interpreter=/usr/bin/python3\n"
    )


def test_reserved_stack_with_no_groups_emits_no_aggregate():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "stacks": {
            "pcmk-ubuntu": {"groups": ["san_a"]},
            "nativeha-ubuntu": {"groups": []},  # reserved — no aggregate
        },
    }
    out = render_inventory(topo)
    assert "[pcmk_ubuntu:children]" in out
    assert "nativeha_ubuntu" not in out


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"san-a": {"nics": {}}}, "groups": {"san_a": ["san-a"]}, "stacks": {}}
    with pytest.raises(InventoryError, match="no net-mgmt IP: san-a"):
        render_inventory(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"san_a": ["san-a"]}, "stacks": {}}
    with pytest.raises(InventoryError, match="undefined host: san-a"):
        render_inventory(topo)


def test_stack_referencing_undefined_group_raises():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "stacks": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(InventoryError, match="undefined group: nope"):
        render_inventory(topo)
