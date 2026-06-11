"""Render libvirt network state as a Prometheus textfile (#108, Tweak 2).

Tri-state per #107: absent(0)/inactive(1)/active(2), via lifecycle.classify_net,
over the topology-declared network list — so a destroyed (undefined) net still
gets an explicit 0, not a missing series.
"""

from __future__ import annotations

from typing import Any

from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, classify_net

_CODE = {ABSENT: 0, INACTIVE: 1, ACTIVE: 2}
EXCLUDED_NETS = {"net-mgmt"}  # the scrape plane itself — never a tested data path


def render_net_state_prom(net_names: list[str], parsed_states: dict[str, str]) -> str:
    """Project (declared net names, parsed `virsh net-list --all`) -> textfile metrics."""
    lines = [
        "# HELP lab_network_state libvirt network state (0=absent,1=inactive,2=active)",
        "# TYPE lab_network_state gauge",
    ]
    for net in net_names:
        code = _CODE[classify_net(parsed_states, net)]
        lines.append(f'lab_network_state{{network="{net}"}} {code}')
    return "\n".join(lines) + "\n"


def net_peers(topo: dict[str, Any]) -> dict[str, dict[str, list[dict[str, str]]]]:
    """host -> network -> [{peer, ip}] for every same-network peer (mgmt excluded)."""
    nodes = topo.get("nodes", {})
    members: dict[str, list[tuple[str, str]]] = {}  # network -> [(host, ip)]
    for host, spec in nodes.items():
        for net, ip in ((spec or {}).get("nics") or {}).items():
            if net in EXCLUDED_NETS:
                continue
            members.setdefault(net, []).append((host, str(ip)))
    result: dict[str, dict[str, list[dict[str, str]]]] = {}
    for net, hosts in members.items():
        for host, _ in hosts:
            peers = [{"peer": p, "ip": pip} for p, pip in hosts if p != host]
            if peers:
                result.setdefault(host, {})[net] = peers
    return result
