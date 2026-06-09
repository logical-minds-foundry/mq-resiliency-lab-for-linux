"""Topology-aware fleet status: join declarative topology.yaml with live virsh state (#86).

Unlike a pass-through (the net status table removed in #70), this *composes* two
sources — what the topology defines and what virsh has — to show the whole intended
fleet, instantiated or not, with each guest's arm and platform.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from mqlab.paths import repo_root

DEFAULT_PLATFORM = "ubuntu2404-arm64"

# Bespoke lab structure: guest-name prefix -> arm/role label.
_ARM_PREFIXES = (
    ("rdqm-", "RDQM / RHEL"),
    ("pcmk-", "Pacemaker / SAN"),
    ("san-", "Pacemaker / SAN"),
    ("node-", "Phase-A placeholder"),
)
_STANDALONE = {"qm-main", "dtcc-sim", "app-client"}


@dataclass(frozen=True)
class FleetRow:
    guest: str
    arm: str
    platform: str
    state: str


def arm_of(guest: str) -> str:
    """Map a guest name to its arm/role label (bespoke lab structure)."""
    for prefix, label in _ARM_PREFIXES:
        if guest.startswith(prefix):
            return label
    if guest in _STANDALONE:
        return "Phase-B standalone"
    return "?"


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
    """Join topology guests with virsh state (domains are named lab_<guest>)."""
    rows = [
        FleetRow(guest, arm_of(guest), platform, states.get(f"lab_{guest}", "not created"))
        for guest, platform in platforms.items()
    ]
    return sorted(rows, key=lambda r: (r.arm, r.guest))
