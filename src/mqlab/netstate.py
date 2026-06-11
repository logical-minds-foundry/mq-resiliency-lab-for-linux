"""Render libvirt network state as a Prometheus textfile (#108, Tweak 2).

Tri-state per #107: absent(0)/inactive(1)/active(2), via lifecycle.classify_net,
over the topology-declared network list — so a destroyed (undefined) net still
gets an explicit 0, not a missing series.
"""

from __future__ import annotations

from mqlab.lifecycle import ABSENT, ACTIVE, INACTIVE, classify_net

_CODE = {ABSENT: 0, INACTIVE: 1, ACTIVE: 2}


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
