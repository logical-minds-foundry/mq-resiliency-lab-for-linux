"""Render the Grafana dashboard as a pure function of the curated layout + topology (#108).

Sibling of scrape.py / inventory.py: one source of truth. Curated QMS + ROWS +
NET_SECTIONS lists fix the lab-shaped order; the topology `groups` namespace
validates the VM rows. Emits a layered Grafana dashboard — the MQ Service rows
(one per queue manager) on top, grouped VM rows (up/down + per-group CPU) in the
middle, per-network rows at the bottom. uid is pinned so mqlab obs open / the
docs deep-links keep working.

The MQ Service rows are **object-driven, not metric-driven** (#178): the channels
and queues we run are known up front, so each renders a row at all times. The MQSC
object always exists; only its *status* comes and goes. A channel with no live
status reads "No status" (the row stays put — that absence is the signal, not a
vanished row), and lights up Running/Transitioning/Stopped once its status flows.
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
    ("App · DTCC", ["app", "dtcc"]),
    ("Observability", ["obs_box", "probe"]),
]


class DashboardError(RuntimeError):
    """The curated layout cannot be rendered against the topology."""


# A status metric (qmgr / channel status_squash) is numeric; every status tile
# maps it to the same coloured-string family so the panel reads at a glance.
# -1 is our object-driven sentinel: the curated object exists but has no live
# status (exporter down, channel inactive) — the row stays, labelled "No status".
_STATUS_OPTIONS: dict[str, dict[str, str]] = {
    "-1": {"text": "No status", "color": "grey"},
    "0": {"text": "Stopped", "color": "red"},
    "1": {"text": "Transitioning", "color": "yellow"},
    "2": {"text": "Running", "color": "green"},
}


def _status_mappings() -> list[dict[str, Any]]:
    return [{"type": "value", "options": dict(_STATUS_OPTIONS)}]


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
# and the DTCC counterparty (QMDTCC, #153). Each entry also names the channels we
# care about (the client SVRCONN + the inter-QM SENDER/RECEIVER pair, named
# identically on both ends), so the panel draws a tile per channel object even
# when it has no live status. Queue rows are driven by the exporter's curated
# monitoredQueues, so the depth table + rate graphs need no per-QM list here.
# QMDTCC reads "No status" until its exporter lands (#182); QMRDQM / QMAIN return
# if/when those arms are in play.
QMS: list[tuple[str, str, list[str]]] = [
    ("QMPCMK", "service · Ubuntu HA/DR", ["APP.SVRCONN", "QMPCMK.QMDTCC", "QMDTCC.QMPCMK"]),
    ("QMDTCC", "counterparty · DTCC service", ["SVC.SVRCONN", "QMDTCC.QMPCMK", "QMPCMK.QMDTCC"]),
]


def _qm_status_panel(qm: str, y: int) -> dict[str, Any]:
    # `or vector(-1)` keeps the tile populated when the QM has no live status
    # (its exporter is down / not yet wired) — object-driven, never blank.
    return {
        "type": "stat",
        "title": f"{qm} — status",
        "gridPos": {"h": 4, "w": 5, "x": 0, "y": y},
        "fieldConfig": {"defaults": {"mappings": _status_mappings()}},
        "targets": [{"expr": f'max(ibmmq_qmgr_status{{qmgr="{qm}"}}) or vector(-1)'}],
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


def _channel_tiles(qm: str, channels: list[str], y: int) -> list[dict[str, Any]]:
    # Object-driven: a status tile per curated channel, always rendered. The MQSC
    # channel object always exists; only its status comes and goes — so when the
    # SENDER/RECEIVER pair is INACTIVE (no inter-QM traffic) the tile reads
    # "No status" rather than vanishing, and lights up Running once a request
    # crosses the WAN. `or vector(-1)` supplies the sentinel when the series is
    # absent.
    width = 24 // len(channels)
    tiles: list[dict[str, Any]] = []
    for i, channel in enumerate(channels):
        expr = f'max(ibmmq_channel_status_squash{{qmgr="{qm}",channel="{channel}"}}) or vector(-1)'
        tiles.append(
            {
                "type": "stat",
                "title": f"{qm} · {channel}",
                "gridPos": {"h": 4, "w": width, "x": i * width, "y": y},
                "fieldConfig": {"defaults": {"mappings": _status_mappings()}},
                "targets": [{"expr": expr}],
            }
        )
    return tiles


def _queues_table(qm: str, y: int) -> dict[str, Any]:
    # Transmission + application queues — depth (the staging/backlog signal).
    # Queues always have a status, so the curated monitoredQueues populate this
    # directly. Strip the metadata columns the exporter leaks in; keep name +
    # depth only.
    return {
        "type": "table",
        "title": f"{qm} — queues",
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": y},
        "targets": [
            {"expr": f'ibmmq_queue_depth{{qmgr="{qm}"}}', "format": "table", "instant": True}
        ],
        "transformations": [
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "qmgr": True,
                        "job": True,
                        "instance": True,
                        "__name__": True,
                        "cluster": True,
                        "description": True,
                        "platform": True,
                        "usage": True,
                    },
                    "renameByName": {"queue": "Queue", "Value": "Depth"},
                },
            }
        ],
    }


def _queue_rates_panel(qm: str, y: int) -> dict[str, Any]:
    # Depth says "how backed up"; rates say "is it flowing" — enqueue (mqput) and
    # dequeue (mqget) per queue, beside the depth table.
    put = f'rate(ibmmq_queue_mqput_mqput1_count{{qmgr="{qm}"}}[1m])'
    get = f'rate(ibmmq_queue_mqget_count{{qmgr="{qm}"}}[1m])'
    return {
        "type": "timeseries",
        "title": f"{qm} — queue rates",
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": y},
        "fieldConfig": {"defaults": {"unit": "short"}},
        "targets": [
            {"expr": put, "legendFormat": "{{queue}} put"},
            {"expr": get, "legendFormat": "{{queue}} get"},
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

    for qm, role, channels in QMS:
        panels.append(_row(f"MQ Service · {qm} · {role}", y))
        y += 1
        panels.append(_qm_status_panel(qm, y))
        panels.append(_qm_rate_panel(qm, y))
        panels.append(_qm_conn_panel(qm, y))
        y += 4
        panels.extend(_channel_tiles(qm, channels, y))
        y += 4
        panels.append(_queues_table(qm, y))
        panels.append(_queue_rates_panel(qm, y))
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
