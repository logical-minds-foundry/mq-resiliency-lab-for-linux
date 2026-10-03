"""Topology-aware fleet status: join declarative topology.yaml with live virsh state (#86).

Unlike a pass-through (the net status table removed in #70), this *composes* two
sources — what the topology defines and what virsh has — to show the whole intended
fleet, instantiated or not, with each guest's box and the stacks it belongs to.
The stacks come from config (topology.yaml), replacing the old hardcoded arm-from-
name-prefix map — config, not knowledge baked into code (#90).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from mqlab.paths import repo_root
from mqlab.stacks import stack_members
from mqlab.versions import load_catalog, node_boxes


@dataclass(frozen=True)
class FleetRow:
    guest: str
    box: str
    state: str
    stacks: str


def stacks_of(guest: str) -> list[str]:
    """Names of the canonical stacks a guest belongs to, sorted (#350).

    The stack model replaces the old setups: a guest belongs to a stack when it is
    a member of that stack's groups. Commons-only hosts (svc/app/obs/probe) belong
    to no stack and get an empty list.
    """
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return sorted(
        name for name in (data.get("stacks") or {}) if guest in (stack_members(name) or [])
    )


def lab_guests() -> dict[str, str]:
    """guest name -> the box it boots, from topology.yaml through the version layer
    (versions.node_boxes: each node's box role on its stack's or the infra OS)."""
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return {name: entry.name for name, entry in node_boxes(data, load_catalog()).items()}


def parse_domain_states(text: str) -> dict[str, str]:
    """Parse `virsh list --all` output -> {domain_name: state}."""
    states: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("Id") or set(line) <= {"-"}:
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        states[parts[1]] = " ".join(parts[2:])
    return states


def fleet_rows(boxes: dict[str, str], states: dict[str, str]) -> list[FleetRow]:
    """Join topology guests with virsh state (domains are named lab_<guest>) and the
    stacks each guest belongs to. Rows are sorted by column left-to-right
    (Guest, Box, State, Stack(s)) so the leading column reads in order; Guest is
    unique so it dominates and the rest are tie-breakers (#273)."""
    rows = [
        FleetRow(
            guest,
            box,
            states.get(f"lab_{guest}", "not created"),
            ", ".join(stacks_of(guest)),
        )
        for guest, box in boxes.items()
    ]
    return sorted(rows, key=lambda r: (r.guest, r.box, r.state, r.stacks))
