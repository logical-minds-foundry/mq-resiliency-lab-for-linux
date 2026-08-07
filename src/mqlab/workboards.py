"""Portable render mode + work-edition board helpers (#963, epic .github#169 Wave 1a).

The lab cockpits (`clusterboard.py`, `qmboard.py`) are *lab-object-driven*: every panel
binds a concrete lab QM/queue/channel name and the lab's own Prometheus/Loki datasource
uid. That is right for the lab, but it makes the boards unusable anywhere else.

This module is the FOUNDATION the work-edition board generators (#964/#965/#967) build on.
It renders the *same* panel primitives **portable**:

- every datasource is the ``${datasource}`` (or ``${loki}``) template variable — never a
  hardcoded datasource uid — so the board drops into any MQ Grafana with a Prometheus (and
  Loki) datasource, and
- every lab object is a template variable (``$qmgr``, ...), never a lab literal.

It reuses `clusterboard`'s panel primitives (`_ds`, `_stat`, `_timeseries`, `_logs_panel`,
`_state_timeline`, `_row_header`) verbatim — `clusterboard` stays lab-driven; portability
is layered on top here, so the two concerns never entangle. The rich QM / queue-channel /
infra boards themselves are OUT of scope for this task; this ships only the mechanism, the
template-variable helpers, and one minimal seeded overview board that exercises the whole
render path end to end.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from mqlab.clusterboard import _STALE_MAP, _STATUS_MAP, _ds, _stat, _t, _timeseries

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# The template-variable names + their ${...} reference forms. A portable board wires every
# prometheus datasource to ${datasource} and every loki datasource to ${loki}; objects are
# referenced by their own template variables (e.g. $qmgr) inside the query text.
DATASOURCE_VAR = "datasource"
LOKI_VAR = "loki"
DS_REF = f"${{{DATASOURCE_VAR}}}"
LOKI_REF = f"${{{LOKI_VAR}}}"


def tmpl_var(
    name: str,
    query: str,
    *,
    var_type: str = "query",
    label: str | None = None,
    datasource: str = DS_REF,
    multi: bool = False,
    include_all: bool = False,
    regex: str = "",
    hide: int = 0,
    refresh: int = 2,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one Grafana templating variable.

    The default ``var_type="query"`` builds a Prometheus query variable — e.g.
    ``tmpl_var("qmgr", "label_values(ibmmq_qmgr_status, qmgr)")`` — whose own datasource
    references the ``${datasource}`` variable, so the whole board stays datasource-portable.
    ``var_type="datasource"`` builds the datasource picker itself (its ``query`` is the
    plugin id, e.g. ``"prometheus"``) and carries no ``datasource`` field, since it is the
    source of ``${datasource}`` and referencing one would be circular.
    """
    var: dict[str, Any] = {
        "name": name,
        "type": var_type,
        "label": name if label is None else label,
        "query": query,
        "multi": multi,
        "includeAll": include_all,
        "regex": regex,
        "hide": hide,
        "refresh": refresh,
        "current": {} if current is None else current,
        "options": [],
    }
    if var_type != "datasource":
        var["datasource"] = _ds(datasource)
    return var


def datasource_var(
    name: str = DATASOURCE_VAR, *, plugin: str = "prometheus", label: str = "Data source"
) -> dict[str, Any]:
    """The datasource picker variable — the source of ``${datasource}``. `plugin` is the
    Grafana datasource plugin id it filters the picker to (default ``prometheus``)."""
    return tmpl_var(name, plugin, var_type="datasource", label=label, refresh=1)


def qmgr_var(
    name: str = "qmgr", *, label: str = "Queue manager", metric: str = "ibmmq_qmgr_status"
) -> dict[str, Any]:
    """The queue-manager picker: ``label_values(<metric>, qmgr)`` over the portable
    datasource, so the board lists whatever QMs the target Prometheus actually scrapes."""
    return tmpl_var(name, f"label_values({metric}, qmgr)", label=label)


def _portable_ds(ds: dict[str, Any]) -> dict[str, Any]:
    """Rewrite one datasource ref to its portable template variable, preserving the type:
    loki → ``${loki}``, everything else (prometheus) → ``${datasource}``."""
    if ds.get("type") == "loki":
        return {"type": "loki", "uid": LOKI_REF}
    return {"type": "prometheus", "uid": DS_REF}


def _portabilize(panel: dict[str, Any]) -> dict[str, Any]:
    """Rewrite every datasource on a panel (panel-level, per-target, and recursively into a
    collapsed row's child panels) to its portable template variable, in place."""
    if "datasource" in panel:
        panel["datasource"] = _portable_ds(panel["datasource"])
    for target in panel.get("targets", []):
        if "datasource" in target:
            target["datasource"] = _portable_ds(target["datasource"])
    for child in panel.get("panels", []):
        _portabilize(child)
    return panel


def portable_dashboard(
    title: str,
    uid: str,
    panels: list[dict[str, Any]],
    templating: list[dict[str, Any]],
    *,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble a datasource-portable dashboard. Every panel's datasource is rewritten to
    the ``${datasource}`` / ``${loki}`` template variable (no hardcoded datasource uid), so
    the board is portable regardless of how the caller built its panels. The caller's panel
    list is deep-copied first, so its (lab-driven) originals are never mutated."""
    portable_panels = [_portabilize(deepcopy(p)) for p in panels]
    return {
        "uid": uid,
        "title": title,
        "schemaVersion": 39,
        "version": 0,
        "panels": portable_panels,
        "templating": {"list": list(templating)},
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "tags": ["work", "portable"] if tags is None else list(tags),
    }


# ── seeded work-edition boards ──────────────────────────────────────────────────
#
# The rich QM / queue-channel / infra boards are #964/#965/#967 (interactive panel design
# done later). Wave 1a ships one minimal overview board so the render path — primitives →
# portable_dashboard → write — is exercised end to end and the CLI has real output. Later
# waves append their builders to WORK_BOARD_BUILDERS.

# QM status pill vocabulary, reused from the lab boards: -1/0/1/2 coloured, plus STALE so a
# null series reads no-data, never a false healthy.
_QM_STATUS_MAP: list[dict[str, Any]] = [*_STATUS_MAP, _STALE_MAP]


def _overview_board() -> dict[str, Any]:
    """A minimal portable QM overview: pick a datasource + a $qmgr, see that QM's status and
    connection trend. Object-agnostic (everything rides $qmgr) and datasource-agnostic — the
    foundation the Wave-1b/1c boards join, and the smallest board that proves the contract."""
    banner = {
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": {"h": 2, "w": 24, "x": 0, "y": 0},
        "options": {"mode": "markdown", "content": "## Queue Manager · $qmgr — portable overview"},
    }
    status = _stat(
        "QM status",
        'max(ibmmq_qmgr_status{qmgr="$qmgr"}) or vector(-1)',
        DS_REF,
        0,
        2,
        mappings=_QM_STATUS_MAP,
        w=8,
        h=4,
        value_size=22,
    )
    connections = _timeseries(
        "Connections",
        [_t("A", 'max(ibmmq_qmgr_connection_count{qmgr="$qmgr"}) or vector(-1)', "connections")],
        DS_REF,
        8,
        2,
        w=16,
        h=4,
    )
    return portable_dashboard(
        "Queue Manager — Overview (portable)",
        "work-qm-overview",
        [banner, status, connections],
        [datasource_var(), qmgr_var()],
        tags=["work", "portable", "qm"],
    )


# The registry of work-edition board builders. Wave 1b/1c (#964/#965/#967) append here.
WORK_BOARD_BUILDERS: list[Callable[[], dict[str, Any]]] = [_overview_board]


def work_dashboard_paths_and_texts(out_dir: Path) -> list[tuple[Path, str]]:
    """Render every registered work-edition board; return (path, text) pairs without touching
    the filesystem (the pure seam the tests drive). Each board's uid is its filename stem."""
    out: list[tuple[Path, str]] = []
    for build in WORK_BOARD_BUILDERS:
        board = build()
        text = json.dumps(board, indent=2) + "\n"
        out.append((out_dir / f"{board['uid']}.json", text))
    return out


def write_work_dashboards(out_dir: Path) -> list[Path]:
    """Render + write every registered work-edition board under `out_dir`; return the written
    paths. The CLI passes ``build/work/grafana/work-edition/`` (via `mqlab.paths.work`)."""
    paths: list[Path] = []
    for path, text in work_dashboard_paths_and_texts(out_dir):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        paths.append(path)
    return paths
