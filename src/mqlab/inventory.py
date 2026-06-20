"""Render the Ansible inventory as a pure function of lab/topology.yaml (#101).

The inventory is the full declarative *map* of every host — not a snapshot of what
is running (liveness lives in the verb pre-flight, #99/#102). One source of truth
(topology), one grouping namespace: atomic role×site groups, plus setups rendered
as Ansible `:children` parent groups (underscore names = groups, hyphens = hosts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

# Confirmed by the #101 transport spike. With config.ssh.insert_key=false every
# guest shares Vagrant's insecure key, so the inventory needs one constant path.
INSECURE_KEY = "~/.vagrant.d/insecure_private_key"


class InventoryError(RuntimeError):
    """topology.yaml cannot be rendered to a valid inventory."""


def _mgmt_ip(nodes: dict[str, Any], host: str) -> str:
    spec = nodes.get(host)
    if spec is None:
        raise InventoryError(f"group references undefined host: {host}")
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise InventoryError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def render_inventory(topo: dict[str, Any]) -> str:
    """Project parsed topology -> Ansible INI text. Raises InventoryError on any
    integrity problem (missing mgmt IP, undefined host or group) — never silently."""
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    setups = topo.get("setups", {})
    lines: list[str] = []
    for group, hosts in groups.items():
        lines.append(f"[{group}]")
        for host in hosts:
            lines.append(f"{host} ansible_host={_mgmt_ip(nodes, host)}")
    for setup, cfg in setups.items():
        members = (cfg or {}).get("groups", [])
        for g in members:
            if g not in groups:
                raise InventoryError(f"setup {setup} references undefined group: {g}")
        lines.append(f"[{setup}:children]")
        lines.extend(members)
    lines += [
        "[all:vars]",
        "ansible_user=vagrant",
        f"ansible_ssh_private_key_file={INSECURE_KEY}",
        "ansible_ssh_common_args=-o StrictHostKeyChecking=no",
        "ansible_python_interpreter=/usr/bin/python3",
    ]
    return "\n".join(lines) + "\n"


def inventory_path() -> Path:
    """Where the rendered inventory is written — under the gitignored build/ tree."""
    return work("inventory.ini")


def lab_inventory() -> str:
    """Render the real lab/topology.yaml to inventory INI text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_inventory(topo)
