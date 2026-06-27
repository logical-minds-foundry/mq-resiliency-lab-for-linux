from __future__ import annotations

import pytest

from mqlab.roster import RosterError, render_roster

TOPO = {
    "nodes": {
        "san-a": {"nics": {"net-mgmt": "10.50.0.5"}},
        "pcmk-a1": {"nics": {"net-mgmt": "10.50.0.51"}},
    },
    "groups": {
        "san_a": ["san-a"],
        "pcmk_a": ["pcmk-a1"],
        "site_a": ["san-a", "pcmk-a1"],
    },
    "stacks": {"pcmk-ubuntu": {"groups": ["san_a", "pcmk_a"]}},
}


def test_render_emits_targets_grains_in_first_appearance_order(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")  # pin expanduser for a deterministic priv
    out = render_roster(TOPO)
    assert out == (
        "# salt-ssh roster — generated from lab/topology.yaml. Do not edit by hand.\n"
        "san-a:\n"
        "  host: 10.50.0.5\n"
        "  user: vagrant\n"
        "  priv: /home/tester/.vagrant.d/insecure_private_key\n"
        "  sudo: true\n"
        "  minion_opts:\n"
        "    grains:\n"
        "      roster_groups:\n"
        "      - san_a\n"
        "      - site_a\n"
        "      roster_stacks:\n"
        "      - pcmk-ubuntu\n"
        "pcmk-a1:\n"
        "  host: 10.50.0.51\n"
        "  user: vagrant\n"
        "  priv: /home/tester/.vagrant.d/insecure_private_key\n"
        "  sudo: true\n"
        "  minion_opts:\n"
        "    grains:\n"
        "      roster_groups:\n"
        "      - pcmk_a\n"
        "      - site_a\n"
        "      roster_stacks:\n"
        "      - pcmk-ubuntu\n"
    )


def test_missing_mgmt_ip_raises():
    topo = {"nodes": {"san-a": {"nics": {}}}, "groups": {"san_a": ["san-a"]}, "stacks": {}}
    with pytest.raises(RosterError, match="no net-mgmt IP: san-a"):
        render_roster(topo)


def test_group_referencing_undefined_host_raises():
    topo = {"nodes": {}, "groups": {"san_a": ["san-a"]}, "stacks": {}}
    with pytest.raises(RosterError, match="undefined host: san-a"):
        render_roster(topo)


def test_stack_referencing_undefined_group_raises():
    topo = {
        "nodes": {"san-a": {"nics": {"net-mgmt": "10.50.0.5"}}},
        "groups": {"san_a": ["san-a"]},
        "stacks": {"bad": {"groups": ["nope"]}},
    }
    with pytest.raises(RosterError, match="undefined group: nope"):
        render_roster(topo)


def test_ungrouped_node_is_absent_and_uncovered_host_has_empty_stacks(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    topo = {
        "nodes": {
            "a": {"nics": {"net-mgmt": "10.50.0.1"}},
            "b": {"nics": {"net-mgmt": "10.50.0.2"}},
            "lonely": {"nics": {"net-mgmt": "10.50.0.9"}},  # in no group
        },
        "groups": {"ga": ["a"], "gb": ["b"]},
        "stacks": {"s1": {"groups": ["ga"]}},  # covers a, not b
    }
    out = render_roster(topo)
    assert "lonely:" not in out  # ungrouped node absent (parity with inventory)
    assert "a:\n" in out and "b:\n" in out
    # a is covered by s1; b is in a group no stack references -> empty list
    assert "      roster_stacks:\n      - s1\n" in out
    assert (
        "  minion_opts:\n    grains:\n      roster_groups:\n      - gb\n      roster_stacks: []\n"
        in out
    )
