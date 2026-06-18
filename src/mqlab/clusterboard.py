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
    "up": [{"type": "value", "options": {
        "0": {"color": _RED, "text": "down", "index": 0},
        "1": {"color": _GREEN, "text": "up", "index": 1}}}],
    "clean0": [
        {"type": "value", "options": {"0": {"color": _GREEN, "text": "ok", "index": 0}}},
        {"type": "range", "options": {"from": 1, "to": 9999,
            "result": {"color": _RED, "text": "!", "index": 1}}}],
    "sessions": [
        {"type": "value", "options": {"0": {"color": _RED, "text": "none", "index": 0}}},
        {"type": "range", "options": {"from": 1, "to": 9999,
            "result": {"color": _GREEN, "text": "ok", "index": 1}}}],
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
        targets.append({"refId": ref, "expr": expr, "format": "table",
                        "instant": True, "datasource": _ds(ds_uid)})
        rename[f"Value #{ref}"] = col_title
        overrides.append({"matcher": {"id": "byName", "options": col_title}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
            {"id": "mappings", "value": _MAPPINGS[kind]},
            {"id": "color", "value": {"mode": "fixed"}}]})
    return {
        "type": "table", "title": title, "datasource": _ds(ds_uid),
        "gridPos": {"h": 9, "w": 24, "x": 0, "y": y},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "n", "mode": "outer"}},
            {"id": "organize", "options": {"renameByName": rename, "excludeByName": {"Time": True}}}],
        "fieldConfig": {"defaults": {"custom": {"align": "center"}}, "overrides": overrides},
    }


_PRECEDENCE = ("STALE", "red", "amber", "green")


def fold_side(cells: list[str]) -> str:
    """Fold a side's cell states to one tri-state+STALE, worst-wins with STALE first
    (precedence STALE > red > amber > green). No cells = blind collector = STALE."""
    if not cells:
        return "STALE"
    for level in _PRECEDENCE:
        if level in cells:
            return level
    return "green"


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


def render_cluster_dashboard(
    topo: dict[str, Any], arm: str = "pcmk", ds_uid: str = "prometheus"
) -> dict[str, Any]:
    """Assemble the cockpit board: the ② Compute + ③ Storage matrices on a dedicated
    board with a stable uid. Hero/timeline/logs/① cards land in later PRs."""
    panels = [
        matrix("② Compute — node × component", _COMPUTE_COLS, ds_uid, y=0),
        matrix("③ Storage — DRBD / SAN", _STORAGE_COLS, ds_uid, y=9),
    ]
    return {
        "uid": "lab-pcmk-cluster", "title": "PCMK Cluster · Infrastructure View",
        "schemaVersion": 39, "version": 0, "panels": panels,
        "time": {"from": "now-15m", "to": "now"}, "refresh": "10s",
        "tags": ["lab", "cockpit", arm],
    }


def cluster_dashboard_path() -> Path:
    """Where the rendered cockpit board is written — beside lab-status.json (gitignored)."""
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-pcmk-cluster.json"


def lab_cluster_dashboard() -> str:
    """Render the real lab/topology.yaml to cockpit dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_cluster_dashboard(topo), indent=2) + "\n"
