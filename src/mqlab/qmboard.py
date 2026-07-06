"""Per-QM state board (#489) — one code-templated Grafana board per app queue manager,
answering "how is *this* QM doing?": the QM's own health (① a stat band), its critical
application queues (② a table), and its critical channels (③ a table). It joins the cockpit
family (clusterboard.py / messagingboard.py) as the single-QM *operational* view.

Everything comes from the already-scraped Prometheus `ibmmq_*` series (no new exporter,
collector, or REST path). The metric bindings were verified against a live exporter's
`/metrics` during the observe pass; the object-driven `or vector(-1)` wrapper makes any
that turn out sparse read as no-data rather than a false zero.

Object-driven, not metric-driven (the fleet convention, #178): the queues and channels we
care about are known up front (derived from the stack `short`, no QM literal hardcoded), so
each renders a tile/row at all times — a missing series reads as a coloured no-data, never a
vanished row. QM/queue/channel names derive from `short`: app QM = `<short>APP`, svc QM =
`<svc.short>QM`; only the fixed MQSC object names (APP.REPLY, APP.SVRCONN) are constants.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.clusterboard import _STALE_MAP, _ds, _row_header, _stat
from mqlab.messagingboard import _STATUS_MAP, _qm_status_expr
from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_UID_PREFIX = "lab-qm-"

# Fixed MQSC object names in the app<->svc flow (the QM names are per-stack, derived from
# `short`). APP.REPLY is where replies land; APP.SVRCONN is the app client's inbound channel.
_APP_REPLY = "APP.REPLY"
_APP_SVRCONN = "APP.SVRCONN"

_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# The value expr of one table cell, as a function of (qm, object). The table builder wraps
# each in `... or vector(-1)` + a label_replace that stamps the object name, so the curated
# row is present even when the series is absent.
CellExpr = Callable[[str, str], str]
# (column title, cell-expr builder, optional colour mapping for the value cell)
Column = tuple[str, CellExpr, list[dict[str, Any]] | None]

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


# ── ① QM header band ──────────────────────────────────────────────────────────


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
    # Early-warning gauge for a filling recovery log: restart / (restart + reusable) as a
    # percentage. Sparse-safe via the trailing sentinel.
    restart = f'max(ibmmq_qmgr_log_size_restart{{qmgr="{qm}"}})'
    reusable = f'max(ibmmq_qmgr_log_size_reusable{{qmgr="{qm}"}})'
    return f"100 * {restart} / ({restart} + {reusable}) or vector(-1)"


def _qm_band(ds_uid: str, app_qm: str, y: int) -> list[dict[str, Any]]:
    """A row of six equal stat tiles: status · uptime · connections · msg-rate (sparkline) ·
    services pill · recovery-log %. Every value expr is object-driven (`or vector(-1)`)."""
    # Each tile carries a title, its PromQL, an x-offset, an optional colour mapping, and an
    # optional value unit.
    specs: list[tuple[str, str, int, list[dict[str, Any]] | None, str | None]] = [
        ("Status", _qm_status_expr(app_qm), 0, _QM_STATUS_MAP, None),
        ("Uptime", _uptime_expr(app_qm), 4, None, "dtdurations"),
        ("Connections", _connections_expr(app_qm), 8, None, None),
        ("Msg rate", _msg_rate_expr(app_qm), 12, None, "short"),
        ("Services", _services_expr(app_qm), 16, _SERVICES_MAP, None),
        ("Recovery log %", _recovery_log_expr(app_qm), 20, None, "percent"),
    ]
    return [
        _stat(title, expr, ds_uid, x, y, mappings=mappings, unit=unit, w=4, h=4, value_size=22)
        for title, expr, x, mappings, unit in specs
    ]


# ── ②/③ The curated object tables ─────────────────────────────────────────────


def _q(metric: str) -> CellExpr:
    """A simple queue cell: `max(ibmmq_queue_<metric>{qmgr,queue})`."""
    return lambda qm, obj: f'max(ibmmq_queue_{metric}{{qmgr="{qm}",queue="{obj}"}})'


def _c(metric: str) -> CellExpr:
    """A simple channel cell: `max(ibmmq_channel_<metric>{qmgr,channel})`."""
    return lambda qm, obj: f'max(ibmmq_channel_{metric}{{qmgr="{qm}",channel="{obj}"}})'


def _q_pct_full(qm: str, obj: str) -> str:
    depth = f'max(ibmmq_queue_depth{{qmgr="{qm}",queue="{obj}"}})'
    max_depth = f'max(ibmmq_queue_attribute_max_depth{{qmgr="{qm}",queue="{obj}"}})'
    return f"100 * {depth} / {max_depth}"


def _q_put_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqput_mqput1_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _q_get_rate(qm: str, obj: str) -> str:
    return f'sum(rate(ibmmq_queue_mqget_count{{qmgr="{qm}",queue="{obj}"}}[1m]))'


def _queue_columns() -> list[Column]:
    return [
        ("Depth", _q("depth"), None),
        ("% full", _q_pct_full, None),
        ("Oldest age", _q("oldest_message_age"), None),
        ("Uncommitted", _q("uncommitted_messages"), None),
        ("In handles", _q("input_handles"), None),
        ("Out handles", _q("output_handles"), None),
        ("Put rate", _q_put_rate, None),
        ("Get rate", _q_get_rate, None),
        ("Since get", _q("time_since_get"), None),
    ]


def _channel_columns() -> list[Column]:
    return [
        ("Status", _c("status_squash"), _QM_STATUS_MAP),
        ("Substate", _c("substate"), None),
        ("Messages", _c("messages"), None),
        ("Bytes sent", _c("bytes_sent"), None),
        ("Bytes rcvd", _c("bytes_rcvd"), None),
        ("Batches", _c("batches"), None),
        ("Nettime", _c("nettime_short"), None),
        ("Since msg", _c("time_since_msg"), None),
        ("Cur inst", _c("cur_inst"), None),
    ]


def _cell(value_expr: str, label: str, obj: str) -> str:
    """Wrap one cell's value in the object-driven sentinel and stamp the object name so the
    curated row is present even when the series is absent."""
    return f'label_replace({value_expr} or vector(-1),"{label}","{obj}","","")'


def _object_table(
    ds_uid: str,
    title: str,
    qm: str,
    objects: list[str],
    label: str,
    columns: list[Column],
    y: int,
) -> dict[str, Any]:
    """A curated multi-column table: one row per object in `objects` (keyed by `label` =
    queue/channel), one column per `Column`. Each column is a single instant target unioning
    a per-object `label_replace(... or vector(-1))` cell, so every curated row renders at all
    times; a value cell with a `mapping` is colour-background-mapped (channel status)."""
    name_col = label.capitalize()
    targets: list[dict[str, Any]] = []
    rename: dict[str, str] = {label: name_col}
    overrides: list[dict[str, Any]] = []
    for i, (col_title, cell_expr, mapping) in enumerate(columns):
        ref = _REFIDS[i]
        expr = " or ".join(_cell(cell_expr(qm, obj), label, obj) for obj in objects)
        targets.append(
            {
                "refId": ref,
                "expr": expr,
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            }
        )
        rename[f"Value #{ref}"] = col_title
        if mapping is not None:
            overrides.append(
                {
                    "matcher": {"id": "byName", "options": col_title},
                    "properties": [
                        {
                            "id": "custom.cellOptions",
                            "value": {"type": "color-background", "mode": "basic"},
                        },
                        {"id": "mappings", "value": mapping},
                        {"id": "color", "value": {"mode": "fixed"}},
                    ],
                }
            )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": label, "mode": "outer"}},
            {
                "id": "organize",
                "options": {"renameByName": rename, "excludeByName": {"Time": True}},
            },
            {"id": "sortBy", "options": {"sort": [{"field": name_col}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}}, "overrides": overrides},
        "options": {"cellHeight": "sm"},
    }


def _queues_table(ds_uid: str, app_qm: str, svc_qm: str, y: int) -> dict[str, Any]:
    """② Critical queues: APP.REPLY (replies land here) and the svc XMITQ (named after the
    counterparty QM, USAGE(XMITQ) — the canonical request-outbound path)."""
    queues = [_APP_REPLY, svc_qm]
    return _object_table(
        ds_uid, f"{app_qm} — critical queues", app_qm, queues, "queue", _queue_columns(), y
    )


def _channels_table(ds_uid: str, app_qm: str, svc_qm: str, y: int) -> dict[str, Any]:
    """③ Critical channels: APP.SVRCONN (app client in), <app>.<svc> (SDR → svc),
    <svc>.<app> (RCVR ← svc)."""
    channels = [_APP_SVRCONN, f"{app_qm}.{svc_qm}", f"{svc_qm}.{app_qm}"]
    return _object_table(
        ds_uid, f"{app_qm} — critical channels", app_qm, channels, "channel", _channel_columns(), y
    )


def _title_banner(name: str, app_qm: str, y: int) -> dict[str, Any]:
    content = f"## Queue Manager · {name} · {app_qm} — health · critical queues · channels"
    return {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def render_qm_board(
    topo: dict[str, Any], name: str, cfg: dict[str, Any], ds_uid: str = "prometheus"
) -> dict[str, Any]:
    """Assemble one stack's app-QM board (pure — no I/O). `name` is the stack key; `cfg` is
    that stack's config (its `short` drives the QM/queue/channel names + the uid); `topo`
    supplies the shared svc identity (svc_qm = `<svc.short>QM`)."""
    short = cfg["short"]
    app_qm = f"{short}APP"
    svc_qm = f"{topo['svc']['short']}QM"
    panels = [
        _title_banner(name, app_qm, y=0),
        _row_header("① QM health — status · uptime · connections · rate · services · log", y=2),
        *_qm_band(ds_uid, app_qm, y=3),
        _row_header("② Critical queues — APP.REPLY · SVCQM XMITQ", y=7),
        _queues_table(ds_uid, app_qm, svc_qm, y=8),
        _row_header("③ Critical channels — SVRCONN · SDR · RCVR", y=16),
        _channels_table(ds_uid, app_qm, svc_qm, y=17),
    ]
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
