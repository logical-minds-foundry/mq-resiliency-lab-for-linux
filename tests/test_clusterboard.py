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
    nativeha_status_band,
    net_section,
    perf_section,
    rdqm_dr_card,
    rdqm_status_band,
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


def test_cluster_log_panels_exclude_the_event_stream():
    """The wildcard log selectors (.*mq.* / mq-.*) must not sweep in unit=mq-events (#517) —
    events are a separate stream and belong on the events panel, not the log panels."""
    from mqlab.clusterboard import _nativeha_log_row, _rdqm_log_row

    for panel in (log_row("loki", y=0), _nativeha_log_row("loki", y=0), _rdqm_log_row("loki", y=0)):
        expr = panel["targets"][0]["expr"]
        assert 'unit!="mq-events"' in expr, expr


def test_log_severity_toggle_defaults_to_warn():
    d = render_cluster_dashboard({}, arm="pcmk-ubuntu")
    level = next(v for v in d["templating"]["list"] if v["name"] == "level")
    assert level["current"]["text"] == "WARN+"  # defaults to WARN+
    opts = {o["text"] for o in level["options"]}
    assert opts == {"WARN+", "All (incl. info)"}  # toggle to All for info


def test_board_annotations_are_holder_agnostic():
    d = render_cluster_dashboard({}, arm="pcmk-ubuntu")
    anns = d["annotations"]["list"]
    owner = next(a for a in anns if "owner" in a["name"].lower())
    # holder-agnostic: counts owners per resource, never the holder-labelled series
    assert "count by (resource)(cluster_resource_owner)" in owner["expr"]
    assert "holder" not in owner["expr"]
    # always-on with no top-bar toggle (#219 feedback)
    assert owner["enable"] is True and owner["hide"] is True


def test_board_has_uid_hero_integrity_and_the_two_matrices():
    d = render_cluster_dashboard({}, arm="pcmk-ubuntu")
    assert d["uid"] == "lab-pcmk-ubuntu-cluster"
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


def test_pcmk_integrity_panel_unchanged():
    # regression: the PCMK integrity panel still carries the DRBD hazard expr, full-width
    p = integrity_panel("promtest", y=7)
    assert 'cluster_drbd_conn{conn="StandAlone"}' in p["targets"][0]["expr"]
    assert p["gridPos"]["w"] == 24


def test_nativeha_status_band_is_one_compact_full_width_row():
    # ① is a single row of five equal compact tiles; integrity is a tile, not a banner (#279)
    tiles = nativeha_status_band("promtest", y=3)
    assert [t["title"] for t in tiles] == [
        "Active instance",
        "Quorum",
        "Instances in-sync",
        "HA status",
        "Integrity",
    ]
    # all on one row (same y), widths fill the 24-col grid, compact value font, short height
    assert all(t["gridPos"]["y"] == 3 for t in tiles)
    assert sum(t["gridPos"]["w"] for t in tiles) == 24
    assert all(t["gridPos"]["h"] == 3 for t in tiles)
    assert all(t["options"]["text"]["valueSize"] == 22 for t in tiles)
    # the integrity tile still carries the gated hazard expr (no false green on no-data)
    integ = tiles[4]["targets"][0]["expr"]
    assert "cluster_nha_insync == 0" in integ and "count(cluster_nha_role) > 0" in integ
    assert 'cluster_resource_owner{resource="NHARAPP"}' in integ
    assert 'cluster_resource_owner{resource="NHARAPP"}' in tiles[0]["targets"][0]["expr"]


def test_role_mapping_codes_active_replica_unknown():
    from mqlab.clusterboard import _MAPPINGS

    opts = _MAPPINGS["role"][0]["options"]
    assert opts["2"]["text"] == "Active"
    assert opts["1"]["text"] == "Replica"
    assert opts["0"]["text"] == "Unknown"
    # the Recovery group's leader (ROLE Leader) is the healthy standby — blue (the "alternate
    # green", same family as Replica), not green (green is the live Active), not red (a genuinely
    # down instance), and not yellow (yellow is a warning, which a normal standby is not) (#399)
    assert opts["3"]["text"] == "Leader"
    assert opts["3"]["color"] == "blue"
    assert opts["1"]["color"] == "blue"  # Replica — healthy follower, same standby family
    assert opts["2"]["color"] == "green"  # Active (live) is green
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
    assert d["uid"] == "lab-nativeha-rhel-cluster"
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
    d = render_cluster_dashboard({}, arm="pcmk-ubuntu")
    assert d["uid"] == "lab-pcmk-ubuntu-cluster"
    titles = [p.get("title", "") for p in d["panels"]]
    assert "② Compute — node × component" in titles
    assert "③ Storage — DRBD / SAN" in titles


def test_pcmk_board_scopes_shared_metrics_to_pcmk_groups():
    # cluster_node_online / cluster_quorate are emitted by every arm — on the PCMK board every
    # use must carry the pcmk group scope, else nha nodes leak in (#279).
    blob = json.dumps(render_cluster_dashboard({}, arm="pcmk-ubuntu"))
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
    assert any("Cross-region replication (CRR)" in t for t in titles)  # ③ CRR row header
    assert any(t == "CRR connected" for t in titles)  # CRR card tile
    assert any(t in ("Site A", "Site B") for t in titles)  # site role badges + matrices
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


def test_nativeha_log_panel_includes_ibm_mq_diagnostic_stream():
    # the logs panel carries a description so an empty panel doesn't read as broken (#279),
    # and since #282/#822 it targets the ibm-mq diagnostic (AMQERR JSON) stream.
    d = render_cluster_dashboard({}, arm="nativeha-rhel")
    logs = next(p for p in d["panels"] if p["type"] == "logs")
    assert "AMQERR" in logs["description"]
    assert "ibm-mq" in logs["targets"][0]["expr"]
    assert 'host=~"nha-rhel-.*"' in logs["targets"][0]["expr"]


def test_nativeha_perf_uses_nha_groups_and_no_san_disk():
    from mqlab.clusterboard import nativeha_perf_section

    blob = json.dumps(nativeha_perf_section("promtest", y=0))
    assert "nha_rhel_a|nha_rhel_b" in blob  # CPU scoped to nha nodes
    assert "node_disk" not in blob  # no SAN disk I/O panel
    assert "virbr-hb" in blob and "virbr-wan" in blob  # raft + CRR throughput


def test_nativeha_ubuntu_board_is_the_rhel_board_reparameterized():
    # The Ubuntu arm (#417) reuses the SAME assembly as RHEL; only the QM (NHAUAPP),
    # the ansible groups (nha_ubuntu_*), the host/instance prefix (nha-ubuntu), and the
    # board uid differ — every section the RHEL board has must be present, retargeted.
    d = render_cluster_dashboard({}, arm="nativeha-ubuntu")
    blob = json.dumps(d)
    assert d["uid"] == "lab-nativeha-ubuntu-cluster"
    assert "nativeha-ubuntu" in d["tags"]
    # QM + group + prefix are all the Ubuntu arm's, and the RHEL arm's never leak in.
    # (substring checks — json.dumps escapes the quotes in the PromQL label selectors)
    assert "NHAUAPP" in blob
    assert "NHARAPP" not in blob
    assert "nha_ubuntu_a|nha_ubuntu_b" in blob
    assert "nha_rhel_a|nha_rhel_b" not in blob
    assert "nha-ubuntu-" in blob
    assert "nha-rhel-" not in blob
    # quorate stays scoped to THIS arm's groups (no cross-arm metric leak)
    for m in re.finditer(r"cluster_quorate(\{[^}]*\})?", blob):
        assert "nha_ubuntu_a|nha_ubuntu_b" in (m.group(1) or ""), "unscoped quorate on ubuntu board"
    # full section parity (minus storage), same as RHEL
    titles = [p.get("title", "") for p in d["panels"]]
    assert any("Cross-region replication (CRR)" in t for t in titles)
    assert any(t in ("Site A", "Site B") for t in titles)
    assert "③ Storage — DRBD / SAN" not in titles
    # the logs panel names the Ubuntu QM and targets the ibm-mq diagnostic stream (#822)
    logs = next(p for p in d["panels"] if p["type"] == "logs")
    assert "NHAUAPP" in logs["description"]
    assert "ibm-mq" in logs["targets"][0]["expr"]


def test_nativeha_crr_card_is_replication_health_not_group_roles():
    from mqlab.clusterboard import nativeha_crr_card

    tiles = nativeha_crr_card("promtest", y=0)
    titles = [t["title"] for t in tiles]
    # the redundant Live/Recovery group-role tiles are gone (#279 feedback); the card is the
    # cross-region replication health (which site is live is shown in the instances section).
    assert titles == ["CRR connected", "CRR in-sync", "CRR backlog"]
    assert not any("group role" in t.lower() for t in titles)
    assert 'cluster_nha_connected{group="Recovery"}' in tiles[0]["targets"][0]["expr"]
    assert "cluster_nha_group_backlog" in tiles[2]["targets"][0]["expr"]


def test_site_role_chip_flips_live_recovery_by_data():
    from mqlab.clusterboard import _site_role_badge

    chip = _site_role_badge("nha-rhel-a.*", "promtest", 0, 7)
    # the chip derives its role from the site's instances (max role code: 2=Active→LIVE,
    # 3=Leader→RECOVERY) so it flips on failover; green LIVE vs blue RECOVERY (a healthy
    # standby, not a yellow warning), background-lit (#399)
    opts = chip["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert opts["2"]["text"] == "LIVE" and opts["2"]["color"] == "green"
    assert opts["3"]["text"] == "RECOVERY" and opts["3"]["color"] == "blue"
    assert chip["options"]["colorMode"] == "background"
    assert 'member=~"nha-rhel-a.*"' in chip["targets"][0]["expr"]
    # it is a compact chip beside the matrix (narrow), not a full row
    assert chip["gridPos"]["w"] == 5 and chip["gridPos"]["h"] == 7


# ── RDQM arm (#287) ───────────────────────────────────────────────────────────


def test_rdqm_role_mapping_codes_primary_secondary_unknown():
    from mqlab.clusterboard import _MAPPINGS

    opts = _MAPPINGS["rdqm_role"][0]["options"]
    # Primary (runs the QM) is the live green leader; Secondary is the healthy standby (blue);
    # only a genuinely unknown/down instance is red.
    assert opts["2"]["text"] == "Primary" and opts["2"]["color"] == "green"
    assert opts["1"]["text"] == "Secondary" and opts["1"]["color"] == "blue"
    assert opts["0"]["text"] == "Unknown" and opts["0"]["color"] == "red"


def test_rdqm_instance_cols_for_a_site():
    from mqlab.clusterboard import _rdqm_instance_cols

    cols = _rdqm_instance_cols("rdqm-a.*")
    # a single "Pacemaker" summary cell sits between QM-running and DRBD-in-sync so the
    # drill-down reads left-to-right: a NOT-READY site shows WHY (pacemaker) inline, with the
    # per-resource detail one section down in ③ (#287 feedback).
    assert [c[0] for c in cols] == ["HA status", "role", "QM running", "Pacemaker", "DRBD in-sync"]
    assert cols[1][2] == "rdqm_role"  # role column uses the RDQM role mapping
    assert "cluster_rdqm_role_code" in cols[1][1]
    # the QM-running column uses a NON-alarming mapping: a standby reads neutral, not red "down"
    assert cols[2][2] == "qm_running"
    assert "cluster_rdqm_qm_running" in cols[2][1]
    # the Pacemaker summary cell = node_ready (online AND able to run the QM); banned -> red
    assert cols[3][0] == "Pacemaker" and cols[3][2] == "node_ready"
    assert "cluster_rdqm_node_ready" in cols[3][1]
    # "DRBD in-sync" keys off the DRBD disk state (UpToDate), not the raw out-of-sync byte
    # counter — DRBD's activity-log granularity leaves a benign ~1KB out-of-sync on a fully
    # UpToDate node, which must NOT read as out-of-sync here (the honest byte count lives in ③)
    assert cols[4][2] == "up"
    assert "cluster_drbd_disk" in cols[4][1] and 'disk="UpToDate"' in cols[4][1]
    # the per-member columns scope to the site's members; the DRBD column scopes by node +
    # the HA resource so the DR resource (qmrdqm.dr) doesn't leak into the in-sync cell
    assert 'member=~"rdqm-a.*"' in cols[0][1]
    assert 'resource="qmrdqm"' in cols[4][1] and 'node=~"rdqm-a.*"' in cols[4][1]
    assert "qmrdqm.dr" not in cols[4][1]


def test_rdqm_qm_running_mapping_is_neutral_for_standby():
    from mqlab.clusterboard import _MAPPINGS

    opts = _MAPPINGS["qm_running"][0]["options"]
    # 1 = running (green); 0 = standby — a healthy non-serving node, NOT a red failure (#287)
    assert opts["1"]["color"] == "green"
    assert opts["0"]["color"] != "red"
    assert "standby" in opts["0"]["text"].lower()


def test_rdqm_site_badge_flips_live_recovery_by_dr_role():
    from mqlab.clusterboard import _rdqm_site_badge

    chip = _rdqm_site_badge("rdqm_a", "promtest", 0, 7)
    # LIVE (DR primary) = green; RECOVERY (DR secondary, standby & ready) = BLUE — our standby
    # colour, NOT yellow (yellow means warning); a RECOVERY site that is banned (can't fail over)
    # = RED, matching the integrity hazard (#287 feedback).
    opts = chip["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert opts["2"]["text"] == "LIVE" and opts["2"]["color"] == "green"
    assert opts["1"]["text"] == "RECOVERY" and opts["1"]["color"] == "blue"
    assert opts["0"]["color"] == "red"  # recovery site that cannot run the QM
    assert chip["options"]["colorMode"] == "background"
    expr = chip["targets"][0]["expr"]
    # the chip combines the DR role with pacemaker startability so it goes red when banned
    assert "cluster_rdqm_dr_role_code" in expr and "cluster_rdqm_qm_startable" in expr
    assert 'groups=~"rdqm_a"' in expr
    # a compact chip beside the (widened) matrix, sized to match the matrix (#300)
    assert chip["gridPos"]["w"] == 4 and chip["gridPos"]["h"] == 6


def test_rdqm_matrices_are_right_sized_and_fit_without_horizontal_scroll():
    """The ②/③ matrices fit their rows (no ~2 empty rows) and let columns shrink to fit
    the panel (a small minWidth) so no horizontal scrollbar appears (#300)."""
    board = render_cluster_dashboard({}, arm="rdqm-rhel")
    by_title = {p["title"]: p for p in board["panels"] if p.get("title")}
    # 3-node instance + pacemaker matrices are sized to their rows, not two rows taller
    for t in ("Site A", "Site B", "Pacemaker — Site A", "Pacemaker — Site B"):
        p = by_title[t]
        assert p["gridPos"]["h"] == 6, t
        # columns can shrink below their content width -> no overflow -> no horizontal scroll
        assert p["fieldConfig"]["defaults"]["custom"]["minWidth"] <= 100, t
    # the six-node storage matrix is right-sized too
    assert by_title["Storage — DRBD"]["gridPos"]["h"] == 10
    assert by_title["Storage — DRBD"]["fieldConfig"]["defaults"]["custom"]["minWidth"] <= 100


def test_rdqm_board_sections_are_contiguous_no_vertical_gaps():
    """Right-sizing must reflow the y-stack so sections stay snug — a matrix's successor
    sits immediately below it, never leaving the old padded gap (#300)."""
    board = render_cluster_dashboard({}, arm="rdqm-rhel")
    by_title = {p["title"]: p for p in board["panels"] if p.get("title")}
    site_a, site_b = by_title["Site A"], by_title["Site B"]
    # Site B begins exactly where Site A ends (no gap, no overlap)
    assert site_b["gridPos"]["y"] == site_a["gridPos"]["y"] + site_a["gridPos"]["h"]
    pace_a, pace_b = by_title["Pacemaker — Site A"], by_title["Pacemaker — Site B"]
    assert pace_b["gridPos"]["y"] == pace_a["gridPos"]["y"] + pace_a["gridPos"]["h"]
    # the LIVE/RECOVERY badge beside Site A shares its top edge and height
    a_y = site_a["gridPos"]["y"]
    badge = next(
        p
        for p in board["panels"]
        if p["type"] == "stat" and p["gridPos"]["w"] == 4 and p["gridPos"]["y"] == a_y
    )
    assert badge["gridPos"]["h"] == site_a["gridPos"]["h"]


def test_rdqm_status_band_is_one_compact_full_width_row():
    tiles = rdqm_status_band("promtest", y=3)
    assert [t["title"] for t in tiles] == [
        "Running on",
        "HA status",
        "Nodes online",
        "Floating IP",
        "Integrity",
    ]
    assert all(t["gridPos"]["y"] == 3 for t in tiles)
    assert sum(t["gridPos"]["w"] for t in tiles) == 24
    assert all(t["gridPos"]["h"] == 3 for t in tiles)
    assert all(t["options"]["text"]["valueSize"] == 22 for t in tiles)
    # running-on is the resolved QM owner; the floating-IP tile is first-class (the single VIP)
    assert 'cluster_resource_owner{resource="RDQMAPP"}' in tiles[0]["targets"][0]["expr"]
    assert "cluster_rdqm_floating_ip" in tiles[3]["targets"][0]["expr"]
    # the integrity tile carries the gated hazard expr (no false green on no-data)
    integ = tiles[4]["targets"][0]["expr"]
    assert "count(cluster_rdqm_ha_status_ok) > 0" in integ


def test_rdqm_integrity_is_pcmk_style_drbd_plus_ha_not_normal():
    from mqlab.clusterboard import _rdqm_integrity_expr

    expr = _rdqm_integrity_expr()
    # PCMK-style DRBD hazards: split-brain (StandAlone), Diskless, dual-primary
    assert "StandAlone" in expr and "Diskless" in expr
    # dual-primary must be counted PER RESOURCE *AND PER SITE*: a DR pair legitimately has one
    # qmrdqm primary on each side (2 globally), which must NOT read as split-brain (#287)
    assert "count by (resource, groups)(cluster_drbd_role" in expr
    # plus the RDQM HA-not-Normal hazard, gated on data present so no-data reads STALE
    assert "cluster_rdqm_ha_status_ok == 0" in expr
    assert "and on() (count(cluster_rdqm_ha_status_ok) > 0)" in expr
    # DRBD hazards are scoped to rdqm groups so another arm's DRBD can't trip this light
    assert 'groups=~"rdqm_a|rdqm_b"' in expr


def test_rdqm_storage_matrix_is_the_ha_drbd_resource():
    from mqlab.clusterboard import _rdqm_storage_cols

    cols = _rdqm_storage_cols()
    assert [c[0] for c in cols] == ["resync %", "out-of-sync"]
    assert all('resource="qmrdqm"' in expr for _, expr, _ in cols)
    assert all('node=~"rdqm-.*"' in expr for _, expr, _ in cols)
    assert "cluster_drbd_resync_pct" in cols[0][1]
    assert "cluster_drbd_out_of_sync_bytes" in cols[1][1]


def test_rdqm_dr_card_is_status_primary_failover_ready_and_backlog():
    tiles = rdqm_dr_card("promtest", y=0)
    assert [t["title"] for t in tiles] == [
        "DR status",
        "DR primary",
        "Failover ready",
        "DR backlog",
    ]
    assert "cluster_rdqm_dr_status_ok" in tiles[0]["targets"][0]["expr"]
    # "DR primary" names the SIDE (Site A/B), not the three individual hosts on that side (#287)
    dr_primary = tiles[1]["targets"][0]["expr"]
    assert 'cluster_rdqm_dr_role{role="Primary"}' in dr_primary
    assert "max by (groups)" in dr_primary  # collapse the side's hosts to one value
    assert "Site A" in dr_primary and "Site B" in dr_primary  # relabelled to the side name
    assert tiles[1]["targets"][0]["legendFormat"] == "{{site}}"
    # "Failover ready": red when a whole site can't start the QM (pacemaker view), STALE on
    # no data — this is the panel that turns a banned recovery site loud (#287)
    fr = tiles[2]
    assert "cluster_rdqm_qm_startable" in fr["targets"][0]["expr"]
    fr_maps = fr["fieldConfig"]["defaults"]["mappings"]
    assert any(m.get("options", {}).get("0", {}).get("color") == "green" for m in fr_maps)
    assert any(m["type"] == "special" for m in fr_maps)  # STALE on no-data
    # the backlog tile reads the DR DRBD resource's out-of-sync bytes (no rdqmstatus field)
    assert 'cluster_drbd_out_of_sync_bytes{resource="qmrdqm.dr"}' in tiles[3]["targets"][0]["expr"]
    assert tiles[3]["fieldConfig"]["defaults"]["unit"] == "bytes"


def test_rdqm_pm_state_and_failcount_mappings():
    from mqlab.clusterboard import _MAPPINGS

    pm = _MAPPINGS["pm_state"][0]["options"]
    assert pm["2"]["color"] == "green"  # Started/Promoted = active
    assert pm["1"]["color"] == "blue"  # Unpromoted replica
    assert pm["0"]["color"] != "red"  # Stopped is neutral, not an alarm
    fc = _MAPPINGS["failcount"]
    # fail-count 0 = green ok; pacemaker INFINITY (1000000) = red BANNED
    assert fc[0]["options"]["0"]["color"] == "green"
    assert any(
        m["type"] == "range"
        and m["options"]["from"] >= 1000000
        and m["options"]["result"]["color"] == "red"
        for m in fc
    )


def test_rdqm_pacemaker_cols_expose_the_resource_stack():
    from mqlab.clusterboard import _rdqm_pacemaker_cols

    cols = _rdqm_pacemaker_cols("rdqm-b.*")
    assert [c[0] for c in cols] == ["ready", "QM", "DRBD HA", "DR repl", "float-IP", "fail-count"]
    # "ready" = online AND able to run the QM (banned node reads red "blocked", not green up)
    assert "cluster_rdqm_node_ready" in cols[0][1] and cols[0][2] == "node_ready"
    # the QM / DRBD / IP cells read pacemaker resource state; the fail-count cell is the alarm
    assert 'resource="qmrdqm"' in cols[1][1] and cols[1][2] == "pm_state"
    assert 'resource="p_drbd_qmrdqm"' in cols[2][1]
    assert "cluster_rdqm_failcount" in cols[5][1] and cols[5][2] == "failcount"
    assert all('member=~"rdqm-b.*"' in expr for _, expr, _ in cols)


def test_rdqm_wide_matrices_drop_phantom_join_columns():
    # joinByField leaves a Time column per instant query (Time, Time 1, Time 2, …); the organize
    # transform only excludes "Time", so the rest survive as hidden columns that widen the table
    # into a horizontal scrollbar. The rdqm matrices must filter down to node + their real
    # columns so a 3-col storage table (and the 6/7-col matrices) don't scroll horizontally (#287).
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    for title in ("Site A", "Pacemaker — Site A", "Storage — DRBD"):
        p = next(x for x in d["panels"] if x.get("title") == title)
        flt = next((t for t in p["transformations"] if t["id"] == "filterFieldsByName"), None)
        assert flt is not None, f"{title} keeps phantom join columns -> horizontal scrollbar"
        kept = flt["options"]["include"]["names"]
        assert "node" in kept and not any(k.startswith("Time") for k in kept)


def test_rdqm_node_ready_mapping_is_green_ready_red_blocked():
    from mqlab.clusterboard import _MAPPINGS

    opts = _MAPPINGS["node_ready"][0]["options"]
    assert opts["1"]["color"] == "green"  # online + startable = a usable host
    assert opts["0"]["color"] == "red"  # online-but-banned (or offline) = blocked, not green


def test_rdqm_integrity_flags_a_site_that_cannot_run_the_qm():
    from mqlab.clusterboard import _rdqm_integrity_expr

    expr = _rdqm_integrity_expr()
    # a site (group) with no startable node = the QM can't run there (live outage, or DR not
    # viable) → a first-class hazard, derived from the pacemaker fail-counts (#287)
    assert "cluster_rdqm_qm_startable" in expr
    assert "max by (groups)(cluster_rdqm_qm_startable) == 0" in expr


def test_rdqm_storage_out_of_sync_tolerates_a_sub_extent_delta():
    from mqlab.clusterboard import _rdqm_storage_cols

    cols = _rdqm_storage_cols()
    oos = cols[1]  # ("out-of-sync", expr, kind)
    assert oos[0] == "out-of-sync"
    assert oos[2] == "drbd_oos"  # tolerant mapping, not the hard clean0
    from mqlab.clusterboard import _MAPPINGS

    maps = _MAPPINGS["drbd_oos"]
    # a benign sub-extent delta (e.g. 1024 B secondary↔secondary) is GREEN, not red; a real
    # backlog (≥ one 4 KiB extent) is red. A completely-normal state must read green (#287).
    green = next(m for m in maps if m["options"].get("result", {}).get("color") == "green")
    assert green["options"]["to"] >= 1024
    assert any(m["options"].get("result", {}).get("color") == "red" for m in maps)


def test_rdqm_board_has_a_pacemaker_resource_section():
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    row_titles = [p["title"] for p in d["panels"] if p["type"] == "row"]
    assert any("Pacemaker" in t for t in row_titles)  # ⑤ pacemaker section
    by_title = {p.get("title", ""): p for p in d["panels"]}
    assert "Pacemaker — Site A" in by_title and "Pacemaker — Site B" in by_title
    # the pacemaker matrices carry the fail-count column (what exposes a banned QM)
    blob = json.dumps(by_title["Pacemaker — Site B"])
    assert "cluster_rdqm_failcount" in blob and "cluster_rdqm_pm_state" in blob


def test_rdqm_board_uid_sections_and_fixed_site_labels():
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    assert d["uid"] == "lab-rdqm-rhel-cluster"
    assert "rdqm-rhel" in d["tags"]
    by_title = {p.get("title", ""): p for p in d["panels"]}
    # matrices labelled by FIXED site (A/B), never by the dynamic live/recovery role
    assert "Site A" in by_title and "Site B" in by_title
    assert "Running on" in by_title and "Integrity" in by_title
    site_a, site_b = by_title["Site A"], by_title["Site B"]
    assert site_a["gridPos"]["y"] < site_b["gridPos"]["y"]
    banner = next(p for p in d["panels"] if p["type"] == "text")
    assert "RDQM" in banner["options"]["content"]
    for p in d["panels"]:
        if p["type"] == "table" and p["title"] in ("Site A", "Site B"):
            assert "Live" not in p["title"] and "Recovery" not in p["title"]
    assert any('member=~"rdqm-a.*"' in t["expr"] for t in site_a["targets"])
    assert any('member=~"rdqm-b.*"' in t["expr"] for t in site_b["targets"])


def test_rdqm_board_has_storage_dr_timeline_logs_perf_net():
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    titles = [p.get("title", "") for p in d["panels"]]
    types = {p["type"] for p in d["panels"]}
    row_titles = [p["title"] for p in d["panels"] if p["type"] == "row"]
    assert any(t.startswith("①") for t in row_titles)
    assert any(t.startswith("②") for t in row_titles)
    # pacemaker rides above storage (mirrors the PCMK/Ubuntu hierarchy), then DRBD storage, then DR
    assert any("③" in t and "Pacemaker" in t for t in row_titles)  # ③ pacemaker (above storage)
    assert any("④" in t and "Storage" in t for t in row_titles)  # ④ DRBD storage section
    assert any("⑤" in t and "DR" in t for t in row_titles)  # ⑤ cross-site DR
    assert "DR status" in titles and "DR backlog" in titles
    assert "state-timeline" in types  # failover timeline
    assert "logs" in types  # rdqm log row
    assert any("CPU busy" in t for t in titles)  # perf
    assert any("net-wan" in json.dumps(p) for p in d["panels"])  # DR replication plane
    assert d["templating"]["list"][0]["name"] == "level"  # severity toggle wired
    assert len(row_titles) >= 6


def test_rdqm_board_scopes_shared_metrics_to_rdqm_groups():
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    blob = json.dumps(d)
    # cluster_quorate / cluster_node_online are emitted by every arm — every use on the rdqm
    # board must be rdqm-scoped (by group or by rdqm node regex), else another arm leaks in
    for m in re.finditer(r"cluster_quorate(\{[^}]*\})?", blob):
        assert "rdqm_a|rdqm_b" in (m.group(1) or ""), "unscoped quorate on rdqm board"
    for m in re.finditer(r"cluster_node_online(\{[^}]*\})?", blob):
        assert "rdqm-" in (m.group(1) or "") or "rdqm_a|rdqm_b" in (m.group(1) or "")
    assert "pcmk_a|pcmk_b" not in blob and "nha_rhel" not in blob  # no other arm's scope


def test_rdqm_log_panel_includes_ibm_mq_diagnostic_stream():
    d = render_cluster_dashboard({}, arm="rdqm-rhel")
    logs = next(p for p in d["panels"] if p["type"] == "logs")
    assert "AMQERR" in logs["description"]
    assert "ibm-mq" in logs["targets"][0]["expr"]
    assert 'host=~"rdqm-.*"' in logs["targets"][0]["expr"]


def test_rdqm_perf_uses_rdqm_groups_with_hb_and_wan():
    from mqlab.clusterboard import rdqm_perf_section

    blob = json.dumps(rdqm_perf_section("promtest", y=0))
    assert "rdqm_a|rdqm_b" in blob  # CPU scoped to rdqm nodes
    assert "virbr-hb" in blob and "virbr-wan" in blob  # HA (heartbeat) + DR (wan) throughput


def test_pcmk_and_nativeha_boards_unchanged_by_rdqm_arm():
    # regression: adding the rdqm arm must not alter the PCMK / Native HA boards
    pcmk = render_cluster_dashboard({}, arm="pcmk-ubuntu")
    assert pcmk["uid"] == "lab-pcmk-ubuntu-cluster"
    assert "③ Storage — DRBD / SAN" in [p.get("title", "") for p in pcmk["panels"]]
    nha = render_cluster_dashboard({}, arm="nativeha-rhel")
    assert nha["uid"] == "lab-nativeha-rhel-cluster"
    assert "rdqm" not in json.dumps(nha)  # no rdqm plumbing leaked onto the nha board
