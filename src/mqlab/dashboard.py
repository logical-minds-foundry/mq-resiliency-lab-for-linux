"""Render the Grafana dashboard as a pure function of the curated layout + topology (#108).

Sibling of scrape.py / inventory.py: one source of truth. Curated QMS + ROWS +
NET_SECTIONS lists fix the lab-shaped order; the topology `groups` namespace
validates the VM rows. Emits a layered Grafana dashboard — the MQ Service rows
(one per queue manager) on top, grouped VM rows (up/down + per-group CPU) in the
middle, per-network rows at the bottom. uid is pinned so mqlab obs open / the
docs deep-links keep working.
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


# --- MQ Service (Layer 2) ---------------------------------------------------
# One curated row per queue manager we actually run today: our HA QM (QMPCMK)
# and the DTCC counterparty (QMDTCC, #153). Metrics confirmed from the exporter
# source in the #141 spike — they read no-data until mq_prometheus is wired
# (#172), but the layout is real. Each QM row: status / msg-rate / connections
# stat tiles + a channels table (ibmmq_channel_status_squash) + a queues table
# (ibmmq_queue_depth). QMRDQM (RHEL arm) and QMAIN (standalone) return if/when
# those arms are in play.
QMS: list[tuple[str, str]] = [
    ("QMPCMK", "service · Ubuntu HA/DR"),
    ("QMDTCC", "counterparty · DTCC service"),
]


def _qm_status_panel(qm: str, y: int) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": f"{qm} — status",
        "gridPos": {"h": 4, "w": 5, "x": 0, "y": y},
        "fieldConfig": {
            "defaults": {
                "mappings": [
                    {"type": "value", "options": {"0": {"text": "STOPPED", "color": "red"}}}
                ],
                "thresholds": {
                    "steps": [{"value": None, "color": "red"}, {"value": 1, "color": "green"}]
                },
            }
        },
        "targets": [{"expr": f'ibmmq_qmgr_status{{qmgr="{qm}"}}'}],
    }


def _qm_rate_panel(qm: str, y: int) -> dict[str, Any]:
    # The "it's moving" QM stat — message activity (puts + destructive gets).
    expr = (
        f'rate(ibmmq_qmgr_interval_mqput_mqput1_total_count{{qmgr="{qm}"}}[1m]) '
        f'+ rate(ibmmq_qmgr_interval_destructive_get_total_count{{qmgr="{qm}"}}[1m])'
    )
    return {
        "type": "stat",
        "title": f"{qm} — msg rate",
        "gridPos": {"h": 4, "w": 5, "x": 5, "y": y},
        "options": {"graphMode": "area"},
        "fieldConfig": {"defaults": {"unit": "short"}},
        "targets": [{"expr": expr}],
    }


def _qm_conn_panel(qm: str, y: int) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": f"{qm} — connections",
        "gridPos": {"h": 4, "w": 5, "x": 10, "y": y},
        "targets": [{"expr": f'ibmmq_qmgr_connection_count{{qmgr="{qm}"}}'}],
    }


def _channels_table(qm: str, y: int) -> dict[str, Any]:
    # Inter-QM links + the client SVRCONN — status for watching retries.
    return {
        "type": "table",
        "title": f"{qm} — channels",
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [
            {
                "expr": f'ibmmq_channel_status_squash{{qmgr="{qm}"}}',
                "format": "table",
                "instant": True,
            }
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True, "qmgr": True, "job": True, "instance": True},
                    "renameByName": {"channel": "Channel", "chltype": "Type", "Value": "Status"},
                },
            }
        ],
    }


def _queues_table(qm: str, y: int) -> dict[str, Any]:
    # Transmission + application queues — depth (the staging/backlog signal).
    return {
        "type": "table",
        "title": f"{qm} — queues",
        "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
        "targets": [
            {"expr": f'ibmmq_queue_depth{{qmgr="{qm}"}}', "format": "table", "instant": True}
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {"Time": True, "qmgr": True, "job": True, "instance": True},
                    "renameByName": {"queue": "Queue", "usage": "Type", "Value": "Depth"},
                },
            }
        ],
    }


# Curated network sections (application first, infrastructure last). Each entry's
# nets become per-net rows under a collapsible section header. A net `net-X` maps
# to host bridge `virbr-X` (throughput) and shorthand `X` (display).
NET_SECTIONS: list[tuple[str, list[str]]] = [
    ("Message path", ["net-client", "net-dtcc", "net-ext", "net-data-a", "net-data-b"]),
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

    for qm, role in QMS:
        panels.append(_row(f"MQ Service · {qm} · {role}", y))
        y += 1
        panels.append(_qm_status_panel(qm, y))
        panels.append(_qm_rate_panel(qm, y))
        panels.append(_qm_conn_panel(qm, y))
        y += 4
        panels.append(_channels_table(qm, y))
        y += 8
        panels.append(_queues_table(qm, y))
        y += 8

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
