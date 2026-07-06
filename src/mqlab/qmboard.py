"""Per-QM state board (#489 → #521 v2) — one code-templated Grafana board per app queue
manager, answering "how is *this* QM doing, and *how is it trending*?": the QM's own
health (① a compact stat band + trend graphs), its critical application queues (② grouped
time-series blocks), and its critical channels (③ grouped time-series blocks).

Where the #489 board rendered static stat tiles + instant object tables, this v2 renders the
same live-verified metric bindings as **time-series trend graphs** — the point is to *see the
derivative*: put-rate vs get-rate on one graph is the depth derivative; in-handles vs
out-handles on one graph is who is attached; the channel status over time shows *when* it
dropped. Related series share a graph on purpose (the relationship is the signal).

Everything comes from the already-scraped Prometheus `ibmmq_*` series (no new exporter,
collector, or REST path); the metric bindings are the ones verified against a live exporter's
`/metrics` during the observe pass. Status tiles keep the object-driven `or vector(-1)`
sentinel so a null series reads no-data, never a false healthy; a trend graph with no series
simply renders empty, which is the honest no-data for a trend.

Object-driven, not metric-driven (the fleet convention, #178): the queues and channels we
care about are known up front (derived from the stack `short`, no QM literal hardcoded), so
each gets its own labelled block at all times. QM/queue/channel names derive from `short`:
app QM = `<short>APP`, svc QM = `<svc.short>QM`; only the fixed MQSC object names (APP.REPLY,
APP.SVRCONN) are constants.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.clusterboard import (
    _STALE_MAP,
    _STATUS_MAP,
    _logs_panel,
    _qm_status_expr,
    _row_header,
    _stat,
    _t,
    _timeseries,
)
from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_UID_PREFIX = "lab-qm-"

# Fixed MQSC object names in the app<->svc flow (the QM names are per-stack, derived from
# `short`). APP.REPLY is where replies land; APP.SVRCONN is the app client's inbound channel.
_APP_REPLY = "APP.REPLY"
_APP_SVRCONN = "APP.SVRCONN"

# QM status / channel status share the messaging board's -1/0/1/2 coloured family; every
# status tile additionally carries STALE so a truly-null series never reads healthy.
_QM_STATUS_MAP: list[dict[str, Any]] = [*_STATUS_MAP, _STALE_MAP]

# The services pill folds the plumbing into one signal: 1 (all up) / 0 (something down) /
# -1 (no data). Coloured, with STALE for a null read.
_SERVICES_MAP: list[dict[str, Any]] = [
    {
        "type": "value",
        "options": {
            "-1": {"text": "No data", "color": "grey", "index": 0},
            "0": {"text": "⚠ issue", "color": "red", "index": 1},
            "1": {"text": "✓ up", "color": "green", "index": 2},
        },
    },
    _STALE_MAP,
]


def qm_board_uid(short: str) -> str:
    """The pinned per-QM board uid: `lab-qm-<short>` (lowercased)."""
    return f"{DASHBOARD_UID_PREFIX}{short.lower()}"


# ── ① QM header band — compact status pills + trend graphs ────────────────────


def _uptime_expr(qm: str) -> str:
    return f'max(ibmmq_qmgr_uptime{{qmgr="{qm}"}}) or vector(-1)'


def _connections_expr(qm: str) -> str:
    return f'max(ibmmq_qmgr_connection_count{{qmgr="{qm}"}}) or vector(-1)'


def _msg_rate_expr(qm: str) -> str:
    # Mirror dashboard.py:_qm_rate_panel — interval puts+put1s and destructive gets — plus
    # the object-driven sentinel so an idle/absent QM reads no-data, not a false zero.
    return (
        f'rate(ibmmq_qmgr_interval_mqput_mqput1_total_count{{qmgr="{qm}"}}[1m]) '
        f'+ rate(ibmmq_qmgr_interval_destructive_get_total_count{{qmgr="{qm}"}}[1m]) '
        "or vector(-1)"
    )


def _services_expr(qm: str) -> str:
    # The plumbing pill: channel initiator running (==2) AND command server running (==2)
    # AND at least one active listener. max() strips labels so the three multiply as plain
    # 0/1 vectors; absent series → empty product → vector(-1) (no data), never a false green.
    initiator = f'(max(ibmmq_qmgr_channel_initiator_status{{qmgr="{qm}"}}) == bool 2)'
    cmd_server = f'(max(ibmmq_qmgr_command_server_status{{qmgr="{qm}"}}) == bool 2)'
    listener = f'(max(ibmmq_qmgr_active_listeners{{qmgr="{qm}"}}) > bool 0)'
    return f"{initiator} * {cmd_server} * {listener} or vector(-1)"


def _recovery_log_expr(qm: str) -> str:
    # Early-warning trend for a filling recovery log: restart / (restart + reusable) as a
    # percentage. Sparse-safe via the trailing sentinel.
    restart = f'max(ibmmq_qmgr_log_size_restart{{qmgr="{qm}"}})'
    reusable = f'max(ibmmq_qmgr_log_size_reusable{{qmgr="{qm}"}})'
    return f"100 * {restart} / ({restart} + {reusable}) or vector(-1)"


def _qm_band(ds_uid: str, app_qm: str, y: int) -> list[dict[str, Any]]:
    """① QM health: a compact top row of three status pills (Status · Uptime · Services),
    then a row of three trend graphs (Connections · Msg rate · Recovery-log %). Status stays
    categorical (a stat pill); the counts/rates that tell a story become timeseries."""
    pills = [
        _stat(
            "Status",
            _qm_status_expr(app_qm),
            ds_uid,
            0,
            y,
            mappings=_QM_STATUS_MAP,
            w=8,
            h=3,
            value_size=22,
        ),
        _stat(
            "Uptime",
            _uptime_expr(app_qm),
            ds_uid,
            8,
            y,
            unit="dtdurations",
            w=8,
            h=3,
            value_size=22,
        ),
        _stat(
            "Services",
            _services_expr(app_qm),
            ds_uid,
            16,
            y,
            mappings=_SERVICES_MAP,
            w=8,
            h=3,
            value_size=22,
        ),
    ]
    ty = y + 3
    trends = [
        _timeseries(
            "Connections",
            [_t("A", _connections_expr(app_qm), "connections")],
            ds_uid,
            0,
            ty,
            w=8,
        ),
        _timeseries(
            "Msg rate (put+get / s)",
            [_t("A", _msg_rate_expr(app_qm), "put+get / s")],
            ds_uid,
            8,
            ty,
            w=8,
            unit="short",
        ),
        _timeseries(
            "Recovery log %",
            [_t("A", _recovery_log_expr(app_qm), "log used")],
            ds_uid,
            16,
            ty,
            w=8,
            unit="percent",
        ),
    ]
    return [*pills, *trends]


# ── ② queue trend blocks / ③ channel trend blocks ────────────────────────────


def _q_series(metric: str, qm: str, obj: str) -> str:
    """A queue series: `max(ibmmq_queue_<metric>{qmgr,queue})` (no sentinel — an empty
    trend is the honest no-data for a graph)."""
    return f'max(ibmmq_queue_{metric}{{qmgr="{qm}",queue="{obj}"}})'


def _c_series(metric: str, qm: str, obj: str) -> str:
    """A channel gauge series: `max(ibmmq_channel_<metric>{qmgr,channel})` (nettime, status)."""
    return f'max(ibmmq_channel_{metric}{{qmgr="{qm}",channel="{obj}"}})'


def _c_rate(metric: str, qm: str, obj: str) -> str:
    """A channel COUNTER as a per-second rate (messages/bytes are `# TYPE counter`, so a raw
    plot is a meaningless ever-climbing line — the throughput trend is the rate)."""
    return f'sum(rate(ibmmq_channel_{metric}{{qmgr="{qm}",channel="{obj}"}}[1m]))'


def _q_put_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqput_mqput1_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _q_get_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqget_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _channel_status_series(qm: str, obj: str) -> str:
    # The channel status timeline keeps the sentinel: a dropped/absent channel must read
    # -1 (No status), not an empty gap, so you can see *when* it went down.
    return f"{_c_series('status_squash', qm, obj)} or vector(-1)"


def _queue_block(ds_uid: str, qm: str, queue: str, y: int) -> list[dict[str, Any]]:
    """One queue's block: a labelled row header + four trend graphs in a 2×2 grid —
    Depth (with max-depth reference) · Flow (put vs get, the depth derivative) · Handles
    (in vs out) · Age & in-flight (oldest-age + uncommitted). Related series share a graph."""
    header = _row_header(f"② Queue · {queue} — depth · flow · handles · age", y)
    py = y + 1
    panels = [
        _timeseries(
            f"{queue} — depth",
            [
                _t("A", _q_series("depth", qm, queue), "depth"),
                _t("B", _q_series("attribute_max_depth", qm, queue), "max depth"),
            ],
            ds_uid,
            0,
            py,
            w=12,
        ),
        _timeseries(
            f"{queue} — flow (put vs get / s)",
            [
                _t("A", _q_put_rate(qm, queue), "put rate"),
                _t("B", _q_get_rate(qm, queue), "get rate"),
            ],
            ds_uid,
            12,
            py,
            w=12,
            unit="ops",
        ),
        _timeseries(
            f"{queue} — handles (in vs out)",
            [
                _t("A", _q_series("input_handles", qm, queue), "input"),
                _t("B", _q_series("output_handles", qm, queue), "output"),
            ],
            ds_uid,
            0,
            py + 7,
            w=12,
        ),
        _timeseries(
            f"{queue} — age & in-flight",
            [
                _t("A", _q_series("oldest_message_age", qm, queue), "oldest age"),
                _t("B", _q_series("uncommitted_messages", qm, queue), "uncommitted"),
            ],
            ds_uid,
            12,
            py + 7,
            w=12,
        ),
    ]
    return [header, *panels]


def _channel_block(ds_uid: str, qm: str, channel: str, role: str, y: int) -> list[dict[str, Any]]:
    """One channel's block: a labelled row header + three trend graphs — Throughput
    (messages + bytes sent/rcvd) · Nettime (round-trip latency) · Status (the squash code
    over time, so a drop is visible in the timeline)."""
    header = _row_header(f"③ Channel · {channel} ({role}) — throughput · nettime · status", y)
    py = y + 1
    panels = [
        _timeseries(
            f"{channel} — throughput (per-sec)",
            [
                _t("A", _c_rate("messages", qm, channel), "messages/s"),
                _t("B", _c_rate("bytes_sent", qm, channel), "bytes sent/s"),
                _t("C", _c_rate("bytes_rcvd", qm, channel), "bytes rcvd/s"),
            ],
            ds_uid,
            0,
            py,
            w=8,
        ),
        _timeseries(
            f"{channel} — nettime",
            [_t("A", _c_series("nettime_short", qm, channel), "nettime")],
            ds_uid,
            8,
            py,
            w=8,
            unit="µs",
        ),
        _timeseries(
            f"{channel} — status",
            [_t("A", _channel_status_series(qm, channel), "status")],
            ds_uid,
            16,
            py,
            w=8,
        ),
    ]
    return [header, *panels]


def _title_banner(name: str, app_qm: str, y: int) -> dict[str, Any]:
    content = f"## Queue Manager · {name} · {app_qm} — health · critical queues · channels (trends)"
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def _events_panel(loki_uid: str, title: str, filter_clause: str, y: int) -> dict[str, Any]:
    """A per-object MQ instrumentation-events panel (#526). The mq-event-monitor collector
    (#515) drains the QM's SYSTEM.ADMIN.*.EVENT queues to JSON tagged mq-events — a separate
    Loki stream (unit=mq-events) from the diagnostic logs. `| json` flattens the event; the
    affected object is eventSource.objectName (→ eventSource_objectName) and the QM is
    eventData.queueMgrName. Each panel scopes to one object so its events sit inline with the
    metric graphs for that same object (for the QM: start/stop, config, and the CMDEV admin
    commands worth watching during a live triage)."""
    sel = f'{{unit="mq-events"}} | json | {filter_clause}'
    return _logs_panel(title, sel, loki_uid, y)


def render_qm_board(
    topo: dict[str, Any],
    name: str,
    cfg: dict[str, Any],
    ds_uid: str = "prometheus",
    loki_uid: str = "loki",
) -> dict[str, Any]:
    """Assemble one stack's app-QM board (pure — no I/O). `name` is the stack key; `cfg` is
    that stack's config (its `short` drives the QM/queue/channel names + the uid); `topo`
    supplies the shared svc identity (svc_qm = `<svc.short>QM`)."""
    short = cfg["short"]
    app_qm = f"{short}APP"
    svc_qm = f"{topo['svc']['short']}QM"

    panels: list[dict[str, Any]] = [_title_banner(name, app_qm, y=0)]
    y = 2
    panels.append(
        _row_header("① QM health — status · uptime · services · connections · rate · log", y)
    )
    y += 1
    panels.extend(_qm_band(ds_uid, app_qm, y))
    y += 10  # 3 pills (h=3) over 3 trend graphs (h=7)
    # ① QM-level events: everything this QM emits (start/stop, config, and the CMDEV admin
    # commands) — scoped by eventData.queueMgrName (#526).
    panels.append(
        _events_panel(loki_uid, f"▤ {app_qm} — all QM events", f'eventData_queueMgrName="{app_qm}"', y)
    )
    y += 8  # logs panel (h=8)

    # ② Critical queues: APP.REPLY (replies land here) and the svc XMITQ (named after the
    # counterparty QM, USAGE(XMITQ) — the canonical request-outbound path). One block each,
    # each followed by that queue's own events panel (#526).
    for queue in (_APP_REPLY, svc_qm):
        panels.extend(_queue_block(ds_uid, app_qm, queue, y))
        y += 15  # header (1) + two rows of graphs (7 + 7)
        panels.append(
            _events_panel(loki_uid, f"▤ {queue} — events", f'eventSource_objectName="{queue}"', y)
        )
        y += 8

    # ③ Critical channels: APP.SVRCONN (app client in), <app>.<svc> (SDR → svc),
    # <svc>.<app> (RCVR ← svc). One block each, each followed by that channel's events (#526).
    channels = [
        (_APP_SVRCONN, "SVRCONN"),
        (f"{app_qm}.{svc_qm}", "SDR"),
        (f"{svc_qm}.{app_qm}", "RCVR"),
    ]
    for channel, role in channels:
        panels.extend(_channel_block(ds_uid, app_qm, channel, role, y))
        y += 8  # header (1) + one row of graphs (7)
        panels.append(
            _events_panel(loki_uid, f"▤ {channel} — events", f'eventSource_objectName="{channel}"', y)
        )
        y += 8

    return {
        "uid": qm_board_uid(short),
        "title": f"Queue Manager · {app_qm}",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", "qm", name],
    }


# ── render + write entry (mirrors messagingboard.write_messaging_dashboards) ────


def _lab_topology() -> dict[str, Any]:
    """The real lab topology dict (single source for both the svc identity and the stack
    list, so a monkeypatched topology drives the whole render in one place)."""
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def qm_dashboard_path(short: str) -> Path:
    """Where a QM's rendered board is written (gitignored build/work tree)."""
    return work("grafana", "dashboards", f"{qm_board_uid(short)}.json")


def qm_dashboard_paths_and_texts() -> list[tuple[Path, str]]:
    """Render every provisioned app QM's board from the real topology; return (path, text)
    pairs without touching the filesystem (the pure seam the smoke test drives)."""
    topo = _lab_topology()
    out: list[tuple[Path, str]] = []
    for name, cfg in (topo.get("stacks") or {}).items():
        cfg = cfg or {}
        if not (cfg.get("provision") and cfg.get("short")):
            continue
        board = render_qm_board(topo, name, cfg)
        text = json.dumps(board, indent=2) + "\n"
        out.append((qm_dashboard_path(cfg["short"]), text))
    return out


def write_qm_dashboards() -> list[Path]:
    """Render + write every provisioned app QM's board (beside the cockpits under
    build/work); return the written paths. One call from the cli render sites."""
    paths: list[Path] = []
    for path, text in qm_dashboard_paths_and_texts():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(path)
    return paths
