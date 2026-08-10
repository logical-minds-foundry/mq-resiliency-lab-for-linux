"""Per-QM state board (#489 → #521 v2): the pure builder — one board per app QM showing
that QM's health + its critical queues + its critical channels as *time-series trend graphs*,
all object-driven from the already-scraped ibmmq_* series. Mirrors tests/test_messagingboard.py."""

from __future__ import annotations

import json

from mqlab.clusterboard import _STALE_MAP
from mqlab.qmboard import (
    DASHBOARD_UID_PREFIX,
    qm_board_uid,
    qm_dashboard_path,
    qm_dashboard_paths_and_texts,
    render_qm_board,
)

# Fixture topology: ≥1 provisioned stack + a distinct second stack (cross-leak check) +
# a reserved stack (no provision) + the shared svc block (svc_qm = <svc.short>QM).
FIXTURE = {
    "svc": {"short": "SVC", "conn": "10.60.0.50"},
    "stacks": {
        "nha-x": {"short": "NHAX", "mechanism": "native-ha", "os": "ubuntu", "provision": "x.yml"},
        "other": {"short": "OTHR", "mechanism": "native-ha", "os": "ubuntu", "provision": "o.yml"},
        "reserved": {"short": "RSVD", "mechanism": "native-ha", "os": "ubuntu", "provision": None},
    },
}


def _board():
    return render_qm_board(FIXTURE, "nha-x", {"short": "NHAX"})


def _panels_of(board, kind):
    return [p for p in board["panels"] if p["type"] == kind]


def _stat_exprs(board):
    return [
        t["expr"] for p in _panels_of(board, "stat") for t in p.get("targets", []) if "expr" in t
    ]


def _timeseries_panels(board):
    return _panels_of(board, "timeseries")


def _panel_exprs(panel):
    return [t["expr"] for t in panel.get("targets", []) if "expr" in t]


def _all_exprs(board):
    return [t["expr"] for p in board["panels"] for t in p.get("targets", []) if "expr" in t]


def _panel_titled(board, needle):
    """The one timeseries panel whose title contains `needle` (unique per queue/channel)."""
    hits = [p for p in _timeseries_panels(board) if needle in p["title"]]
    assert len(hits) == 1, (needle, [p["title"] for p in hits])
    return hits[0]


def _row_titles(board):
    return [p["title"] for p in _panels_of(board, "row")]


def test_uid_prefix_and_helper():
    assert DASHBOARD_UID_PREFIX == "lab-qm-"
    assert qm_board_uid("NHAX") == "lab-qm-nhax"
    assert qm_board_uid("pcmk") == "lab-qm-pcmk"


def test_board_uid_and_sections_present():
    board = _board()
    assert board["uid"] == "lab-qm-nhax"
    # ① QM band still has compact stat pills; the trends are timeseries panels; there are
    # NO table panels anymore (this is the v2 change).
    assert _panels_of(board, "stat")
    assert _timeseries_panels(board)
    assert not _panels_of(board, "table")
    assert "cockpit" in board["tags"] and "nha-x" in board["tags"]


def test_the_only_graph_panels_are_timeseries():
    # every graphing panel is a timeseries (the v2 panel type); the sole non-graph panels are
    # the compact stat pills, the row headers, the text banner, and the per-object mq-events
    # logs panels (#526).
    board = _board()
    graph_types = {
        p["type"] for p in board["panels"] if p["type"] not in {"stat", "row", "text", "logs"}
    }
    assert graph_types == {"timeseries"}


def test_names_are_derived_from_short_with_no_cross_stack_leak():
    board = _board()
    exprs = " ".join(_all_exprs(board))
    # app QM + the three critical channels (SVRCONN / SDR / RCVR) + both critical queues
    assert 'qmgr="NHAXAPP"' in exprs
    assert 'queue="APP.REPLY"' in exprs
    assert 'queue="SVCQM"' in exprs  # the XMITQ to the svc counterparty
    assert 'channel="APP.SVRCONN"' in exprs
    assert 'channel="NHAXAPP.SVCQM"' in exprs  # SDR → svc
    assert 'channel="SVCQM.NHAXAPP"' in exprs  # RCVR ← svc
    # no other stack's app QM leaks into this board
    assert "OTHRAPP" not in json.dumps(board)
    assert "PCMKAPP" not in json.dumps(board)


def test_qm_band_binds_the_verified_metric_names():
    blob = " ".join(_all_exprs(_board()))
    assert "ibmmq_qmgr_status" in blob
    assert "ibmmq_qmgr_uptime" in blob
    assert "ibmmq_qmgr_connection_count" in blob
    # msg-rate mirrors dashboard.py:_qm_rate_panel (interval put+1 and destructive get)
    assert "rate(ibmmq_qmgr_interval_mqput_mqput1_total_count" in blob
    assert "rate(ibmmq_qmgr_interval_destructive_get_total_count" in blob
    # services pill folds initiator + command server + a live listener
    assert "ibmmq_qmgr_channel_initiator_status" in blob
    assert "ibmmq_qmgr_command_server_status" in blob
    assert "ibmmq_qmgr_active_listeners" in blob
    # recovery-log % = restart / (restart + reusable)
    assert "ibmmq_qmgr_log_size_restart" in blob
    assert "ibmmq_qmgr_log_size_reusable" in blob


def test_band_connections_rate_and_log_are_now_timeseries():
    # the story-telling counts/rates moved out of stat tiles into trend graphs.
    board = _board()
    conn = _panel_titled(board, "Connections")
    rate = _panel_titled(board, "Msg rate")
    log = _panel_titled(board, "Recovery log %")
    assert "ibmmq_qmgr_connection_count" in " ".join(_panel_exprs(conn))
    assert "ibmmq_qmgr_interval_mqput_mqput1_total_count" in " ".join(_panel_exprs(rate))
    assert "ibmmq_qmgr_log_size_restart" in " ".join(_panel_exprs(log))


def test_status_pills_are_object_driven_and_carry_stale():
    # the compact QM pills (status / uptime / services) stay object-driven: a missing series
    # reads no-data (or STALE), never a false zero — every pill expr ends with the sentinel.
    board = _board()
    for expr in _stat_exprs(board):
        assert expr.endswith("or vector(-1)"), expr
    status_tiles = [
        p
        for p in _panels_of(board, "stat")
        if _STALE_MAP in p["fieldConfig"]["defaults"].get("mappings", [])
    ]
    assert status_tiles  # at least the QM status + services pills carry STALE


def test_each_queue_has_its_own_labelled_block_of_four_trend_graphs():
    board = _board()
    rows = _row_titles(board)
    # a labelled row per critical queue
    assert any("Queue · APP.REPLY" in t for t in rows)
    assert any("Queue · SVCQM" in t for t in rows)
    # four trend graphs per queue: depth · flow · handles · age
    for q in ("APP.REPLY", "SVCQM"):
        titles = [p["title"] for p in _timeseries_panels(board) if p["title"].startswith(q)]
        assert any("depth" in t for t in titles)
        assert any("flow" in t for t in titles)
        assert any("handles" in t for t in titles)
        assert any("age" in t for t in titles)


def test_queue_graphs_bind_verified_queue_metrics():
    blob = " ".join(_all_exprs(_board()))
    assert "ibmmq_queue_depth" in blob
    assert "ibmmq_queue_attribute_max_depth" in blob  # depth reference series
    assert "ibmmq_queue_oldest_message_age" in blob
    assert "ibmmq_queue_uncommitted_messages" in blob
    assert "ibmmq_queue_input_handles" in blob
    assert "ibmmq_queue_output_handles" in blob
    # verified-at-build: the real counter is mqput_mqput1_count / mqget_count (no _total_)
    assert "rate(ibmmq_queue_mqput_mqput1_count" in blob
    assert "rate(ibmmq_queue_mqget_count" in blob


def test_flow_and_handles_graphs_group_both_series_on_one_panel():
    # the relationship IS the derivative: enqueue vs dequeue share one graph, and
    # in-handles vs out-handles share one graph.
    board = _board()
    flow = _panel_titled(board, "APP.REPLY — flow")
    flow_blob = " ".join(_panel_exprs(flow))
    assert "rate(ibmmq_queue_mqput_mqput1_count" in flow_blob
    assert "rate(ibmmq_queue_mqget_count" in flow_blob
    handles = _panel_titled(board, "APP.REPLY — handles")
    handles_blob = " ".join(_panel_exprs(handles))
    assert "ibmmq_queue_input_handles" in handles_blob
    assert "ibmmq_queue_output_handles" in handles_blob


def test_each_channel_has_its_own_labelled_block_of_three_trend_graphs():
    board = _board()
    rows = _row_titles(board)
    for chan in ("APP.SVRCONN", "NHAXAPP.SVCQM", "SVCQM.NHAXAPP"):
        assert any(f"Channel · {chan}" in t for t in rows)
        titles = [p["title"] for p in _timeseries_panels(board) if p["title"].startswith(chan)]
        assert any("throughput" in t for t in titles)
        assert any("nettime" in t for t in titles)
        assert any("status" in t for t in titles)


def test_channel_graphs_bind_verified_channel_metrics():
    blob = " ".join(_all_exprs(_board()))
    assert "ibmmq_channel_messages" in blob
    assert "ibmmq_channel_bytes_sent" in blob
    assert "ibmmq_channel_bytes_rcvd" in blob
    assert "ibmmq_channel_nettime_short" in blob
    # throughput counters must be rate()-d (they're `# TYPE counter` — a raw plot is a
    # meaningless climbing line), while nettime (a gauge) stays raw
    assert "rate(ibmmq_channel_messages" in blob
    assert "rate(ibmmq_channel_bytes_sent" in blob
    assert "rate(ibmmq_channel_nettime_short" not in blob
    assert "ibmmq_channel_status_squash" in blob


def test_channel_status_timeline_keeps_the_no_status_sentinel():
    # the status graph must show WHEN a channel dropped: a missing series reads -1 (No status),
    # not an empty gap — so the status series keeps its `or vector(-1)` sentinel.
    board = _board()
    status = _panel_titled(board, "APP.SVRCONN — status")
    expr = _panel_exprs(status)[0]
    assert "ibmmq_channel_status_squash" in expr
    assert expr.endswith("or vector(-1)")


def test_rename_short_makes_the_board_follow_it():
    b2 = render_qm_board(FIXTURE, "nha-x", {"short": "ZZZ"})
    assert b2["uid"] == "lab-qm-zzz"
    assert 'qmgr="ZZZAPP"' in " ".join(_all_exprs(b2))
    assert "NHAXAPP" not in json.dumps(b2)  # nothing hardcoded — the board tracks the short


def test_dashboard_path_is_per_qm_lowercased():
    assert qm_dashboard_path("PCMK").name == "lab-qm-pcmk.json"
    assert qm_dashboard_path("nhau").name == "lab-qm-nhau.json"


def test_real_topology_smoke_one_valid_board_per_app_qm():
    pairs = qm_dashboard_paths_and_texts()
    uids = set()
    for path, text in pairs:
        board = json.loads(text)  # valid JSON
        uids.add(board["uid"])
        assert path.name == f"{board['uid']}.json"
        # each real board renders as time-series trends
        assert any(p["type"] == "timeseries" for p in board["panels"])
    assert {"lab-qm-pcmk", "lab-qm-rdqm", "lab-qm-nhar", "lab-qm-nhau"} <= uids


def test_write_renders_provisioned_stacks_only(monkeypatch, tmp_path):
    import mqlab.qmboard as qb

    monkeypatch.setattr(qb, "_lab_topology", lambda: FIXTURE)
    monkeypatch.setattr(
        qb, "qm_dashboard_path", lambda short, folder="": tmp_path / f"lab-qm-{short.lower()}.json"
    )
    paths = qb.write_qm_dashboards()
    names = sorted(p.name for p in paths)
    # the reserved stack (provision: null) is filtered out; the two provisioned ones render
    assert names == ["lab-qm-nhax.json", "lab-qm-othr.json"]
    assert "NHAXAPP" in (tmp_path / "lab-qm-nhax.json").read_text()


def _logs_panels(board):
    return _panels_of(board, "logs")


def test_per_object_event_panels_present_and_scoped():
    # #526: each object on the board carries an MQ instrumentation-events panel reading the
    # {unit="mq-events"} JSON stream, scoped to that object. NHAX → app_qm NHAXAPP, svc SVCQM.
    board = _board()
    exprs = [p["targets"][0]["expr"] for p in _logs_panels(board)]
    assert exprs, "expected mq-events panels on the QM board"
    assert all('unit="mq-events"' in e and "| json" in e for e in exprs)
    # ① QM-level: all events for this QM (eventData.queueMgrName)
    assert any('eventData_queueMgrName="NHAXAPP"' in e for e in exprs)
    # ② each critical queue (APP.REPLY + the svc XMITQ SVCQM), by the affected-object field
    assert any('eventSource_objectName="APP.REPLY"' in e for e in exprs)
    assert any('eventSource_objectName="SVCQM"' in e for e in exprs)
    # ③ each critical channel (SVRCONN in, SDR out, RCVR back)
    assert any('eventSource_objectName="APP.SVRCONN"' in e for e in exprs)
    assert any('eventSource_objectName="NHAXAPP.SVCQM"' in e for e in exprs)
    assert any('eventSource_objectName="SVCQM.NHAXAPP"' in e for e in exprs)


def test_event_panels_bind_the_loki_datasource():
    board = render_qm_board(FIXTURE, "nha-x", {"short": "NHAX"}, loki_uid="loki-x")
    logs = _logs_panels(board)
    assert logs and all(p["datasource"] == {"type": "loki", "uid": "loki-x"} for p in logs)


# ── ④ log-health band (Native HA) — #811 ──────────────────────────────────────


def _nha_board(short="NHAX"):
    """A board for a native-ha arm (the band gates on mechanism == native-ha)."""
    return render_qm_board(FIXTURE, "nha-x", {"short": short, "mechanism": "native-ha"})


def test_log_health_band_uses_instance_series():
    from mqlab.qmboard import _log_health_band

    panels = _log_health_band("ds", "QM1", y=800)
    joined = str(panels)
    assert "mqlab_log_disk_used_bytes" in joined
    assert "mqlab_log_extents_active" in joined
    assert "by (instance" in joined  # instance-aware series (per-replica divergence)
    assert panels[0]["type"] == "timeseries"  # time-series-led, not a stat tile


def test_log_health_band_leads_with_the_reclaim_health_story():
    # the spike headline: a monotonic extent rise with 0 reuse. The band leads with the
    # standard (S, in-use) vs reserved (R, recycled) extent trend, both on the lead panel.
    from mqlab.qmboard import _log_health_band

    lead = _log_health_band("ds", "QM1", y=0)[0]
    blob = " ".join(t["expr"] for t in lead["targets"])
    assert "mqlab_log_extents_active" in blob
    assert "mqlab_log_extents_inactive" in blob


def test_log_health_band_binds_every_collector_metric_raw_not_rated():
    from mqlab.qmboard import _log_health_band

    blob = str(_log_health_band("ds", "QM1", y=0))
    for metric in (
        "mqlab_log_disk_used_bytes",
        "mqlab_log_disk_total_bytes",
        "mqlab_log_extents_active",
        "mqlab_log_extents_inactive",
        "mqlab_log_sample_stale",
    ):
        assert metric in blob
    # every mqlab_log_* series is a gauge (a point-in-time df/ls reading — loglifecycle.py
    # emits no counters), so it is plotted raw; rate() would be meaningless.
    assert "rate(mqlab_log_" not in blob


def test_log_health_band_qm_is_a_variable_not_a_literal():
    from mqlab.qmboard import _log_health_band

    blob = str(_log_health_band("ds", "ZZZAPP", y=0))
    assert 'qm="ZZZAPP"' in blob
    assert "QM1" not in blob


def test_log_health_gauges_degrade_gracefully_with_the_sentinel():
    # the two slow gauges (media-image recency + collector sample) are stat tiles that read
    # no-data — never a false value — when their series is absent: every expr ends with the
    # object-driven sentinel.
    from mqlab.qmboard import _log_health_band

    stats = [p for p in _log_health_band("ds", "QM1", y=0) if p["type"] == "stat"]
    assert stats
    for panel in stats:
        assert panel["targets"][0]["expr"].endswith("or vector(-1)")


def test_log_health_media_recency_is_a_slow_gauge_not_a_trend():
    # spike-gated: media recency is coarse-grained (moves only on the automatic-image
    # schedule, pinned on a young QM), so it ships as a single-value slow gauge that degrades
    # gracefully — not a reactive/second-scale trend panel.
    from mqlab.qmboard import _log_health_band

    media = [
        p
        for p in _log_health_band("ds", "QM1", y=0)
        if p["type"] == "stat" and "Media" in p["title"]
    ]
    assert len(media) == 1
    assert "mqlab_log_media" in media[0]["targets"][0]["expr"]


def test_log_health_band_has_a_logger_event_log():
    from mqlab.qmboard import _log_health_band

    logs = [p for p in _log_health_band("ds", "QM1", y=0, loki_uid="loki-z") if p["type"] == "logs"]
    assert len(logs) == 1
    expr = logs[0]["targets"][0]["expr"]
    assert 'unit="mq-events"' in expr and "| json" in expr
    assert "SYSTEM.ADMIN.LOGGER.EVENT" in expr  # the logger-event source (spike S2)
    assert logs[0]["datasource"] == {"type": "loki", "uid": "loki-z"}


def test_log_health_band_wired_only_for_native_ha_arms():
    # the band renders on a native-ha board (its collector is deployed only on the nha arms)
    # and is absent otherwise.
    nha = _nha_board()
    assert any("④ Log health" in t for t in _row_titles(nha))
    assert "mqlab_log_extents_active" in " ".join(_all_exprs(nha))
    # the default fixture cfg carries no mechanism → not native-ha → no band at all.
    plain = _board()
    assert not any("④ Log health" in t for t in _row_titles(plain))
    assert "mqlab_log_" not in json.dumps(plain)


def test_log_health_band_keeps_graph_panels_timeseries_only():
    board = _nha_board()
    graph_types = {
        p["type"] for p in board["panels"] if p["type"] not in {"stat", "row", "text", "logs"}
    }
    assert graph_types == {"timeseries"}
