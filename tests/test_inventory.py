from __future__ import annotations

import pytest

from mqlab.inventory import InventoryError, render_inventory

TOPO = {
    "nodes": {
        "rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}},
        "rdqm-b1": {"nics": {"net-mgmt": "10.50.0.41"}},
        "rdqm-b2": {"nics": {"net-mgmt": "10.50.0.42"}},
    },
    "groups": {
        "rdqm_a": ["rdqm-a1"],
        "rdqm_b": ["rdqm-b1", "rdqm-b2"],
    },
    "stacks": {
        "rdqm-rhel": {"groups": ["rdqm_a", "rdqm_b"], "provision": "ansible/site-rdqm.yml"},
    },
}


def test_render_emits_groups_children_and_vars_in_order():
    out = render_inventory(TOPO)
    assert out == (
        "[rdqm_a]\n"
        "rdqm-a1 ansible_host=10.50.0.31\n"
        "[rdqm_b]\n"
        "rdqm-b1 ansible_host=10.50.0.41\n"
        "rdqm-b2 ansible_host=10.50.0.42\n"
        # stack-aggregate group: hyphens -> underscores (#350)
        "[rdqm_rhel:children]\n"
        "rdqm_a\n"
        "rdqm_b\n"
        "[all:vars]\n"
        "ansible_user=vagrant\n"
        "ansible_ssh_private_key_file=~/.vagrant.d/insecure_private_key\n"
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no\n"
        "ansible_python_interpreter=/usr/bin/python3\n"
    )


def test_reserved_stack_with_no_groups_emits_no_aggregate():
    topo = {
        "nodes": {"rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}}},
        "groups": {"rdqm_a": ["rdqm-a1"]},
        "stacks": {
            "rdqm-rhel": {"groups": ["rdqm_a"]},
            "nativeha-ubuntu": {"groups": []},  # reserved — no aggregate
        },
    }
    out = render_inventory(topo)
    assert "[rdqm_rhel:children]" in out
    assert "nativeha_ubuntu" not in out


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"rdqm-a1": {"nics": {}}}, "groups": {"rdqm_a": ["rdqm-a1"]}, "stacks": {}}
    with pytest.raises(InventoryError, match="no net-mgmt IP: rdqm-a1"):
        render_inventory(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"rdqm_a": ["rdqm-a1"]}, "stacks": {}}
    with pytest.raises(InventoryError, match="undefined host: rdqm-a1"):
        render_inventory(topo)


def test_stack_referencing_undefined_group_raises():
    topo = {
        "nodes": {"rdqm-a1": {"nics": {"net-mgmt": "10.50.0.31"}}},
        "groups": {"rdqm_a": ["rdqm-a1"]},
        "stacks": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(InventoryError, match="undefined group: nope"):
        render_inventory(topo)
