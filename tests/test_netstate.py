from __future__ import annotations

from mqlab.netstate import net_peers, render_net_state_prom


def test_emits_tri_state_per_named_network():
    # parsed `virsh net-list --all` states; net-data-a undefined (absent)
    states = {"net-hb-a": "active", "net-wan": "inactive"}
    out = render_net_state_prom(["net-hb-a", "net-wan", "net-data-a"], states)
    assert 'lab_network_state{network="net-hb-a"} 2' in out  # active
    assert 'lab_network_state{network="net-wan"} 1' in out  # inactive
    assert 'lab_network_state{network="net-data-a"} 0' in out  # absent (not in states)
    assert out.startswith("# HELP lab_network_state")


def test_net_peers_lists_same_network_peers_with_their_ips():
    topo = {
        "nodes": {
            "pcmk-a1": {"nics": {"net-hb-a": "172.16.1.51", "net-mgmt": "10.50.0.51"}},
            "pcmk-a2": {"nics": {"net-hb-a": "172.16.1.52", "net-mgmt": "10.50.0.52"}},
            "san-a": {"nics": {"net-san-a": "10.40.1.5"}},
        }
    }
    peers = net_peers(topo)
    assert peers["pcmk-a1"]["net-hb-a"] == [{"peer": "pcmk-a2", "ip": "172.16.1.52"}]
    # net-mgmt is excluded (the scrape plane, not a tested data path)
    assert "net-mgmt" not in peers["pcmk-a1"]
    # a node alone on a net has no peers entry for it
    assert peers.get("san-a", {}) == {}
