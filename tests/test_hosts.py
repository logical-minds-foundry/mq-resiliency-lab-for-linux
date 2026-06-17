from __future__ import annotations

from mqlab.hosts import render_hosts

_TOPO = {
    "nodes": {
        "rdqm-a1": {
            "nics": {
                "net-mgmt": "10.50.0.31",
                "net-data-a": "10.10.1.31",
                "net-hb-a": "172.16.1.31",
                "net-wan": "10.99.0.31",
            }
        },
        "rdqm-b1": {
            "nics": {"net-mgmt": "10.50.0.41", "net-data-b": "10.10.2.41", "net-wan": "10.99.0.41"}
        },
        "no-mgmt": {"nics": {"net-wan": "10.99.0.99"}},
        "bare": {},
    }
}


def test_bare_name_maps_to_mgmt_plus_mgmt_alias() -> None:
    assert "10.50.0.31 rdqm-a1 rdqm-a1-mgmt" in render_hosts(_TOPO)


def test_per_plane_aliases_for_each_declared_nic() -> None:
    out = render_hosts(_TOPO)
    assert "10.10.1.31 rdqm-a1-data-a" in out
    assert "172.16.1.31 rdqm-a1-hb-a" in out
    assert "10.99.0.31 rdqm-a1-wan" in out
    assert "10.99.0.41 rdqm-b1-wan" in out


def test_node_without_mgmt_emits_only_plane_aliases() -> None:
    out = render_hosts(_TOPO)
    assert "10.99.0.99 no-mgmt-wan" in out  # plane alias present
    assert "\n10.99.0.99 no-mgmt\n" not in out  # no bare-name line without mgmt


def test_node_without_nics_emits_nothing_for_it() -> None:
    assert "bare" not in render_hosts(_TOPO)
