from __future__ import annotations

from mqlab.netstate import render_net_state_prom


def test_emits_tri_state_per_named_network():
    # parsed `virsh net-list --all` states; net-data-a undefined (absent)
    states = {"net-hb-a": "active", "net-wan": "inactive"}
    out = render_net_state_prom(["net-hb-a", "net-wan", "net-data-a"], states)
    assert 'lab_network_state{network="net-hb-a"} 2' in out  # active
    assert 'lab_network_state{network="net-wan"} 1' in out  # inactive
    assert 'lab_network_state{network="net-data-a"} 0' in out  # absent (not in states)
    assert out.startswith("# HELP lab_network_state")
