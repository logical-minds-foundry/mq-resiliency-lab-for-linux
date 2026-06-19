from __future__ import annotations

import json
import re

from mqlab.clusterboard import (
    active_side,
    fold_side,
    hero_tiles,
    integrity_panel,
    log_row,
    matrix,
    nativeha_hero_tiles,
    nativeha_integrity_panel,
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
    # always-on with no top-bar toggle (#219 feedback)
    assert owner["enable"] is True and owner["hide"] is True


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


# ── Native HA arm (#279) ──────────────────────────────────────────────────────


def test_nativeha_integrity_is_quorum_active_insync_gated_on_data():
    p = nativeha_integrity_panel("promtest", y=7)
    assert p["type"] == "stat"
    assert p["gridPos"]["w"] == 24
    expr = p["targets"][0]["expr"]
    assert "cluster_quorate" in expr
    assert 'cluster_resource_owner{resource="QMNATIVE"}' in expr
    assert "cluster_nha_insync == 0" in expr
    assert "count(cluster_nha_role) > 0" in expr  # gated -> no-data reads STALE
    kinds = [m["type"] for m in p["fieldConfig"]["defaults"]["mappings"]]
    assert "special" in kinds  # the STALE special-mapping (never a false green)


def test_pcmk_integrity_panel_unchanged():
    # regression: the PCMK integrity panel still carries the DRBD hazard expr, full-width
    p = integrity_panel("promtest", y=7)
    assert 'cluster_drbd_conn{conn="StandAlone"}' in p["targets"][0]["expr"]
    assert p["gridPos"]["w"] == 24


def test_nativeha_hero_tiles_band():
    tiles = nativeha_hero_tiles("promtest", y=3)
    assert [t["title"] for t in tiles] == [
        "Active instance",
        "Quorum",
        "Instances in-sync",
        "HA status",
    ]
    active = tiles[0]
    assert active["options"]["textMode"] == "name"
    assert 'cluster_resource_owner{resource="QMNATIVE"}' in active["targets"][0]["expr"]
    assert "cluster_nha_quorum" in tiles[1]["targets"][0]["expr"]
    assert "cluster_nha_insync" in tiles[2]["targets"][0]["expr"]
    assert "cluster_nha_hastatus" in tiles[3]["targets"][0]["expr"]
    assert [t["gridPos"]["x"] for t in tiles] == [0, 6, 12, 18]  # tile left-to-right


def test_role_mapping_codes_active_replica_unknown():
    from mqlab.clusterboard import _MAPPINGS

    opts = _MAPPINGS["role"][0]["options"]
    assert opts["2"]["text"] == "Active"
    assert opts["1"]["text"] == "Replica"
    assert opts["0"]["text"] == "Unknown"
    # the Recovery group's leader (ROLE Leader) is a healthy state, coloured green not red
    assert opts["3"]["text"] == "Leader"
    assert opts["3"]["color"] == "green"
    assert opts["0"]["color"] == "red"  # only genuinely-unknown is alarming


def test_nativeha_instance_cols_for_a_site():
    from mqlab.clusterboard import _nativeha_instance_cols

    cols = _nativeha_instance_cols("nha-rhel-a.*")
    assert [c[0] for c in cols] == ["online", "role", "in-sync", "HA Normal"]
    assert cols[1][2] == "role"  # the role column uses the role mapping
    assert "cluster_nha_role_code" in cols[1][1]
    # every column is scoped to the site's members
    assert all('member=~"nha-rhel-a.*"' in expr for _, expr, _ in cols)


def test_nativeha_board_uid_sections_and_tags():
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    assert d["uid"] == "lab-nativeha-cluster"
    assert "nativeha-rhel" in d["tags"]
    by_title = {p.get("title", ""): p for p in d["panels"]}
    # matrices are labelled by FIXED site (A/B), never by the dynamic live/recovery role (#279)
    assert "Site A" in by_title
    assert "Site B" in by_title
    assert "Active instance" in by_title and "Integrity" in by_title
    # the two matrices stack without overlap, below the hero/integrity band
    site_a = by_title["Site A"]
    site_b = by_title["Site B"]
    assert site_a["gridPos"]["y"] < site_b["gridPos"]["y"]
    assert by_title["Integrity"]["gridPos"]["y"] < site_a["gridPos"]["y"]
    banner = next(p for p in d["panels"] if p["type"] == "text")
    assert "Native HA" in banner["options"]["content"]
    # the instance matrices must NOT label a site with a role word — live/recovery swaps (#279)
    for p in d["panels"]:
        if p["type"] == "table":
            assert "Live" not in p["title"] and "Recovery" not in p["title"]
    # Site A scopes to a-nodes, Site B to b-nodes
    assert any('member=~"nha-rhel-a.*"' in t["expr"] for t in site_a["targets"])
    assert any('member=~"nha-rhel-b.*"' in t["expr"] for t in site_b["targets"])
    # no PCMK-only plumbing leaks into the nativeha board
    blob = json.dumps(d)
    assert "corosync" not in blob and "cluster_drbd" not in blob and "iSCSI" not in blob


def test_pcmk_board_still_renders_unchanged():
    d = render_cluster_dashboard({}, arm="pcmk")
    assert d["uid"] == "lab-pcmk-cluster"
    titles = [p.get("title", "") for p in d["panels"]]
    assert "② Compute — node × component" in titles
    assert "③ Storage — DRBD / SAN" in titles


def test_pcmk_board_scopes_shared_metrics_to_pcmk_groups():
    # cluster_node_online / cluster_quorate are emitted by every arm — on the PCMK board every
    # use must carry the pcmk group scope, else nha nodes leak in (#279).
    blob = json.dumps(render_cluster_dashboard({}, arm="pcmk"))
    for m in re.finditer(r"cluster_(?:node_online|quorate)(\{[^}]*\})?", blob):
        sel = m.group(1) or ""
        assert "pcmk_a|pcmk_b" in sel, f"unscoped shared metric on PCMK board: {m.group(0)}"


def test_nativeha_board_scopes_shared_metrics_to_nha_groups():
    # the nha board's shared cluster_quorate (integrity) is scoped to nha groups; its
    # cluster_node_online lives only in the instance matrices, scoped by member regex.
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    blob = json.dumps(d)
    for m in re.finditer(r"cluster_quorate(\{[^}]*\})?", blob):
        assert "nha_rhel_a|nha_rhel_b" in (m.group(1) or ""), "unscoped quorate on nha board"
    for m in re.finditer(r"cluster_node_online(\{[^}]*\})?", blob):
        assert "nha-rhel-" in (m.group(1) or ""), "unscoped node_online on nha board"
    assert "pcmk_a|pcmk_b" not in blob  # no PCMK scope leaks onto the nha board


def test_nativeha_board_has_full_section_parity_minus_storage():
    # the nha board carries everything after ② that PCMK has — CRR card, timeline, logs,
    # perf, network — but NO storage (Native HA has no DRBD/SAN tier).
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    titles = [p.get("title", "") for p in d["panels"]]
    types = {p["type"] for p in d["panels"]}
    assert any("Cross-region (CRR)" in t for t in titles)  # ③ CRR row header
    assert any(t == "CRR connected" for t in titles)  # CRR card tile
    assert "state-timeline" in types  # failover + CRR timeline
    assert "logs" in types  # native HA log row
    assert any("CPU busy" in t for t in titles)  # perf
    assert any("CRR replication (net-wan)" in t for t in titles)  # perf net-wan
    assert any("network planes" in t.lower() for t in titles)  # net section
    assert "③ Storage — DRBD / SAN" not in titles  # no storage section
    # the log severity toggle is wired (templating var present)
    assert d["templating"]["list"][0]["name"] == "level"
    # each named section is its own peer-level collapsible row (#279 feedback): one row per
    # section, so collapse behaves consistently top-to-bottom (not one giant ① section).
    row_titles = [p["title"] for p in d["panels"] if p["type"] == "row"]
    assert row_titles[0].startswith("①")
    assert any(t.startswith("②") for t in row_titles)
    assert any(t.startswith("③") for t in row_titles)
    assert len(row_titles) >= 6  # ① ② ③ + timeline + logs + perf + net


def test_nativeha_log_panel_notes_amqerr_is_file_based():
    # the logs panel carries a description so an empty panel doesn't read as broken (#279):
    # MQ's AMQERR error log is file-based, not journald.
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    logs = next(p for p in d["panels"] if p["type"] == "logs")
    assert "AMQERR" in logs["description"]
    assert 'host=~"nha-rhel-.*"' in logs["targets"][0]["expr"]


def test_nativeha_perf_uses_nha_groups_and_no_san_disk():
    from mqlab.clusterboard import nativeha_perf_section

    blob = json.dumps(nativeha_perf_section("promtest", y=0))
    assert "nha_rhel_a|nha_rhel_b" in blob  # CPU scoped to nha nodes
    assert "node_disk" not in blob  # no SAN disk I/O panel
    assert "virbr-hb" in blob and "virbr-wan" in blob  # raft + CRR throughput


def test_nativeha_crr_card_reports_group_roles_and_backlog():
    from mqlab.clusterboard import nativeha_crr_card

    tiles = nativeha_crr_card("promtest", y=0)
    titles = [t["title"] for t in tiles]
    assert titles == ["Live group role", "Recovery group role", "CRR connected", "CRR backlog"]
    assert 'cluster_nha_group_role{group="Live"}' in tiles[0]["targets"][0]["expr"]
    assert tiles[0]["targets"][0]["legendFormat"] == "{{role}}"  # shows the role text
    assert "cluster_nha_group_backlog" in tiles[3]["targets"][0]["expr"]
