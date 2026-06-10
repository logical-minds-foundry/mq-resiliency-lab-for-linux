"""Lab setups — named groupings of guests into runnable configurations, from topology.yaml.

A setup names which machines compose a config (e.g. the Pacemaker/SAN HA arm), in
bring-up order, and which Ansible playbook provisions them. This is the single
source of truth that `mqlab vm` and `vm status` read — replacing knowledge that
lived in inventory.sh name-patterns, the playbook play-order, and the AI's context
(#90).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from mqlab.paths import repo_root


@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    members: list[str]
    provision: str | None


def lab_setups() -> dict[str, Setup]:
    """All named setups from topology.yaml, keyed by name."""
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    result: dict[str, Setup] = {}
    for name, cfg in (data.get("setups") or {}).items():
        cfg = cfg or {}
        result[name] = Setup(
            name=name,
            description=cfg.get("description", ""),
            members=list(cfg.get("members", [])),
            provision=cfg.get("provision"),
        )
    return result


def setup_members(name: str) -> list[str] | None:
    """Members of a named setup (in bring-up order), or None if no such setup."""
    setup = lab_setups().get(name)
    return list(setup.members) if setup else None


def setups_of(guest: str) -> list[str]:
    """Names of the setups a guest belongs to, sorted."""
    return sorted(name for name, setup in lab_setups().items() if guest in setup.members)
