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
    # SAN folds into its PCMK site row — the iSCSI SAN is part of that arm's HA
    # setup, so each site shows 4 nodes (san + 3 cluster) as one unit (no separate
    # SAN row). Topology groups are unchanged; this is display grouping only.
    ("PCMK · A", ["pcmk_a", "san_a"]),
    ("PCMK · B", ["pcmk_b", "san_b"]),
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


# Curated network sections (application first, infrastructure last). Each entry's
# nets become per-net rows under a collapsible section header. A net `net-X` maps
# to host bridge `virbr-X` (throughput) and shorthand `X` (display).
NET_SECTIONS: list[tuple[str, list[str]]] = [
    ("Message path", ["net-client", "net-dtcc", "net-data-a", "net-data-b"]),
    ("Cluster + storage", ["net-hb-a", "net-san-a", "net-hb-b", "net-san-b"]),
    ("Cross-site + mgmt", ["net-wan", "net-mgmt"]),
]


def _net_health_panel(net: str, y: int) -> dict[str, Any]:
    short = net.removeprefix("net-")
    return {
        "type": "stat",
        "title": f"{short} — health",
        "gridPos": {"h": 6, "w": 4, "x": 0, "y": y},
        "fieldConfig": {
            "defaults": {
                "mappings": [
                    {
                        "type": "value",
                        "options": {
                            "0": {"text": "ABSENT", "color": "grey"},
                            "1": {"text": "DOWN", "color": "red"},
                            "2": {"text": "DEGRADED", "color": "orange"},
                            "3": {"text": "UP", "color": "green"},
                        },
                    }
                ]
            }
        },
        "targets": [{"expr": f'lab_network_health{{network="{net}"}}'}],
    }


def _net_throughput_panel(net: str, direction: str, x: int, y: int) -> dict[str, Any]:
    # rx/tx off the host bridge (virbr-<x>) — own Y-scale per net so tiny
    # heartbeat traffic stays visible.
    short = net.removeprefix("net-")
    bridge = net.replace("net-", "virbr-", 1)
    metric = "receive" if direction == "rx" else "transmit"
    return {
        "type": "timeseries",
        "title": f"{short} — {direction}",
        "gridPos": {"h": 6, "w": 10, "x": x, "y": y},
        "targets": [{"expr": f'rate(node_network_{metric}_bytes_total{{device="{bridge}"}}[1m])'}],
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

    for section, nets in NET_SECTIONS:
        panels.append(_row(f"Networks · {section}", y))
        y += 1
        for net in nets:
            panels.append(_net_health_panel(net, y))
            panels.append(_net_throughput_panel(net, "rx", 4, y))
            panels.append(_net_throughput_panel(net, "tx", 14, y))
            y += 6

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
