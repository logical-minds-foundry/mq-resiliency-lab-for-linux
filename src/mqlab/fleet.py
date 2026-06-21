"""Topology-aware fleet status: join declarative topology.yaml with live virsh state (#86).

Unlike a pass-through (the net status table removed in #70), this *composes* two
sources — what the topology defines and what virsh has — to show the whole intended
fleet, instantiated or not, with each guest's platform and the setups it belongs to.
The setups come from config (topology.yaml), replacing the old hardcoded arm-from-
name-prefix map — config, not knowledge baked into code (#90).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from mqlab.hostfacts import HostFacts, probe
from mqlab.paths import repo_root
from mqlab.platforms import default_platform
from mqlab.setups import setups_of


@dataclass(frozen=True)
class FleetRow:
    guest: str
    platform: str
    state: str
    setups: str


def lab_guests(facts: HostFacts | None = None) -> dict[str, str]:
    """guest name -> platform, from topology.yaml. The default Ubuntu platform is
    host-resolved (native-preferred, #276) for any node that doesn't pin one."""
    facts = facts if facts is not None else probe()
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    default = default_platform(facts)
    nodes = data.get("nodes", {})
    return {name: (cfg or {}).get("platform", default) for name, cfg in nodes.items()}


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


def fleet_rows(platforms: dict[str, str], states: dict[str, str]) -> list[FleetRow]:
    """Join topology guests with virsh state (domains are named lab_<guest>) and the
    setups each guest belongs to. Rows are sorted by column left-to-right
    (Guest, Platform, State, Setup(s)) so the leading column reads in order; Guest is
    unique so it dominates and the rest are tie-breakers (#273)."""
    rows = [
        FleetRow(
            guest,
            platform,
            states.get(f"lab_{guest}", "not created"),
            ", ".join(setups_of(guest)),
        )
        for guest, platform in platforms.items()
    ]
    return sorted(rows, key=lambda r: (r.guest, r.platform, r.state, r.setups))
