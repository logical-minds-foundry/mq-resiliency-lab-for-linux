"""Render Prometheus file_sd scrape targets as a pure function of lab/topology.yaml (#103).

Sibling of inventory.py: one source of truth (topology), one address plan (the
net-mgmt IP). Emits the `node` job target list (node_exporter on :9100). MQ and
ha_cluster jobs are added by later plans (Layer 2). Fail-loud — a node without a
net-mgmt IP is an error, never a silent skip.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

NODE_EXPORTER_PORT = 9100
HYPERVISOR_MGMT_IP = "10.50.0.1"  # the Vergil VM (libvirt host) on net-mgmt


class ScrapeError(RuntimeError):
    """topology.yaml cannot be rendered to valid scrape targets."""


def _mgmt_ip(spec: dict[str, Any], host: str) -> str:
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise ScrapeError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def _groups_of(groups: dict[str, list[str]], host: str) -> str:
    return ",".join(sorted(g for g, hosts in groups.items() if host in hosts))


def render_scrape_targets(topo: dict[str, Any]) -> str:
    """Project parsed topology -> Prometheus file_sd JSON for the node job."""
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    entries = [
        {
            "targets": [f"{_mgmt_ip(spec or {}, host)}:{NODE_EXPORTER_PORT}"],
            "labels": {"host": host, "groups": _groups_of(groups, host)},
        }
        for host, spec in nodes.items()
    ]
    entries.append(
        {
            "targets": [f"{HYPERVISOR_MGMT_IP}:{NODE_EXPORTER_PORT}"],
            "labels": {"host": "hypervisor", "groups": "hypervisor"},
        }
    )
    return json.dumps(entries, indent=2) + "\n"


def scrape_targets_path() -> Path:
    """Where the rendered node target list is written — under the gitignored build/ tree."""
    return work("prometheus", "targets", "node.json")


def lab_scrape_targets() -> str:
    """Render the real lab/topology.yaml to file_sd JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_scrape_targets(topo)
