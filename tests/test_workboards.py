"""Portable render mode + work-edition board helpers (#963, epic .github#169 Wave 1a).

These tests are the *portability contract* for the work-edition boards: the crux is
that a rendered board carries NO hardcoded datasource uid and NO lab-specific
QM/queue/channel name — its datasource is the ${datasource} template variable and its
objects are template variables ($qmgr, ...). The lab cockpits (clusterboard/qmboard)
stay lab-object-driven; this module layers portability on top of their primitives.
"""

from __future__ import annotations

import json
import re

from mqlab.workboards import (
    WORK_BOARD_BUILDERS,
    datasource_var,
    portable_dashboard,
    qmgr_var,
    tmpl_var,
    work_dashboard_paths_and_texts,
    write_work_dashboards,
)
from mqlab.workqmboard import FLOW_BOARD_UID, QM_BOARD_UID, work_qm_dashboard

# Lab-specific names that must NEVER leak into a portable board (the anti-contract).
_LAB_NAMES = ("NHAUAPP", "NHARCAPP", "SVCQM", "RDQMAPP", "APP.REPLY", "APP.SVRCONN", "PCMKAPP")


def _all_datasources(board):
    """Every datasource dict on the board — panel-level, target-level, recursively into
    collapsed row children."""
    found = []

    def walk(panels):
        for p in panels:
            if "datasource" in p:
                found.append(p["datasource"])
            for t in p.get("targets", []):
                if "datasource" in t:
                    found.append(t["datasource"])
            walk(p.get("panels", []))

    walk(board["panels"])
    return found


# ── tmpl_var ──────────────────────────────────────────────────────────────────


def test_tmpl_var_query_defaults_reference_the_datasource_variable():
    var = tmpl_var("qmgr", "label_values(ibmmq_qmgr_status, qmgr)")
    assert var["name"] == "qmgr"
    assert var["type"] == "query"
    assert var["query"] == "label_values(ibmmq_qmgr_status, qmgr)"
    # label defaults to the name; a query var's own datasource references ${datasource}
    assert var["label"] == "qmgr"
    assert var["datasource"] == {"type": "prometheus", "uid": "${datasource}"}
    assert var["current"] == {}


def test_tmpl_var_honours_overrides():
    var = tmpl_var(
        "queue",
        "label_values(ibmmq_queue_depth, queue)",
        label="Queue",
        multi=True,
        include_all=True,
        regex="/APP.*/",
        current={"text": "APP.REPLY", "value": "APP.REPLY"},
    )
    assert var["label"] == "Queue"
    assert var["multi"] is True
    assert var["includeAll"] is True
    assert var["regex"] == "/APP.*/"
    assert var["current"] == {"text": "APP.REPLY", "value": "APP.REPLY"}


def test_tmpl_var_datasource_type_carries_no_datasource_field():
    # the datasource picker variable is itself the source of ${datasource}, so it must
    # not reference one (that would be circular); its query is the plugin id.
    var = tmpl_var("datasource", "prometheus", var_type="datasource", label="Data source")
    assert var["type"] == "datasource"
    assert var["query"] == "prometheus"
    assert "datasource" not in var


def test_datasource_var_and_qmgr_var_helpers():
    ds = datasource_var()
    assert ds["name"] == "datasource"
    assert ds["type"] == "datasource"
    assert ds["query"] == "prometheus"
    qm = qmgr_var()
    assert qm["name"] == "qmgr"
    assert qm["query"] == "label_values(ibmmq_qmgr_status, qmgr)"
    assert qm["datasource"]["uid"] == "${datasource}"


# ── portable_dashboard: the portability contract ────────────────────────────────


def _example_panels():
    """A throwaway mixed-panel board that exercises every datasource site: a panel with a
    lab prometheus datasource + targets, a target that carries no datasource, a text panel
    with no datasource at all, and a collapsed row whose child rides a lab Loki datasource."""
    return [
        {"type": "text", "title": "banner", "gridPos": {"h": 2, "w": 24, "x": 0, "y": 0}},
        {
            "type": "stat",
            "title": "status",
            "datasource": {"type": "prometheus", "uid": "prometheus"},
            "gridPos": {"h": 4, "w": 8, "x": 0, "y": 2},
            "targets": [
                {
                    "refId": "A",
                    "expr": 'ibmmq_qmgr_status{qmgr="NHAUAPP"}',
                    "datasource": {"type": "prometheus", "uid": "prometheus"},
                },
                {"refId": "B", "expr": "up"},  # a target with NO datasource
            ],
        },
        {
            "type": "row",
            "title": "logs",
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": 6},
            "panels": [
                {
                    "type": "logs",
                    "title": "child",
                    "datasource": {"type": "loki", "uid": "loki"},
                    "gridPos": {"h": 8, "w": 24, "x": 0, "y": 7},
                    "targets": [
                        {
                            "refId": "A",
                            "expr": '{host="x"}',
                            "datasource": {"type": "loki", "uid": "loki"},
                        }
                    ],
                }
            ],
        },
    ]


def test_portable_dashboard_has_no_hardcoded_datasource_uid():
    board = portable_dashboard("Example", "work-example", _example_panels(), [datasource_var()])
    dsx = _all_datasources(board)
    assert dsx, "expected datasources on the example board"
    # every datasource uid is a template-variable reference, never a hardcoded lab uid
    for ds in dsx:
        assert ds["uid"].startswith("${"), ds
    # prometheus panels -> ${datasource}; loki panels -> ${loki}; both preserve the type
    assert {"type": "prometheus", "uid": "${datasource}"} in dsx
    assert {"type": "loki", "uid": "${loki}"} in dsx


def test_portable_dashboard_strips_lab_object_names():
    # the lab QM name baked into the example expr must not survive into a portable board's
    # datasource wiring; the expr text is the caller's to template with $qmgr, but the
    # datasources themselves must be portable regardless.
    board = portable_dashboard("Example", "work-example", _example_panels(), [datasource_var()])
    for ds in _all_datasources(board):
        # the uid is a ${...} template reference, never a bare hardcoded datasource uid
        assert ds["uid"] in ("${datasource}", "${loki}"), ds


def test_portable_dashboard_does_not_mutate_the_caller_panels():
    panels = _example_panels()
    portable_dashboard("Example", "work-example", panels, [datasource_var()])
    # the original lab datasource is untouched (deep-copied before rewriting)
    assert panels[1]["datasource"] == {"type": "prometheus", "uid": "prometheus"}


def test_portable_dashboard_shape_and_default_tags():
    board = portable_dashboard("Example", "work-example", _example_panels(), [datasource_var()])
    assert board["uid"] == "work-example"
    assert board["title"] == "Example"
    assert board["schemaVersion"] == 39
    assert board["templating"]["list"][0]["type"] == "datasource"
    assert board["tags"] == ["work", "portable"]


def test_portable_dashboard_accepts_custom_tags():
    board = portable_dashboard(
        "Example", "work-example", _example_panels(), [datasource_var()], tags=["custom"]
    )
    assert board["tags"] == ["custom"]


# ── the seeded work-edition boards ──────────────────────────────────────────────


def test_work_boards_are_registered_and_render_portable():
    assert WORK_BOARD_BUILDERS, "expected at least one work-edition board builder"
    for build in WORK_BOARD_BUILDERS:
        board = build()
        blob = json.dumps(board)
        # the crux: no hardcoded datasource uid, no lab object name
        for ds in _all_datasources(board):
            assert ds["uid"].startswith("${"), ds
        for name in _LAB_NAMES:
            assert name not in blob, (name, board["uid"])
        # $datasource + a $qmgr-style template variable are both present
        names = {v["name"] for v in board["templating"]["list"]}
        assert "datasource" in names
        assert "qmgr" in names
        # the qmgr object is a template variable, referenced (not hardcoded) in a query
        assert "$qmgr" in blob


def test_work_dashboard_paths_use_the_board_uid_as_the_filename(tmp_path):
    pairs = work_dashboard_paths_and_texts(tmp_path)
    assert pairs
    for path, text in pairs:
        board = json.loads(text)  # valid JSON
        assert path.name == f"{board['uid']}.json"
        assert path.parent == tmp_path


def test_write_work_dashboards_writes_files_under_out_dir(tmp_path):
    out_dir = tmp_path / "grafana" / "work-edition"
    paths = write_work_dashboards(out_dir)
    assert paths
    for path in paths:
        assert path.parent == out_dir
        assert path.exists()
        board = json.loads(path.read_text())
        assert board["uid"]


# ── the QM-view board (#964, Wave 1a) ───────────────────────────────────────────
#
# The FIRST work-edition board; these tests pin the conventions #965/#967 follow. Every
# PromQL must map to the Prometheus schema-note contract (Task 1); the board is QM-scoped
# (no per-queue/per-channel series — that is the Flow board, #965); the trend band uses
# rate() on counters; and a data link drills to the Flow board carrying $qmgr.

# Every ibmmq_* series the QM board is allowed to bind — the schema-note contract subset it
# uses (all qmgr-class; §3.1 object-status + §4.1 publication-driven). Binding anything else
# (any queue-/channel-class series, or a name not in the schema note) is a contract violation
# the board must never commit. Every name below maps to a row in prometheus-schema-note.md.
_QM_CONTRACT_METRICS = {
    # ① status & services + ③ attention — §3.1 object-status floor (failover-resilient)
    "ibmmq_qmgr_status",
    "ibmmq_qmgr_uptime",
    "ibmmq_qmgr_connection_count",
    "ibmmq_qmgr_channel_initiator_status",
    "ibmmq_qmgr_command_server_status",
    "ibmmq_qmgr_active_listeners",
    # ② trends — §4.1 publication-driven (message/byte rate, recovery-log %, CPU, MQI, latency)
    "ibmmq_qmgr_interval_mqput_mqput1_total_count",
    "ibmmq_qmgr_interval_destructive_get_total_count",
    "ibmmq_qmgr_interval_mqput_mqput1_total_bytes",
    "ibmmq_qmgr_interval_destructive_get_total_bytes",
    "ibmmq_qmgr_log_current_primary_space_in_use_percentage",
    "ibmmq_qmgr_cpu_load_one_minute_average_percentage",
    "ibmmq_qmgr_cpu_load_five_minute_average_percentage",
    "ibmmq_qmgr_cpu_load_fifteen_minute_average_percentage",
    "ibmmq_qmgr_failed_mqconn_mqconnx_count",
    "ibmmq_qmgr_failed_mqopen_count",
    "ibmmq_qmgr_failed_mqput_count",
    "ibmmq_qmgr_failed_mqput1_count",
    "ibmmq_qmgr_failed_mqclose_count",
    "ibmmq_qmgr_log_write_latency_seconds",
}


def test_work_qm_board_binds_only_verified_metrics_and_is_qm_scoped():
    dash = work_qm_dashboard()
    blob = json.dumps(dash)
    assert "ibmmq_qmgr_status" in blob and "$qmgr" in blob
    assert "ibmmq_queue_" not in blob  # per-queue detail belongs on the flow board (#965)
    assert "ibmmq_channel_" not in blob  # per-channel detail is the flow board's too
    for name in _LAB_NAMES:
        assert name not in blob, name


def test_work_qm_board_binds_only_schema_note_metrics():
    used = set(re.findall(r"ibmmq_[a-z0-9_]+", json.dumps(work_qm_dashboard())))
    assert used, "expected ibmmq_* bindings"
    assert used <= _QM_CONTRACT_METRICS, used - _QM_CONTRACT_METRICS


def test_work_qm_board_is_registered_and_uses_the_pinned_uid():
    assert work_qm_dashboard in WORK_BOARD_BUILDERS
    assert work_qm_dashboard()["uid"] == QM_BOARD_UID


def test_work_qm_trend_panels_are_timeseries_and_use_rate_for_counters():
    dash = work_qm_dashboard()
    ts_blobs = [json.dumps(p) for p in dash["panels"] if p.get("type") == "timeseries"]
    assert ts_blobs, "expected trend-band timeseries panels"
    # counters (the interval MQI totals) are only ever plotted as a rate(), never raw
    for counter in (
        "ibmmq_qmgr_interval_mqput_mqput1_total_count",
        "ibmmq_qmgr_interval_destructive_get_total_count",
    ):
        hits = [b for b in ts_blobs if counter in b]
        assert hits, f"{counter} not on a trend panel"
        for b in hits:
            assert f"rate({counter}" in b.replace(" ", ""), counter


def test_work_qm_board_has_flow_drilldown_carrying_qmgr():
    blob = json.dumps(work_qm_dashboard())
    assert f"/d/{FLOW_BOARD_UID}" in blob  # drill to the Queue/channel board (#965)
    assert "var-qmgr=$qmgr" in blob  # carrying the selected QM through


def test_work_qm_board_attention_is_empty_healthy_via_query_filter():
    dash = work_qm_dashboard()
    att = [
        p
        for p in dash["panels"]
        if p.get("type") == "table" and "attention" in p.get("title", "").lower()
    ]
    assert att, "expected a ③ Attention table panel"
    exprs = " ".join(t["expr"] for p in att for t in p["targets"])
    # empty = healthy is pushed into the PromQL: a healthy service is filtered OUT by the
    # comparison (so it produces no series and the row is absent), NOT hidden by row color
    assert "!= 2" in exprs  # the two service-status gauges (2 = running)
    assert "== 0" in exprs  # the listener count
    # the surviving rows name which service is down
    assert "Channel initiator" in exprs
    assert "Command server" in exprs
    assert "Listeners" in exprs
    # binds only qmgr-class object-status series (still QM-scoped, still contract-only)
    assert "$qmgr" in exprs
    assert "ibmmq_queue_" not in exprs and "ibmmq_channel_" not in exprs


def test_work_qm_board_es_feed_is_a_seam_not_a_wired_panel():
    dash = work_qm_dashboard()
    # ④ is a clearly-marked placeholder for Wave 1b / #966 (blocked on LogSearch)
    seams = [p for p in dash["panels"] if p.get("type") == "text" and "#966" in json.dumps(p)]
    assert seams, "expected an ES event/error-feed seam panel"
    # no Loki datasource is wired (work has no Loki; the feed is ES-only, built once in 1b)
    for ds in _all_datasources(dash):
        assert ds.get("type") != "loki", ds
    # and no ${loki} datasource variable is even declared (Wave 1a is metrics-only)
    assert "loki" not in {v["name"] for v in dash["templating"]["list"]}
