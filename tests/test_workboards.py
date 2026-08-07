"""Portable render mode + work-edition board helpers (#963, epic .github#169 Wave 1a).

These tests are the *portability contract* for the work-edition boards: the crux is
that a rendered board carries NO hardcoded datasource uid and NO lab-specific
QM/queue/channel name — its datasource is the ${datasource} template variable and its
objects are template variables ($qmgr, ...). The lab cockpits (clusterboard/qmboard)
stay lab-object-driven; this module layers portability on top of their primitives.
"""

from __future__ import annotations

import json

from mqlab.workboards import (
    WORK_BOARD_BUILDERS,
    datasource_var,
    portable_dashboard,
    qmgr_var,
    tmpl_var,
    work_dashboard_paths_and_texts,
    write_work_dashboards,
)

# Lab-specific names that must NEVER leak into a portable board (the anti-contract).
_LAB_NAMES = ("NHAUAPP", "NHARAPP", "SVCQM", "RDQMAPP", "APP.REPLY", "APP.SVRCONN", "PCMKAPP")


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
                {"refId": "A", "expr": 'ibmmq_qmgr_status{qmgr="NHAUAPP"}',
                 "datasource": {"type": "prometheus", "uid": "prometheus"}},
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
                    "targets": [{"refId": "A", "expr": '{host="x"}',
                                 "datasource": {"type": "loki", "uid": "loki"}}],
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
