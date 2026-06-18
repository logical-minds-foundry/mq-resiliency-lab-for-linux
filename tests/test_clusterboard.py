from __future__ import annotations

from mqlab.clusterboard import (
    active_side,
    fold_side,
    hero_tiles,
    integrity_panel,
    log_row,
    matrix,
    net_section,
    perf_section,
    render_cluster_dashboard,
    timeline_band,
)

DS = "promtest"


def test_matrix_is_a_joined_colourised_table():
    cols = [
        ("corosync", "max by (n)(label_replace(cluster_daemon_up...))", "up"),
        ("fence", "max by (n)(label_replace(cluster_fence_count...))", "clean0"),
    ]
    p = matrix("② Compute", cols, DS, y=0)
    assert p["type"] == "table"
    assert p["title"] == "② Compute"
    # one instant table-format target per column, all on the pinned datasource
    assert [t["refId"] for t in p["targets"]] == ["A", "B"]
    assert all(t["format"] == "table" and t["instant"] for t in p["targets"])
    assert all(t["datasource"] == {"type": "prometheus", "uid": DS} for t in p["targets"])
    assert p["targets"][0]["expr"].startswith("max by (n)(label_replace(cluster_daemon_up")
    # join on n, then rename Value #<ref> -> column title, n -> node, drop Time,
    # then sort rows by node so site A groups before site B (no interleaving)
    tids = [t["id"] for t in p["transformations"]]
    assert tids == ["joinByField", "organize", "sortBy"]
    assert p["transformations"][2]["options"]["sort"] == [{"field": "node"}]
    org = p["transformations"][1]["options"]
    assert org["renameByName"] == {"Value #A": "corosync", "Value #B": "fence", "n": "node"}
    assert org["excludeByName"] == {"Time": True}
    # one colour-background override per column, with the right mapping family
    ov = {o["matcher"]["options"]: o for o in p["fieldConfig"]["overrides"]}
    assert set(ov) == {"corosync", "fence"}
    cell = next(pr for pr in ov["corosync"]["properties"] if pr["id"] == "custom.cellOptions")
    assert cell["value"] == {"type": "color-background", "mode": "basic"}


def test_fold_side_precedence():
    assert fold_side(["green", "green"]) == "green"
    assert fold_side(["green", "amber"]) == "amber"
    assert fold_side(["amber", "red"]) == "red"
    assert fold_side(["red", "STALE"]) == "STALE"  # STALE outranks red
    assert fold_side([]) == "STALE"  # no cells = blind = STALE


def test_active_side_states():
    assert active_side(["A"]) == "A"
    assert active_side(["B"]) == "B"
    assert active_side([]) == "none"  # nobody owns it mid-transition
    assert active_side(["A", "B"]) == "split"  # dual owner = hazard


def test_hero_tiles_are_stat_panels_across_the_top():
    tiles = hero_tiles(DS, y=0)
    assert [t["type"] for t in tiles] == ["stat"] * 4
    titles = [t["title"] for t in tiles]
    assert titles == ["Cluster health", "Nodes online", "Active QM owner", "Replication backlog"]
    # laid out across one row (x = 0, 6, 12, 18), all at y=0
    assert [t["gridPos"]["x"] for t in tiles] == [0, 6, 12, 18]
    assert all(t["gridPos"]["y"] == 0 for t in tiles)
    # replication tile reports bytes (honest under TCG — no seconds)
    repl = tiles[3]
    assert repl["targets"][0]["expr"] == "max(cluster_drbd_out_of_sync_bytes)"
    assert repl["fieldConfig"]["defaults"]["unit"] == "bytes"


def test_integrity_light_is_loud_on_hazard_and_stale_on_no_data():
    p = integrity_panel(DS, y=4)
    assert p["type"] == "stat"
    assert p["title"] == "Integrity"
    # hazard count: split-brain (StandAlone) / dual-primary / Diskless
    expr = p["targets"][0]["expr"]
    assert "StandAlone" in expr and "Diskless" in expr
    maps = p["fieldConfig"]["defaults"]["mappings"]
    # 0 hazards -> green OK; >=1 -> red loud; and NO-DATA must read STALE, never green
    special = [m for m in maps if m["type"] == "special"]
    assert special and special[0]["options"]["match"] == "null"
    assert special[0]["options"]["result"]["text"] == "STALE"


def test_timeline_band_is_a_state_timeline_with_range_queries():
    p = timeline_band(DS, y=0)
    assert p["type"] == "state-timeline"
    assert p["targets"]  # has series
    assert all(t.get("range") for t in p["targets"])  # range over time, not instant
    legends = [t["legendFormat"] for t in p["targets"]]
    assert "nodes online" in legends and "DRBD primary" in legends


def test_perf_section_timeseries_from_existing_metrics():
    ps = perf_section(DS, y=0)
    assert ps and all(p["type"] == "timeseries" for p in ps)
    titles = " ".join(p["title"] for p in ps)
    assert "CPU" in titles and "DRBD" in titles
    cpu = next(p for p in ps if "CPU" in p["title"])
    assert "node_cpu_seconds_total" in cpu["targets"][0]["expr"]  # existing metric


def test_net_section_per_plane_state_includes_the_wan_replication_plane():
    ns = net_section(DS, y=0)
    assert ns and all(p["type"] == "timeseries" for p in ns)
    state = next(p for p in ns if "state" in p["title"].lower())
    expr = state["targets"][0]["expr"]
    assert "lab_network_state" in expr
    assert "net-wan" in expr  # the DRBD replication plane is in scope


def test_log_row_is_a_loki_logs_panel_scoped_to_cluster_nodes():
    p = log_row("loki", y=0)
    assert p["type"] == "logs"
    assert p["datasource"] == {"type": "loki", "uid": "loki"}
    expr = p["targets"][0]["expr"]
    assert 'host=~"pcmk-' in expr  # scoped to cluster nodes
    assert "${level}" in expr  # severity is a dashboard toggle (#219)


def test_log_severity_toggle_defaults_to_warn():
    d = render_cluster_dashboard({}, arm="pcmk")
    level = next(v for v in d["templating"]["list"] if v["name"] == "level")
    assert level["current"]["text"] == "WARN+"  # defaults to WARN+
    opts = {o["text"] for o in level["options"]}
    assert opts == {"WARN+", "All (incl. info)"}  # toggle to All for info


def test_board_annotations_are_holder_agnostic():
    d = render_cluster_dashboard({}, arm="pcmk")
    anns = d["annotations"]["list"]
    owner = next(a for a in anns if "owner" in a["name"].lower())
    # holder-agnostic: counts owners per resource, never the holder-labelled series
    assert "count by (resource)(cluster_resource_owner)" in owner["expr"]
    assert "holder" not in owner["expr"]


def test_board_has_uid_hero_integrity_and_the_two_matrices():
    d = render_cluster_dashboard({}, arm="pcmk")
    assert d["uid"] == "lab-pcmk-cluster"
    by_title = {p["title"]: p for p in d["panels"]}
    # hero band + integrity + the two matrices all present
    assert "Cluster health" in by_title and "Integrity" in by_title
    compute = by_title["② Compute — node × component"]
    storage = by_title["③ Storage — DRBD / SAN"]
    cols = compute["transformations"][1]["options"]["renameByName"]
    assert {"Value #A", "Value #D"} <= set(cols)  # corosync .. fence present
    # top-to-bottom: banner → ① row → hero → integrity → compute → storage (no overlap)
    assert by_title["Cluster health"]["gridPos"]["y"] > 0  # below the banner + ① row
    assert by_title["Integrity"]["gridPos"]["y"] < compute["gridPos"]["y"] < storage["gridPos"]["y"]
    # a spelled-out title banner + a numbered ① section header at the very top (#219 feedback)
    banner = next(p for p in d["panels"] if p["type"] == "text")
    assert banner["gridPos"]["y"] == 0 and "Ubuntu" in banner["options"]["content"]
    assert any(p["type"] == "row" and p["title"].startswith("①") for p in d["panels"])
