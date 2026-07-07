"""Canonical mqweb REST endpoint derivation, both sites + DR invariant (epic #39)."""

import pytest

from mqlab import rest


def _topo():
    return {
        "stacks": {
            "pcmk-ubuntu": {
                "cluster_group": "pcmk_a",
                "groups": ["pcmk_a", "pcmk_b"],
                "qm": {"vip": "10.10.1.200", "vip_b": "10.10.2.200"},
            },
            "nativeha-ubuntu": {
                "cluster_group": "nha_ubuntu_a",
                "groups": ["nha_ubuntu_a", "nha_ubuntu_b"],
                "qm": {},
            },
        },
        "groups": {
            "pcmk_a": ["pcmk-a1"],
            "nha_ubuntu_a": ["nha-ubuntu-a1", "nha-ubuntu-a2"],
            "nha_ubuntu_b": ["nha-ubuntu-b1"],
        },
        "nodes": {
            "nha-ubuntu-a1": {"nics": {"net-data-a": "10.10.1.11"}},
            "nha-ubuntu-a2": {"nics": {"net-data-a": "10.10.1.12"}},
            "nha-ubuntu-b1": {"nics": {"net-data-b": "10.10.2.11"}},
            "svc-sim": {"nics": {"net-ext": "10.60.0.50"}},
        },
    }


def test_vip_stack_yields_both_site_endpoints():
    recs = {r["stack"]: r for r in rest.rest_endpoints(_topo())}
    assert recs["pcmk-ubuntu"]["kind"] == "vip"
    assert recs["pcmk-ubuntu"]["endpoints"] == {
        "site-a": ["https://10.10.1.200:9443"],
        "site-b": ["https://10.10.2.200:9443"],
    }


def test_native_ha_yields_per_node_endpoints_per_site():
    recs = {r["stack"]: r for r in rest.rest_endpoints(_topo())}
    assert recs["nativeha-ubuntu"]["kind"] == "active-instance"
    assert recs["nativeha-ubuntu"]["endpoints"] == {
        "site-a": ["https://10.10.1.11:9443", "https://10.10.1.12:9443"],
        "site-b": ["https://10.10.2.11:9443"],
    }


def test_svc_sim_is_counterparty_on_net_ext():
    recs = {r["stack"]: r for r in rest.rest_endpoints(_topo())}
    assert recs["svc-sim"]["kind"] == "counterparty"
    assert recs["svc-sim"]["endpoints"] == {"site-a": ["https://10.60.0.50:9443"]}


def test_vip_stack_without_vip_b_raises_dr_invariant():
    topo = {"stacks": {"rdqm-rhel": {"qm": {"vip": "10.10.1.100"}}}, "groups": {}, "nodes": {}}
    with pytest.raises(rest.RestError, match="must .*publish a VIP on both sites"):
        rest.rest_endpoints(topo)


def test_native_ha_site_a_only_when_no_peer_group():
    topo = {
        "stacks": {"nh": {"cluster_group": "g_a", "groups": ["g_a"], "qm": {}}},
        "groups": {"g_a": ["n1"]},
        "nodes": {"n1": {"nics": {"net-data-a": "10.10.1.11"}}},
    }
    recs = rest.rest_endpoints(topo)
    assert recs[0]["endpoints"] == {"site-a": ["https://10.10.1.11:9443"]}


def test_stack_without_vip_or_group_raises():
    topo = {"stacks": {"broken": {"qm": {}}}, "groups": {}, "nodes": {}}
    with pytest.raises(rest.RestError, match="no vip and no cluster_group"):
        rest.rest_endpoints(topo)


def test_node_missing_data_nic_raises():
    topo = {
        "stacks": {"nh": {"cluster_group": "g", "groups": ["g"], "qm": {}}},
        "groups": {"g": ["n1"]},
        "nodes": {"n1": {"nics": {}}},
    }
    with pytest.raises(rest.RestError, match="no net-data-a address"):
        rest.rest_endpoints(topo)


def test_absent_svc_sim_yields_no_counterparty_record():
    assert rest.rest_endpoints({"stacks": {}, "groups": {}, "nodes": {}}) == []


def test_lab_rest_endpoints_reads_real_topology():
    recs = {r["stack"]: r for r in rest.lab_rest_endpoints()}
    # Real topology (rdqm vip_b declared in #540): both VIP stacks have site-a+site-b.
    assert recs["pcmk-ubuntu"]["endpoints"]["site-b"] == ["https://10.10.2.200:9443"]
    assert recs["rdqm-rhel"]["endpoints"]["site-b"] == ["https://10.10.2.100:9443"]
    assert recs["svc-sim"]["kind"] == "counterparty"
