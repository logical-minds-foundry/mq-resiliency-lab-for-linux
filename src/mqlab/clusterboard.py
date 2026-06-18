"""Pure builders for the cluster-cockpit board (lab-pcmk-cluster). Data in → Grafana
panel/dashboard dicts out, no I/O — mirrors dashboard.py. Engine = Table (spike §4.1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

Column = tuple[str, str, str]  # (title, promql, mapping_kind)

_GREEN, _RED = "green", "red"
_MAPPINGS: dict[str, list[dict[str, Any]]] = {
    # 1 → green, 0 → red (daemon up, node online)
    "up": [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "down", "index": 0},
                "1": {"color": _GREEN, "text": "up", "index": 1},
            },
        },
    ],
    # 0 → green, ≥1 → red (fence count, unclean)
    "clean0": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "!", "index": 1},
            },
        },
    ],
    # ≥1 → green, 0 → red (iSCSI sessions, resync %)
    "sessions": [
        {"type": "value", "options": {"0": {"color": _RED, "text": "none", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _GREEN, "text": "ok", "index": 1},
            },
        },
    ],
}
_REFIDS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _ds(uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": uid}


def matrix(title: str, columns: list[Column], ds_uid: str, y: int) -> dict[str, Any]:
    """A node×component Table panel: one normalized query per column, joined on `n`,
    with per-column colour-background cell mappings. Rows are data-driven."""
    targets: list[dict[str, Any]] = []
    rename: dict[str, str] = {"n": "node"}
    overrides: list[dict[str, Any]] = []
    for i, (col_title, expr, kind) in enumerate(columns):
        ref = _REFIDS[i]
        targets.append(
            {
                "refId": ref,
                "expr": expr,
                "format": "table",
                "instant": True,
                "datasource": _ds(ds_uid),
            },
        )
        rename[f"Value #{ref}"] = col_title
        overrides.append(
            {
                "matcher": {"id": "byName", "options": col_title},
                "properties": [
                    {
                        "id": "custom.cellOptions",
                        "value": {"type": "color-background", "mode": "basic"},
                    },
                    {"id": "mappings", "value": _MAPPINGS[kind]},
                    {"id": "color", "value": {"mode": "fixed"}},
                ],
            },
        )
    return {
        "type": "table",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 9, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "n", "mode": "outer"}},
            {
                "id": "organize",
                "options": {"renameByName": rename, "excludeByName": {"Time": True}},
            },
            # sort rows by node so site A (a1/a2/a3) groups before site B (b1/b2/b3)
            {"id": "sortBy", "options": {"sort": [{"field": "node"}]}},
        ],
        "fieldConfig": {
            "defaults": {"custom": {"align": "center"}},
            "overrides": overrides,
        },
        "options": {"cellHeight": "sm"},
    }


_PRECEDENCE = ("STALE", "red", "amber", "green")


def fold_side(cells: list[str]) -> str:
    """Fold a side's cell states to one signal, worst-wins with STALE first
    (precedence STALE > red > amber > green). Empty or unrecognized → STALE (fail-loud:
    a blind/untrustworthy side must never read as healthy)."""
    for level in _PRECEDENCE:
        if level in cells:
            return level
    return "STALE"


def active_side(owner_sites: list[str]) -> str:
    """The active site from the set of sites currently holding mq_* owners:
    one site → "A"/"B"; none → "none" (mid-transition); two → "split" (hazard)."""
    sites = set(owner_sites)
    if len(sites) > 1:
        return "split"
    if not sites:
        return "none"
    return sites.pop()


def _norm(series: str, label: str) -> str:
    """The row-key-normalized per-column query: collapse node|member|holder → `n`."""
    return f'max by (n)(label_replace({series},"n","$1","{label}","(.*)"))'


_COMPUTE_COLS: list[Column] = [
    ("corosync", _norm('cluster_daemon_up{unit="corosync"}', "node"), "up"),
    ("pacemaker", _norm('cluster_daemon_up{unit="pacemaker"}', "node"), "up"),
    ("iSCSI", _norm("cluster_iscsi_sessions", "node"), "sessions"),
    ("fence", _norm("cluster_fence_count", "member"), "clean0"),
    ("online", _norm("cluster_node_online", "member"), "up"),
    ("unclean", _norm("cluster_node_unclean", "member"), "clean0"),
]
_STORAGE_COLS: list[Column] = [
    ("resync %", _norm("cluster_drbd_resync_pct", "node"), "sessions"),
    ("out-of-sync", _norm("cluster_drbd_out_of_sync_bytes", "node"), "clean0"),
]


def _stat(
    title: str,
    expr: str,
    ds_uid: str,
    x: int,
    y: int,
    *,
    mappings: list[dict[str, Any]] | None = None,
    unit: str | None = None,
    text_mode: str = "value",
) -> dict[str, Any]:
    """A single Stat tile with a sparkline (graphMode=area)."""
    defaults: dict[str, Any] = {"mappings": mappings or []}
    if unit is not None:
        defaults["unit"] = unit
    target: dict[str, Any] = {
        "refId": "A",
        "expr": expr,
        "instant": True,
        "datasource": _ds(ds_uid),
    }
    if text_mode == "name":
        target["legendFormat"] = "{{holder}}"
    return {
        "type": "stat",
        "title": title,
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 4, "w": 6, "x": x, "y": y},
        "targets": [target],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "graphMode": "area",
            "textMode": text_mode,
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
    }


_STALE_MAP = {
    "type": "special",
    "options": {"match": "null", "result": {"color": "text", "text": "STALE", "index": 9}},
}


def hero_tiles(ds_uid: str, y: int) -> list[dict[str, Any]]:
    """The top band: cluster health, nodes online, active QM owner, replication backlog.
    No-data reads STALE, never healthy (fail-loud)."""
    online = "max by (member)(cluster_node_online)"
    health_maps = [
        {
            "type": "value",
            "options": {
                "0": {"color": _RED, "text": "DOWN", "index": 0},
                "1": {"color": _GREEN, "text": "✓ healthy", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    return [
        _stat("Cluster health", f"min({online})", ds_uid, 0, y, mappings=health_maps),
        _stat("Nodes online", f"sum({online})", ds_uid, 6, y),
        _stat(
            # max by (holder) collapses the per-reporter series → one tile, not one per node
            "Active QM owner",
            'max by (holder)(cluster_resource_owner{resource="mq_qm"})',
            ds_uid,
            12,
            y,
            text_mode="name",
        ),
        _stat(
            "Replication backlog",
            "max(cluster_drbd_out_of_sync_bytes)",
            ds_uid,
            18,
            y,
            unit="bytes",
        ),
    ]


def integrity_panel(ds_uid: str, y: int) -> dict[str, Any]:
    """First-class integrity light: hazard count (split-brain/dual-primary/Diskless),
    gated on DRBD being present so no-data reads STALE (not a false green)."""
    hazards = (
        '(count(cluster_drbd_conn{conn="StandAlone"}) or vector(0))'
        ' + (count(cluster_drbd_disk{disk="Diskless"}) or vector(0))'
        ' + (count(cluster_drbd_role{role="Primary"}) > bool 1)'
    )
    expr = f"({hazards}) and on() (count(cluster_drbd_role) > 0)"
    maps = [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "✓ integrity", "index": 0}}},
        {
            "type": "range",
            "options": {
                "from": 1,
                "to": 9999,
                "result": {"color": _RED, "text": "⚠ HAZARD", "index": 1},
            },
        },
        _STALE_MAP,
    ]
    panel = _stat("Integrity", expr, ds_uid, 0, y, mappings=maps)
    panel["gridPos"]["w"] = 24  # full-width banner
    return panel


_TIMELINE_SIGNALS = [
    ("nodes online", "sum(max by (member)(cluster_node_online))"),
    ("QM running", 'max(cluster_resource_started{resource="mq_qm"})'),
    ("DRBD primary", 'count(cluster_drbd_role{role="Primary"})'),
    ("quorate", "min(cluster_quorate)"),
]
# Holder-agnostic owner-change marker: cluster_resource_owner carries the holder in a
# label, so an owner change spawns a NEW series — `changes()` on it won't fire. Count
# owners per resource and watch THAT change instead (§6.4).
_OWNER_CHANGE_EXPR = "changes((count by (resource)(cluster_resource_owner))[5m:])"


def timeline_band(ds_uid: str, y: int) -> dict[str, Any]:
    """The failover-story band: a State-timeline of the key signals over the drill window.
    The matrix is *now*; this shows the cluster moving through a cutover."""
    targets = [
        {
            "refId": _REFIDS[i],
            "expr": expr,
            "range": True,
            "legendFormat": name,
            "datasource": _ds(ds_uid),
        }
        for i, (name, expr) in enumerate(_TIMELINE_SIGNALS)
    ]
    return {
        "type": "state-timeline",
        "title": "⟳ Failover timeline",
        "datasource": _ds(ds_uid),
        "gridPos": {"h": 7, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "fieldConfig": {"defaults": {"custom": {"fillOpacity": 80}}, "overrides": []},
        "options": {"mergeValues": True, "showValue": "auto"},
    }


def log_row(loki_uid: str, y: int) -> dict[str, Any]:
    """The embedded live log row: cluster-node journald units, severity-filtered (WARN+).
    The matrix shows *what* changed; this shows *why*, on one screen (§6.5)."""
    ds = {"type": "loki", "uid": loki_uid}
    expr = (
        '{host=~"pcmk-.*|san-.*", unit=~"corosync.*|pacemaker.*|drbd.*|.*mq.*"}'
        " |~ `(?i)warn|error|fail|fenc|split-brain`"
    )
    return {
        "type": "logs",
        "title": "▤ Cluster logs (WARN+)",
        "datasource": ds,
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [{"refId": "A", "expr": expr, "datasource": ds}],
        "options": {
            "showTime": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
            "wrapLogMessage": False,
        },
    }


def _annotations(ds_uid: str) -> dict[str, Any]:
    return {
        "list": [
            {
                "name": "owner change",
                "datasource": _ds(ds_uid),
                "enable": True,
                "iconColor": "orange",
                "expr": _OWNER_CHANGE_EXPR,
                "step": "10s",
            },
        ],
    }


def render_cluster_dashboard(
    topo: dict[str, Any],  # noqa: ARG001 - reserved: later PRs derive rows/sites from topology
    arm: str = "pcmk",
    ds_uid: str = "prometheus",
) -> dict[str, Any]:
    """Assemble the cockpit board top-to-bottom: hero band + integrity light, then the
    ② Compute + ③ Storage matrices. Timeline/logs land in later PRs."""
    panels = [
        *hero_tiles(ds_uid, y=0),
        integrity_panel(ds_uid, y=4),
        matrix("② Compute — node × component", _COMPUTE_COLS, ds_uid, y=7),
        matrix("③ Storage — DRBD / SAN", _STORAGE_COLS, ds_uid, y=16),
        timeline_band(ds_uid, y=25),
        log_row("loki", y=32),
    ]
    return {
        "uid": "lab-pcmk-cluster",
        "title": "PCMK Cluster · Infrastructure View",
        "schemaVersion": 39,
        "version": 0,
        "panels": panels,
        "annotations": _annotations(ds_uid),
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["lab", "cockpit", arm],
    }


def cluster_dashboard_path() -> Path:
    """Where the rendered cockpit board is written — beside lab-status.json (gitignored)."""
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"


def lab_cluster_dashboard() -> str:
    """Render the real lab/topology.yaml to cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo), indent=2) + "\n"
