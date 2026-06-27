"""Render a salt-ssh roster as a pure function of lab/topology.yaml (#206).

Sibling to inventory.py: one source of truth (topology), the same group-reachable
host set, fail-loud on the same integrity problems. Group/stack membership rides
as grains under `minion_opts` so `salt-ssh -G 'roster_groups:<grp>'` can target it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mqlab.inventory import INSECURE_KEY
from mqlab.paths import repo_root, work

_HEADER = "# salt-ssh roster — generated from lab/topology.yaml. Do not edit by hand.\n"


class RosterError(RuntimeError):
    """topology.yaml cannot be rendered to a valid salt-ssh roster."""


def _mgmt_ip(nodes: dict[str, Any], host: str) -> str:
    spec = nodes.get(host)
    if spec is None:
        raise RosterError(f"group references undefined host: {host}")
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise RosterError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def render_roster(topo: dict[str, Any]) -> str:
    """Project parsed topology -> salt-ssh roster YAML. Raises RosterError on any
    integrity problem (missing mgmt IP, undefined host or group) — never silently.

    Covers exactly the hosts reachable through `groups` (parity with
    render_inventory), in first-appearance order across the groups iteration.
    """
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    stacks = topo.get("stacks", {})

    for stack, cfg in stacks.items():
        for g in (cfg or {}).get("groups", []):
            if g not in groups:
                raise RosterError(f"stack {stack} references undefined group: {g}")

    host_groups: dict[str, list[str]] = {}
    for group, hosts in groups.items():
        for host in hosts:
            host_groups.setdefault(host, []).append(group)

    host_stacks: dict[str, list[str]] = {host: [] for host in host_groups}
    for stack, cfg in stacks.items():
        members = set((cfg or {}).get("groups", []))
        for host, hgroups in host_groups.items():
            if members.intersection(hgroups):
                host_stacks[host].append(stack)

    priv = str(Path(INSECURE_KEY).expanduser())
    roster: dict[str, Any] = {}
    for host in host_groups:
        roster[host] = {
            "host": _mgmt_ip(nodes, host),
            "user": "vagrant",
            "priv": priv,
            "sudo": True,
            "minion_opts": {
                "grains": {
                    "roster_groups": host_groups[host],
                    "roster_stacks": host_stacks[host],
                }
            },
        }
    return _HEADER + yaml.safe_dump(roster, sort_keys=False, default_flow_style=False)


def roster_path() -> Path:
    """Where the rendered roster is written — under the gitignored build/ tree."""
    return work("salt", "roster")


def lab_roster() -> str:
    """Render the real lab/topology.yaml to salt-ssh roster text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_roster(topo)
