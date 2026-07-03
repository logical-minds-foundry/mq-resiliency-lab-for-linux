from __future__ import annotations

import pytest

from mqlab.bind import _infra_by_org, lab_render, render
from mqlab.dns import DnsError

TOPO = {
    "nodes": {
        "infra-client": {
            "nics": {"net-mgmt": "10.50.0.8", "net-data-a": "10.10.1.8", "net-ext": "10.60.0.8"}
        },
        "infra-svc": {"org": "service", "nics": {"net-mgmt": "10.50.0.9", "net-ext": "10.60.0.9"}},
        "qm-a1": {"nics": {"net-mgmt": "10.50.0.11", "net-data-a": "10.10.1.11"}},
    },
    "groups": {"infra": ["infra-client", "infra-svc"]},
    "stacks": {},
}


def test_forward_zone_files_have_soa_ns_and_records():
    out = render(TOPO)
    client = out["client.com.zone"]
    # SOA + NS point at the client infra's primary A record (never the CNAME)
    assert "@ IN SOA infra-client-data-a.client.com. hostmaster.client.com." in client
    assert "@ IN NS infra-client-data-a.client.com." in client
    # an A record keeps its address (no trailing dot); the CNAME target gets one
    assert "qm-a1-data-a.client.com. IN A 10.10.1.11" in client
    assert "qm-a1.client.com. IN CNAME qm-a1-data-a.client.com." in client
    # the counterparty zone is served by the service infra (primary = ext)
    svc = out["service.com.zone"]
    assert "@ IN NS infra-svc-ext.service.com." in svc
    assert "infra-svc-ext.service.com. IN A 10.60.0.9" in svc


def test_reverse_zone_files_and_ptr_dotting():
    out = render(TOPO)
    rev = out["1.10.10.in-addr.arpa.zone"]
    assert "11.1.10.10.in-addr.arpa. IN PTR qm-a1-data-a.client.com." in rev


def test_authority_and_forwarding_split():
    out = render(TOPO)
    # client infra masters client.com AND every reverse zone
    local_c = out["named.conf.local.infra-client"]
    assert 'zone "client.com" { type master; file "/etc/bind/zones/client.com.zone"; };' in local_c
    assert 'zone "1.10.10.in-addr.arpa"' in local_c
    assert 'zone "0.60.10.in-addr.arpa"' in local_c
    # service infra masters only service.com
    local_s = out["named.conf.local.infra-svc"]
    assert 'zone "service.com" { type master;' in local_s
    assert "client.com" not in local_s
    # each forwards the rest to its peer's net-ext address (forward only)
    assert "forwarders { 10.60.0.9; };" in out["named.conf.options.infra-client"]
    assert "forwarders { 10.60.0.8; };" in out["named.conf.options.infra-svc"]
    assert "forward only;" in out["named.conf.options.infra-client"]


def test_infra_by_org_rejects_undefined_host():
    with pytest.raises(DnsError, match="infra group references undefined host: ghost"):
        _infra_by_org({"groups": {"infra": ["ghost"]}, "nodes": {}})


def test_infra_by_org_rejects_two_nodes_for_one_org():
    topo = {
        "groups": {"infra": ["a", "b"]},
        "nodes": {"a": {"nics": {}}, "b": {"nics": {}}},  # both default to client
    }
    with pytest.raises(DnsError, match="more than one infra node for org 'client'"):
        _infra_by_org(topo)


def test_infra_by_org_requires_a_node_per_org():
    topo = {"groups": {"infra": ["infra-client"]}, "nodes": {"infra-client": {"nics": {}}}}
    with pytest.raises(DnsError, match="no infra node for org 'service'"):
        _infra_by_org(topo)


def test_lab_render_real_topology():
    out = lab_render()
    assert "client.com.zone" in out
    assert "service.com.zone" in out
    # the real peer addresses (B1's .8/.9 on net-ext)
    assert "forwarders { 10.60.0.9; };" in out["named.conf.options.infra-client"]
    assert "forwarders { 10.60.0.8; };" in out["named.conf.options.infra-svc"]
    # a real VIP service record lands in the client zone
    assert "pcmk-vip.client.com. IN A 10.10.1.200" in out["client.com.zone"]
