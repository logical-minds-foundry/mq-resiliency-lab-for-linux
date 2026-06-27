"""Render the Ansible inventory as a pure function of lab/topology.yaml (#101).

The inventory is the full declarative *map* of every host — not a snapshot of what
is running (liveness lives in the verb pre-flight, #99/#102). One source of truth
(topology), one grouping namespace: atomic role×site groups, plus each stack
rendered as an Ansible `:children` parent group (#350). The stack-aggregate group
name is the stack name with hyphens→underscores (`pcmk-ubuntu` → `pcmk_ubuntu`),
so a provision playbook can target `hosts: pcmk_ubuntu` and reach the union of that
stack's atomic groups. Reserved stacks with no groups (e.g. nativeha-ubuntu) emit
no aggregate. (underscore names = groups, hyphens = hosts.)
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


def _stack_group(stack: str) -> str:
    """The Ansible aggregate-group name for a stack: hyphens → underscores (#350)."""
    return stack.replace("-", "_")


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
    stacks = topo.get("stacks", {})
    lines: list[str] = []
    for group, hosts in groups.items():
        lines.append(f"[{group}]")
        for host in hosts:
            lines.append(f"{host} ansible_host={_mgmt_ip(nodes, host)}")
    for stack, cfg in stacks.items():
        members = (cfg or {}).get("groups", [])
        for g in members:
            if g not in groups:
                raise InventoryError(f"stack {stack} references undefined group: {g}")
        if not members:
            continue  # reserved stack (e.g. nativeha-ubuntu) — no aggregate to render
        lines.append(f"[{_stack_group(stack)}:children]")
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
