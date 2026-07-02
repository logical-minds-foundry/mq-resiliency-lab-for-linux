from __future__ import annotations

import pytest

from mqlab.dns import (
    DnsError,
    Record,
    _node_forward,
    _primary_plane,
    _rev,
    _suffix,
    _vips,
    _zone_of,
    forward_zones,
    lab_forward_zones,
    lab_reverse_zones,
    reverse_zones,
)

# A small synthetic topology exercising both orgs, a data-plane node (primary =
# data-a), a counterparty node with no data plane (primary = ext), and stacks with
# a VIP, a VIP + partner VIP, a Native-HA stack (no VIP), and a short-less block.
TOPO = {
    "nodes": {
        "qm-a1": {
            "nics": {"net-mgmt": "10.50.0.11", "net-data-a": "10.10.1.11", "net-ext": "10.60.0.11"}
        },
        "svc-sim": {"org": "service", "nics": {"net-mgmt": "10.50.0.50", "net-ext": "10.60.0.50"}},
    },
    "stacks": {
        "pcmk-ubuntu": {"short": "PCMK", "qm": {"vip": "10.10.1.200", "vip_ext": "10.60.0.10"}},
        "rdqm-rhel": {"short": "RDQM", "qm": {"vip": "10.10.1.100"}},
        "nativeha-ubuntu": {"short": "NHAU", "qm": {}},  # no VIP -> contributes nothing
        "weird": {"qm": {"vip": "10.10.1.99"}},  # no short -> skipped
    },
}


# --- helpers ---------------------------------------------------------------


def test_suffix_strips_net_prefix_and_passes_other_names_through():
    assert _suffix("net-data-a") == "data-a"
    assert _suffix("custom") == "custom"


def test_zone_of_defaults_to_client_and_maps_service():
    assert _zone_of({}) == "client.com"
    assert _zone_of({"org": "service"}) == "service.com"


def test_zone_of_rejects_unknown_org():
    with pytest.raises(DnsError, match="node org must be one of"):
        _zone_of({"org": "bogus"})


def test_primary_plane_prefers_data_then_ext_then_mgmt():
    assert _primary_plane("h", {"net-mgmt": "x", "net-data-a": "y"}) == "net-data-a"
    assert _primary_plane("h", {"net-mgmt": "x", "net-ext": "y"}) == "net-ext"
    assert _primary_plane("h", {"net-mgmt": "x"}) == "net-mgmt"


def test_primary_plane_raises_when_no_eligible_interface():
    with pytest.raises(DnsError, match="no primary-eligible interface"):
        _primary_plane("h", {"net-hb-a": "172.16.1.1"})


def test_rev_builds_zone_and_owner():
    assert _rev("10.10.1.11") == ("1.10.10.in-addr.arpa", "11.1.10.10.in-addr.arpa")


def test_rev_rejects_non_ipv4():
    with pytest.raises(DnsError, match="not an IPv4 address"):
        _rev("10.10.1")


def test_node_forward_raises_without_nics():
    with pytest.raises(DnsError, match="has no nics"):
        _node_forward("lonely", {"nics": {}}, "client.com")


def test_vips_skips_shortless_and_vipless_stacks():
    vips = _vips(TOPO)
    assert ("pcmk-vip.client.com", "10.10.1.200") in vips
    assert ("pcmk-vip-ext.client.com", "10.60.0.10") in vips
    assert ("rdqm-vip.client.com", "10.10.1.100") in vips
    # NHAU has no vip, "weird" has no short -> neither contributes
    assert not any(fqdn.startswith("nhau") for fqdn, _ in vips)
    assert "10.10.1.99" not in {ip for _, ip in vips}


# --- forward zones ---------------------------------------------------------


def test_forward_zones_a_records_cname_and_vips():
    fwd = forward_zones(TOPO)
    client = fwd["client.com"]
    # one A per interface
    assert Record("qm-a1-data-a.client.com", "A", "10.10.1.11") in client
    assert Record("qm-a1-mgmt.client.com", "A", "10.50.0.11") in client
    # base name CNAMEs to the data-plane (primary) interface, not mgmt/ext
    assert Record("qm-a1.client.com", "CNAME", "qm-a1-data-a.client.com") in client
    # VIP service names land in the client zone
    assert Record("pcmk-vip.client.com", "A", "10.10.1.200") in client
    # counterparty records land in service.com; its primary is net-ext (no data)
    svc = fwd["service.com"]
    assert Record("svc-sim-ext.service.com", "A", "10.60.0.50") in svc
    assert Record("svc-sim.service.com", "CNAME", "svc-sim-ext.service.com") in svc
    # zones are sorted and de-duplicated
    assert client == sorted(set(client))


# --- reverse zones ---------------------------------------------------------


def test_reverse_zones_group_by_slash24_and_span_orgs_on_shared_planes():
    rev = reverse_zones(TOPO)
    # site-A data /24
    assert (
        Record("11.1.10.10.in-addr.arpa", "PTR", "qm-a1-data-a.client.com")
        in rev["1.10.10.in-addr.arpa"]
    )
    # the net-ext /24 carries PTRs for BOTH orgs (the boundary reverse zone)
    ext = rev["0.60.10.in-addr.arpa"]
    assert Record("11.0.60.10.in-addr.arpa", "PTR", "qm-a1-ext.client.com") in ext
    assert Record("50.0.60.10.in-addr.arpa", "PTR", "svc-sim-ext.service.com") in ext
    # a VIP gets a PTR too
    assert (
        Record("200.1.10.10.in-addr.arpa", "PTR", "pcmk-vip.client.com")
        in rev["1.10.10.in-addr.arpa"]
    )


# --- real topology smoke ---------------------------------------------------


def test_real_topology_forward_zones():
    fwd = lab_forward_zones()
    assert set(fwd) == {"client.com", "service.com"}
    client = fwd["client.com"]
    # the B1 infra node, resolving to its data-plane identity
    assert Record("infra-client-data-a.client.com", "A", "10.10.1.8") in client
    assert Record("infra-client.client.com", "CNAME", "infra-client-data-a.client.com") in client
    # the three Native-HA instance names clients dial (fall out of the per-NIC records)
    for i, host in enumerate(("nha-ubuntu-a1", "nha-ubuntu-a2", "nha-ubuntu-a3"), start=1):
        assert Record(f"{host}-data-a.client.com", "A", f"10.10.1.1{i}") in client
    # a VIP service name
    assert Record("pcmk-vip.client.com", "A", "10.10.1.200") in client
    # the counterparty + its mock nameserver are in service.com (primary = ext)
    svc = fwd["service.com"]
    assert Record("svc-sim-ext.service.com", "A", "10.60.0.50") in svc
    assert Record("infra-svc.service.com", "CNAME", "infra-svc-ext.service.com") in svc


def test_real_topology_reverse_zones_include_boundary_zone():
    rev = lab_reverse_zones()
    assert "1.10.10.in-addr.arpa" in rev  # site-A data
    ext = rev["0.60.10.in-addr.arpa"]  # the inter-business boundary /24
    zones_seen = {r.value.split(".", 1)[1] for r in ext}  # "client.com" / "service.com"
    assert {"client.com", "service.com"} <= zones_seen
