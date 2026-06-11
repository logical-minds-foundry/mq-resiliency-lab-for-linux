from __future__ import annotations

import pytest

from mqlab.dashboard import DASHBOARD_UID, DashboardError, render_dashboard

TOPO = {
    "groups": {
        "san_a": ["san-a"],
        "san_b": ["san-b"],
        "pcmk_a": ["pcmk-a1"],
        "pcmk_b": ["pcmk-b1"],
        "rdqm_a": ["rdqm-a1"],
        "rdqm_b": ["rdqm-b1"],
        "qm": ["qm-main"],
        "dtcc": ["dtcc-sim"],
        "client": ["app-client"],
        "obs_box": ["obs"],
        "probe": ["mon-probe"],
    }
}


def test_uid_is_preserved():
    assert render_dashboard(TOPO)["uid"] == "lab-fleet-node" == DASHBOARD_UID


def test_has_a_row_header_per_curated_row_in_order():
    panels = render_dashboard(TOPO)["panels"]
    row_titles = [p["title"] for p in panels if p["type"] == "row"]
    assert row_titles == [
        "MQ Service — reserved · Layer 2",
        "VMs · SAN",
        "VMs · PCMK · A",
        "VMs · PCMK · B",
        "VMs · RDQM · A",
        "VMs · RDQM · B",
        "VMs · Standalone",
        "VMs · Observability",
        "Networks",
    ]


def test_group_rows_filter_by_their_groups_selector():
    panels = render_dashboard(TOPO)["panels"]
    exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
    # SAN row rolls up both san groups; PCMK-A rolls up just pcmk_a
    assert any(e == 'up{job="node", groups=~"san_a|san_b"}' for e in exprs)
    assert any('groups=~"pcmk_a"' in e and e.startswith("100 - ") for e in exprs)


def test_unknown_curated_group_fails_loud():
    topo = {"groups": {"san_a": ["san-a"]}}  # ROWS references many groups not here
    with pytest.raises(DashboardError, match="unknown group in ROWS"):
        render_dashboard(topo)
