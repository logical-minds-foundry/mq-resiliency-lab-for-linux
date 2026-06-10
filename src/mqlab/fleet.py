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

from mqlab.paths import repo_root
from mqlab.setups import setups_of

DEFAULT_PLATFORM = "ubuntu2404-arm64"


@dataclass(frozen=True)
class FleetRow:
    guest: str
    platform: str
    state: str
    setups: str


def lab_guests() -> dict[str, str]:
    """guest name -> platform, from topology.yaml (applying the default platform)."""
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    default = data.get("defaults", {}).get("platform", DEFAULT_PLATFORM)
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
    setups each guest belongs to. Sorted so guests cluster by setup membership."""
    rows = [
        FleetRow(
            guest,
            platform,
            states.get(f"lab_{guest}", "not created"),
            ", ".join(setups_of(guest)),
        )
        for guest, platform in platforms.items()
    ]
    return sorted(rows, key=lambda r: (r.setups, r.guest))
