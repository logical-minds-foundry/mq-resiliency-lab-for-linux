"""Lab setups — named groupings of guests into runnable configurations, from topology.yaml.

A setup names which machines compose a config (e.g. the Pacemaker/SAN HA arm), in
bring-up order, and which Ansible playbook provisions them. Setups are defined as
**compositions of atomic groups** (the `groups:` block), so the same grouping
namespace serves topology, the playbooks, and the Ansible inventory (#101). This is
the single source of truth that `mqlab vm` and `vm status` read — replacing knowledge
that lived in inventory.sh name-patterns, the playbook play-order, and the AI's
context (#90).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from mqlab.paths import repo_root


@dataclass(frozen=True)
class QmConfig:
    """A setup's queue-manager identity (#109): the QM name, its internal data-plane
    VIP, its partner-facing (net-ext) VIP for the inter-business link (#146), and —
    when this QM talks to a counterparty — that counterparty's CONNAME (#147)."""

    name: str
    vip: str
    vip_ext: str
    dtcc_conn: str | None = None


@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None
    secrets: list[str]
    qm: QmConfig | None


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def lab_groups() -> dict[str, list[str]]:
    """Atomic inventory groups -> member hosts, from topology.yaml."""
    return {g: list(hosts) for g, hosts in (_topology().get("groups") or {}).items()}


def lab_setups() -> dict[str, Setup]:
    """All named setups from topology.yaml, keyed by name."""
    data = _topology()
    result: dict[str, Setup] = {}
    for name, cfg in (data.get("setups") or {}).items():
        cfg = cfg or {}
        result[name] = Setup(
            name=name,
            description=cfg.get("description", ""),
            groups=list(cfg.get("groups", [])),
            provision=cfg.get("provision"),
            secrets=list(cfg.get("secrets", [])),
            qm=QmConfig(
                name=cfg["qm"]["name"],
                vip=cfg["qm"]["vip"],
                vip_ext=cfg["qm"]["vip_ext"],
                dtcc_conn=cfg["qm"].get("dtcc_conn"),
            )
            if cfg.get("qm")
            else None,
        )
    return result


def setup_members(name: str) -> list[str] | None:
    """Members of a setup (groups flattened in declared order, de-duped), or None."""
    setup = lab_setups().get(name)
    if setup is None:
        return None
    groups = lab_groups()
    members: list[str] = []
    for g in setup.groups:
        for host in groups.get(g, []):
            if host not in members:
                members.append(host)
    return members


def setups_of(guest: str) -> list[str]:
    """Names of the setups a guest belongs to, sorted."""
    return sorted(name for name in lab_setups() if guest in (setup_members(name) or []))
