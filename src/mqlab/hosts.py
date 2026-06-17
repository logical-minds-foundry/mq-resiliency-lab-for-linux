"""Render a canonical /etc/hosts fragment from lab/topology.yaml (#233).

One name->IP map for the instrumented cluster, with per-plane aliases so every node IP
is addressable by name: `<node>` and `<node>-mgmt` -> its net-mgmt IP; `<node>-<plane>`
-> each other NIC (plane = the NIC name minus the `net-` prefix). Mirrors inventory.py:
a pure function of topology, written under build/ and synced to the instrumented nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root

if TYPE_CHECKING:
    from pathlib import Path


def render_hosts(topo: dict[str, Any]) -> str:
    """Project parsed topology -> /etc/hosts text (per-plane aliases)."""
    nodes = topo.get("nodes", {})
    lines: list[str] = []
    for host, spec in nodes.items():
        nics = (spec or {}).get("nics") or {}
        mgmt = nics.get("net-mgmt")
        if mgmt:
            lines.append(f"{mgmt} {host} {host}-mgmt")
        for nic, ip in nics.items():
            if nic == "net-mgmt":
                continue
            plane = nic.removeprefix("net-")
            lines.append(f"{ip} {host}-{plane}")
    return "\n".join(lines) + "\n"


def hosts_path() -> Path:
    """Where the rendered hosts file is written — under the gitignored build/ tree."""
    return repo_root() / "build" / "hosts"


def lab_hosts() -> str:
    """Render the real lab/topology.yaml to /etc/hosts text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_hosts(topo)
