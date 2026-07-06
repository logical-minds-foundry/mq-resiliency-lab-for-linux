"""Per-QM state board (#489): the pure builder — one board per app QM showing that
QM's health + its critical queues + its critical channels, all object-driven from the
already-scraped ibmmq_* series. Mirrors tests/test_messagingboard.py."""

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
        "nha-x": {"short": "NHAX", "mechanism": "m", "os": "o", "provision": "ansible/x.yml"},
        "other": {"short": "OTHR", "mechanism": "m", "os": "o", "provision": "ansible/o.yml"},
        "reserved": {"short": "RSVD", "mechanism": "m", "os": "o", "provision": None},
    },
}


def _board():
    return render_qm_board(FIXTURE, "nha-x", {"short": "NHAX"})


def _stat_exprs(board):
    return [
        t["expr"]
        for p in board["panels"]
        if p["type"] == "stat"
        for t in p.get("targets", [])
        if "expr" in t
    ]


def _tables(board):
    return [p for p in board["panels"] if p["type"] == "table"]


def _table_exprs(board):
    return [t["expr"] for p in _tables(board) for t in p.get("targets", []) if "expr" in t]


def test_uid_prefix_and_helper():
    assert DASHBOARD_UID_PREFIX == "lab-qm-"
    assert qm_board_uid("NHAX") == "lab-qm-nhax"
    assert qm_board_uid("pcmk") == "lab-qm-pcmk"


def test_board_uid_and_three_sections_present():
    board = _board()
    assert board["uid"] == "lab-qm-nhax"
    # ① QM band = stat tiles; ② queues + ③ channels = exactly two tables.
    assert any(p["type"] == "stat" for p in board["panels"])
    assert len(_tables(board)) == 2
    assert "cockpit" in board["tags"] and "nha-x" in board["tags"]


def test_names_are_derived_from_short_with_no_cross_stack_leak():
    board = _board()
    exprs = " ".join(_stat_exprs(board) + _table_exprs(board))
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
    exprs = _stat_exprs(_board())
    blob = " ".join(exprs)
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


def test_every_band_value_tile_is_object_driven():
    # object-driven: a missing series must read as no-data, never a false zero — so every
    # QM-band value expr ends with the `or vector(-1)` sentinel.
    for expr in _stat_exprs(_board()):
        assert expr.endswith("or vector(-1)"), expr


def test_status_tile_carries_the_stale_map():
    board = _board()
    status_tiles = [
        p
        for p in board["panels"]
        if p["type"] == "stat" and _STALE_MAP in p["fieldConfig"]["defaults"].get("mappings", [])
    ]
    assert status_tiles  # at least the QM status + services pills carry STALE


def test_queue_columns_bind_verified_queue_metrics():
    exprs = " ".join(_table_exprs(_board()))
    assert "ibmmq_queue_depth" in exprs
    assert "ibmmq_queue_attribute_max_depth" in exprs  # %full
    assert "ibmmq_queue_oldest_message_age" in exprs
    assert "ibmmq_queue_uncommitted_messages" in exprs
    assert "ibmmq_queue_input_handles" in exprs
    assert "ibmmq_queue_output_handles" in exprs
    # verified-at-build: the real counter is mqput_mqput1_count / mqget_count (no _total_)
    assert "rate(ibmmq_queue_mqput_mqput1_count" in exprs
    assert "rate(ibmmq_queue_mqget_count" in exprs
    assert "ibmmq_queue_time_since_get" in exprs


def test_channel_columns_bind_verified_channel_metrics():
    exprs = " ".join(_table_exprs(_board()))
    assert "ibmmq_channel_status_squash" in exprs
    assert "ibmmq_channel_substate" in exprs
    assert "ibmmq_channel_messages" in exprs
    assert "ibmmq_channel_bytes_sent" in exprs
    assert "ibmmq_channel_bytes_rcvd" in exprs
    assert "ibmmq_channel_batches" in exprs
    assert "ibmmq_channel_nettime_short" in exprs
    assert "ibmmq_channel_time_since_msg" in exprs
    assert "ibmmq_channel_cur_inst" in exprs


def test_tables_are_object_driven_over_the_curated_objects():
    # each table column stamps every curated object's row (label_replace) so the row is
    # present even with no series, and every cell falls back to `or vector(-1)`.
    for expr in _table_exprs(_board()):
        assert "or vector(-1)" in expr
        assert "label_replace(" in expr
    blob = " ".join(_table_exprs(_board()))
    assert 'label_replace(max(ibmmq_queue_depth{qmgr="NHAXAPP",queue="APP.REPLY"})' in blob
    chan_cell = (
        'label_replace(max(ibmmq_channel_status_squash{qmgr="NHAXAPP",channel="APP.SVRCONN"})'
    )
    assert chan_cell in blob


def test_channel_status_column_is_colour_mapped_but_plain_columns_are_not():
    channels_table = _tables(_board())[1]
    overrides = channels_table["fieldConfig"]["overrides"]
    mapped_cols = {o["matcher"]["options"] for o in overrides}
    assert "Status" in mapped_cols  # channel status carries the coloured mapping
    assert "Messages" not in mapped_cols  # a plain numeric column has no mapping override


def test_rename_short_makes_the_board_follow_it():
    b2 = render_qm_board(FIXTURE, "nha-x", {"short": "ZZZ"})
    assert b2["uid"] == "lab-qm-zzz"
    assert 'qmgr="ZZZAPP"' in " ".join(_stat_exprs(b2) + _table_exprs(b2))
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
    assert {"lab-qm-pcmk", "lab-qm-rdqm", "lab-qm-nhar", "lab-qm-nhau"} <= uids


def test_write_renders_provisioned_stacks_only(monkeypatch, tmp_path):
    import mqlab.qmboard as qb

    monkeypatch.setattr(qb, "_lab_topology", lambda: FIXTURE)
    monkeypatch.setattr(
        qb, "qm_dashboard_path", lambda short: tmp_path / f"lab-qm-{short.lower()}.json"
    )
    paths = qb.write_qm_dashboards()
    names = sorted(p.name for p in paths)
    # the reserved stack (provision: null) is filtered out; the two provisioned ones render
    assert names == ["lab-qm-nhax.json", "lab-qm-othr.json"]
    assert "NHAXAPP" in (tmp_path / "lab-qm-nhax.json").read_text()
