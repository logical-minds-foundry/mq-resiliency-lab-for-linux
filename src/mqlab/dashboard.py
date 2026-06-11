"""Render the Grafana dashboard as a pure function of the curated layout + topology (#108).

Sibling of scrape.py / inventory.py: one source of truth. A curated ROWS list
fixes the lab-shaped order (SAN, the PCMK/RDQM arms split A/B, standalone,
observability); the topology `groups` namespace validates it. Emits a layered
Grafana dashboard — reserved MQ row on top, grouped VM rows (up/down + per-group
CPU) in the middle, a placeholder network row at the bottom (Tweak 2 replaces
it). uid is pinned so mqlab obs open / the docs deep-links keep working.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path

DASHBOARD_UID = "lab-fleet-node"  # pinned — referenced by mqlab obs open + docs

# Curated, lab-shaped order. Each row rolls up one-or-more atomic groups; SAN
# pairs both site SANs, the cluster arms split by site.
ROWS: list[tuple[str, list[str]]] = [
    ("SAN", ["san_a", "san_b"]),
    ("PCMK · A", ["pcmk_a"]),
    ("PCMK · B", ["pcmk_b"]),
    ("RDQM · A", ["rdqm_a"]),
    ("RDQM · B", ["rdqm_b"]),
    ("Standalone", ["qm", "dtcc", "client"]),
    ("Observability", ["obs_box", "probe"]),
]


class DashboardError(RuntimeError):
    """The curated layout cannot be rendered against the topology."""


def _row(title: str, y: int) -> dict[str, Any]:
    return {
        "type": "row",
        "title": title,
        "gridPos": {"h": 1, "w": 24, "x": 0, "y": y},
        "panels": [],
    }


def _text(content: str, y: int) -> dict[str, Any]:
    return {
        "type": "text",
        "gridPos": {"h": 3, "w": 24, "x": 0, "y": y},
        "options": {"mode": "markdown", "content": content},
    }


def _up_panel(label: str, sel: str, y: int) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": f"{label} — up",
        "gridPos": {"h": 4, "w": 10, "x": 0, "y": y},
        "fieldConfig": {
            "defaults": {
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "DOWN", "color": "red"},
                            "1": {"text": "UP", "color": "green"},
                        },
                    }
                ]
            }
        },
        "targets": [{"expr": f'up{{job="node", groups=~"{sel}"}}', "legendFormat": "{{host}}"}],
    }


def _cpu_panel(label: str, sel: str, y: int) -> dict[str, Any]:
    expr = (
        "100 - (avg by (host) "
        f'(rate(node_cpu_seconds_total{{mode="idle", groups=~"{sel}"}}[1m])) * 100)'
    )
    return {
        "type": "timeseries",
        "title": f"{label} — CPU busy %",
        "gridPos": {"h": 4, "w": 14, "x": 10, "y": y},
        "targets": [{"expr": expr, "legendFormat": "{{host}}"}],
    }


def render_dashboard(topo: dict[str, Any]) -> dict[str, Any]:
    """Project the curated ROWS + topology groups -> a Grafana dashboard dict."""
    known = set(topo.get("groups", {}))
    panels: list[dict[str, Any]] = []
    y = 0

    panels.append(_row("MQ Service — reserved · Layer 2", y))
    y += 1
    panels.append(_text("Queue-manager owner · depth · channel status arrive in **Layer 2**.", y))
    y += 3

    for label, groups in ROWS:
        for g in groups:
            if g not in known:
                raise DashboardError(f"unknown group in ROWS: {g}")
        sel = "|".join(groups)
        panels.append(_row(f"VMs · {label}", y))
        y += 1
        panels.append(_up_panel(label, sel, y))
        panels.append(_cpu_panel(label, sel, y))
        y += 4

    panels.append(_row("Networks", y))
    y += 1
    panels.append(_text("Network status arrives in **Tweak 2** (#108 follow-up).", y))
    y += 3

    return {
        "title": "Lab — Layered Status",
        "uid": DASHBOARD_UID,
        "schemaVersion": 39,
        "version": 1,
        "time": {"from": "now-15m", "to": "now"},
        "refresh": "10s",
        "panels": panels,
    }


def dashboard_path() -> Path:
    """Where the rendered dashboard is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "grafana" / "dashboards" / "lab-status.json"


def lab_dashboard() -> str:
    """Render the real lab/topology.yaml to dashboard JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return json.dumps(render_dashboard(topo), indent=2) + "\n"
