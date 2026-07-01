"""Messaging-layer cockpit (#431) — one board per stack of the MQ flow + the live
application round-trip. The messaging-layer peer of the cluster cockpits
(clusterboard.py): those show the infrastructure *under* MQ; this shows whether
messaging is actually *flowing*.

Flow-oriented (epic .github#15 §5): title banner · ① status band (flow indicators)
· ② the app<->svc message-flow strip · ⟳ round-trip timeline (from the #429
app_roundtrip_* signal) · ▤ round-trip logs · queues/channels tables with the
drill-down data-link seam to the future per-QM/channel/queue detail boards.

It imports the clusterboard primitives (one toolkit, two layers); QM names derive
from each stack's #351 short — no QM literal is hardcoded here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mqlab.clusterboard import (
    _ds,
    _log_level_var,
    _logs_panel,
    _row_header,
    _stat,
    _t,
    _timeseries,
)
from mqlab.paths import work
from mqlab.stacks import lab_stacks

if TYPE_CHECKING:
    from pathlib import Path

    from mqlab.stacks import Stack

# MQ channel/QM status squash: -1 no-status(grey) · 0 stopped(red) · 1
# transitioning(yellow) · 2 running(green). Same coding dashboard.py uses.
_STATUS_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No status", "color": "grey", "index": 0},
            "0": {"text": "Stopped", "color": "red", "index": 1},
            "1": {"text": "Transitioning", "color": "yellow", "index": 2},
            "2": {"text": "Running", "color": "green", "index": 3},
        },
    },
]

# Lab-constant object names in the app<->svc flow (the QM names are per-stack).
_APP_SVRCONN = "APP.SVRCONN"
_SVC_SVRCONN = "SVC.SVRCONN"
_APP_REPLY = "APP.REPLY"
_SVC_REQUEST = "SVC.REQUEST"

# Exporter metadata columns stripped from the queue/channel tables (name + value kept).
_TABLE_DROP_COLS = [
    "Time",
    "qmgr",
    "job",
    "instance",
    "__name__",
    "cluster",
    "description",
    "platform",
    "usage",
    "host",
    "type",
]

# The app round-trip signal (#429) is emitted by app-client's node-exporter, not
# qmgr-keyed — one stream (the active stack's requester). Shared by all boards.
_RT_TOTAL = "app_roundtrip_total"
_RT_FAIL = "app_roundtrip_failures_total"
_RT_BUCKET = "app_roundtrip_latency_ms_bucket"


def _qm_status_expr(qm: str) -> str:
    return f'max(ibmmq_qmgr_status{{qmgr="{qm}"}}) or vector(-1)'


def _channel_status_expr(qm: str, channel: str) -> str:
    return f'max(ibmmq_channel_status_squash{{qmgr="{qm}",channel="{channel}"}}) or vector(-1)'


def _queue_depth_expr(qm: str, queue: str) -> str:
    return f'max(ibmmq_queue_depth{{qmgr="{qm}",queue="{queue}"}}) or vector(-1)'


def _status_band(ds_uid: str, app_qm: str, svc_qm: str, y: int) -> list[dict[str, Any]]:
    """① Flow indicators (not depths): App/SVC QM up · round-trip success % · message
    rate · failure rate. Success% reads No data when idle (no `or vector(100)` fallback —
    a fake 100% on no traffic is misleading)."""
    success = f"100 * (1 - (sum(rate({_RT_FAIL}[5m])) / sum(rate({_RT_TOTAL}[5m]))))"
    # Each tile carries: a title, its PromQL, its x-offset and width, an optional
    # colour mapping, and an optional value unit.
    specs: list[tuple[str, str, int, int, list[dict[str, Any]] | None, str | None]] = [
        ("App QM", _qm_status_expr(app_qm), 0, 5, _STATUS_MAP, None),
        ("SVC QM", _qm_status_expr(svc_qm), 5, 5, _STATUS_MAP, None),
        ("Round-trip OK %", success, 10, 5, None, "percent"),
        ("Message rate", f"sum(rate({_RT_TOTAL}[1m]))", 15, 5, None, "reqps"),
        ("Failure rate", f"sum(rate({_RT_FAIL}[1m]))", 20, 4, None, "reqps"),
    ]
    return [
        _stat(t, e, ds_uid, x, y, mappings=m, unit=u, w=w, h=3, value_size=22)
        for t, e, x, w, m, u in specs
    ]


def _flow_strip(ds_uid: str, app_qm: str, svc_qm: str, y: int) -> list[dict[str, Any]]:
    """② The message path as a row of positioned tiles (not a custom viz, so it stays
    generic): app-client → APP.SVRCONN → [APP QM] → SDR → [SVC QM] → SVC.SVRCONN →
    svc-sim, with the APP.REPLY return leg. Each hop colours on its own metric."""
    sdr = f"{app_qm}.{svc_qm}"  # our-side sender to the SVC counterparty
    w = 3
    tiles = [
        ("app-client", f"sum(rate({_RT_TOTAL}[1m]))", "reqps", None),
        (_APP_SVRCONN, _channel_status_expr(app_qm, _APP_SVRCONN), None, _STATUS_MAP),
        (f"{app_qm} (app)", _qm_status_expr(app_qm), None, _STATUS_MAP),
        (sdr, _channel_status_expr(app_qm, sdr), None, _STATUS_MAP),
        (f"{svc_qm} (svc)", _qm_status_expr(svc_qm), None, _STATUS_MAP),
        (_SVC_SVRCONN, _channel_status_expr(svc_qm, _SVC_SVRCONN), None, _STATUS_MAP),
        ("svc-sim: " + _SVC_REQUEST, _queue_depth_expr(svc_qm, _SVC_REQUEST), None, None),
        (_APP_REPLY, _queue_depth_expr(app_qm, _APP_REPLY), None, None),
    ]
    return [
        _stat(title, expr, ds_uid, i * w, y, mappings=mapping, unit=unit, w=w, value_size=18)
        for i, (title, expr, unit, mapping) in enumerate(tiles)
    ]


def _roundtrip_timeline(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """⟳ The signature panel: throughput + failure rate over time (the failover-timeline
    analog) beside the latency distribution (p50/p95 from the #429 histogram)."""
    rate = _timeseries(
        "Round-trip rate & failures",
        [
            _t("A", f"sum(rate({_RT_TOTAL}[1m]))", "throughput"),
            _t("B", f"sum(rate({_RT_FAIL}[1m]))", "failures"),
        ],
        ds_uid,
        0,
        y,
        w=12,
        unit="reqps",
    )
    latency = _timeseries(
        "Round-trip latency (p50 · p95)",
        [
            _t("A", f"histogram_quantile(0.5, sum by (le)(rate({_RT_BUCKET}[5m])))", "p50"),
            _t("B", f"histogram_quantile(0.95, sum by (le)(rate({_RT_BUCKET}[5m])))", "p95"),
        ],
        ds_uid,
        12,
        y,
        w=12,
        unit="ms",
    )
    return [rate, latency]


def _object_table(
    ds_uid: str,
    title: str,
    qm: str,
    series: str,
    label: str,
    value_name: str,
    y: int,
    x: int,
    w: int,
    *,
    drill_uid: str,
    drill_var: str,
    mappings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A per-object table (rows = queues or channels of one QM), keyed by `label`. The
    name column carries a Grafana data link to a future detail dashboard (the drill-down
    seam, #15 ④–⑥) — the target UID is a stub until those boards exist. `mappings` colours
    the value cell (channel status); queues leave it a plain number."""
    name_col = label.capitalize()
    drill_url = f"/d/{drill_uid}?var-{drill_var}=${{__value.text}}&var-qmgr={qm}"
    overrides: list[dict[str, Any]] = []
    if mappings is not None:
        overrides.append(
            {
                "matcher": {"id": "byName", "options": value_name},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "basic"},
                    },
                    {"id": "mappings", "value": mappings},
                    {"id": "color", "value": {"mode": "fixed"}},
                ],
            }
        )
    overrides.append(
        {
            "matcher": {"id": "byName", "options": name_col},
            "properties": [
                {
                    "id": "links",
                    "value": [{"title": f"Drill into this {label}", "url": drill_url}],
                }
            ],
        }
    )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 8, "w": w, "x": x, "y": y},
        "targets": [
            {
                "refId": "A",
                "expr": f'{series}{{qmgr="{qm}"}}',
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            }
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": dict.fromkeys(_TABLE_DROP_COLS, True),
                    "renameByName": {label: name_col, "Value": value_name},
                },
            },
            {"id": "sortBy", "options": {"sort": [{"field": name_col}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}}, "overrides": overrides},
        "options": {"cellHeight": "sm"},
    }


def _title_banner(stack_name: str, app_qm: str, svc_qm: str, y: int) -> dict[str, Any]:
    content = f"## Messaging Layer · {stack_name} · app-client ⇄ {app_qm} ⇄ {svc_qm} ⇄ svc-sim"
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def render_messaging_board(
    stack_name: str, app_qm: str, svc_qm: str, ds_uid: str = "prometheus", loki_uid: str = "loki"
) -> dict[str, Any]:
    """Assemble the flow-oriented messaging board for one stack."""
    panels = [
        _title_banner(stack_name, app_qm, svc_qm, y=0),
        _row_header("① Messaging status — QMs · success · rate · failures", y=2),
        *_status_band(ds_uid, app_qm, svc_qm, y=3),
        _row_header("② The message flow — app-client ⇄ SVC", y=6),
        *_flow_strip(ds_uid, app_qm, svc_qm, y=7),
        _row_header("⟳ Round-trip — throughput · failures · latency", y=11),
        *_roundtrip_timeline(ds_uid, y=12),
        _row_header("▤ Round-trip logs", y=19),
        _logs_panel(
            "▤ Round-trip logs (app + svc, severity: $level)",
            # Loki =~ is fully anchored and the journald unit label carries the .service
            # suffix, so the names must be wildcarded to match mq-app-requester.service etc.
            '{unit=~"mq-app-requester.*|mq-svc-responder.*"} |~ `${level}`',
            loki_uid,
            y=20,
        ),
        _row_header("▦ Queues & channels", y=28),
        _object_table(
            ds_uid,
            f"{app_qm} — queues",
            app_qm,
            "ibmmq_queue_depth",
            "queue",
            "Depth",
            y=29,
            x=0,
            w=12,
            drill_uid="lab-messaging-queue",
            drill_var="queue",
        ),
        _object_table(
            ds_uid,
            f"{app_qm} — channels",
            app_qm,
            "ibmmq_channel_status_squash",
            "channel",
            "Status",
            y=29,
            x=12,
            w=12,
            mappings=_STATUS_MAP,
            drill_uid="lab-messaging-channel",
            drill_var="channel",
        ),
    ]
    return {
        "uid": f"lab-messaging-{stack_name}",
        "title": f"Messaging Layer · {stack_name}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "templating": {"list": [_log_level_var()]},
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", "messaging", stack_name],
    }


def _messaging_stacks() -> list[Stack]:
    """The stacks that get a messaging board — every provisioned stack (has a short)."""
    return [s for s in lab_stacks().values() if s.short and s.provision]


def messaging_dashboard_path(stack_name: str) -> Path:
    """Where a stack's rendered messaging board is written (gitignored)."""
    return work("grafana", "dashboards", f"lab-messaging-{stack_name}.json")


def write_messaging_dashboards() -> list[Path]:
    """Render + write every provisioned stack's messaging board (beside the cluster
    cockpits under build/work); return the written paths. One call from the cli render
    sites, so the per-stack loop lives (and is tested) here, not inline in cli."""
    paths: list[Path] = []
    for stack in _messaging_stacks():
        board = render_messaging_board(stack.name, stack.qm.qm_app, stack.qm.qm_svc)
        path = messaging_dashboard_path(stack.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(board, indent=2) + "\n")
        paths.append(path)
    return paths
