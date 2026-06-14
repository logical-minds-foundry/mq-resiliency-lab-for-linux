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
        "MQ Service · QMPCMK · service · Ubuntu HA/DR",
        "MQ Service · QMDTCC · counterparty · DTCC service",
        "VMs · PCMK · A",
        "VMs · PCMK · B",
        "VMs · RDQM · A",
        "VMs · RDQM · B",
        "VMs · Standalone",
        "VMs · Observability",
        "Networks · Message path",
        "Networks · Cluster + storage",
        "Networks · Cross-site + mgmt",
    ]


def test_group_rows_filter_by_their_groups_selector():
    panels = render_dashboard(TOPO)["panels"]
    exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
    # SAN folds into its PCMK site row: PCMK-A rolls up pcmk_a + san_a, PCMK-B pcmk_b + san_b
    assert any(e == 'up{job="node", groups=~"pcmk_a|san_a"}' for e in exprs)
    assert any(e == 'up{job="node", groups=~"pcmk_b|san_b"}' for e in exprs)
    assert any('groups=~"pcmk_a|san_a"' in e and e.startswith("100 - ") for e in exprs)
    # no standalone SAN row anymore
    assert not any("san_a|san_b" in e for e in exprs)


def test_mq_service_rows_use_confirmed_ibmmq_metrics():
    by_title = {p.get("title"): p for p in render_dashboard(TOPO)["panels"]}
    # QM tiles — status, the "it's moving" rate, connections (per #141 spike)
    assert by_title["QMPCMK — status"]["targets"][0]["expr"] == 'ibmmq_qmgr_status{qmgr="QMPCMK"}'
    assert "ibmmq_qmgr_connection_count" in by_title["QMPCMK — connections"]["targets"][0]["expr"]
    rate = by_title["QMPCMK — msg rate"]["targets"][0]["expr"]
    assert "ibmmq_qmgr_interval_mqput_mqput1_total_count" in rate
    assert "ibmmq_qmgr_interval_destructive_get_total_count" in rate
    # channels + queues tables, keyed by the squash/depth metrics, formatted as tables
    ch = by_title["QMPCMK — channels"]["targets"][0]
    assert ch["expr"] == 'ibmmq_channel_status_squash{qmgr="QMPCMK"}' and ch["format"] == "table"
    qd = by_title["QMDTCC — queues"]["targets"][0]
    assert qd["expr"] == 'ibmmq_queue_depth{qmgr="QMDTCC"}' and qd["instant"] is True


def test_unknown_curated_group_fails_loud():
    topo = {"groups": {"san_a": ["san-a"]}}  # ROWS references many groups not here
    with pytest.raises(DashboardError, match="unknown group in ROWS"):
        render_dashboard(topo)


def test_network_sections_are_curated_collapsible_rows():
    titles = [p["title"] for p in render_dashboard(TOPO)["panels"] if p["type"] == "row"]
    # three curated network section rows, in order, after the VM rows
    assert titles[-3:] == [
        "Networks · Message path",
        "Networks · Cluster + storage",
        "Networks · Cross-site + mgmt",
    ]


def test_each_net_has_a_health_tile_and_rx_tx_graphs_with_shorthand():
    panels = render_dashboard(TOPO)["panels"]
    by_title = {p.get("title"): p for p in panels}
    # shorthand titles (no net- prefix)
    health = by_title["hb-a — health"]
    assert health["targets"][0]["expr"] == 'lab_network_health{network="net-hb-a"}'
    texts = {
        m["options"][k]["text"]
        for m in health["fieldConfig"]["defaults"]["mappings"]
        for k in m["options"]
    }
    assert {"ABSENT", "DOWN", "DEGRADED", "UP"} == texts
    # own-scale rx + tx graphs off the host bridge interface
    assert by_title["hb-a — rx"]["targets"][0]["expr"] == (
        'rate(node_network_receive_bytes_total{device="virbr-hb-a"}[1m])'
    )
    assert by_title["hb-a — tx"]["targets"][0]["expr"] == (
        'rate(node_network_transmit_bytes_total{device="virbr-hb-a"}[1m])'
    )
