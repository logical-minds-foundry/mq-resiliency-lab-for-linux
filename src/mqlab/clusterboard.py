"""Pure builders for the cluster-cockpit board (lab-pcmk-cluster). Data in → Grafana
panel/dashboard dicts out, no I/O — mirrors dashboard.py. Engine = Table (spike §4.1)."""

from __future__ import annotations

from typing import Any

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
