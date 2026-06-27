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
from mqlab.stacks import QmConfig

__all__ = [
    "QmConfig",
    "Setup",
    "lab_groups",
    "lab_setups",
    "setup_members",
    "setups_of",
]


@dataclass(frozen=True)
class Setup:
    name: str
    description: str
    groups: list[str]
    provision: str | None
    secrets: list[str]
    qm: QmConfig | None
    arm: str | None = None


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def lab_groups() -> dict[str, list[str]]:
    """Atomic inventory groups -> member hosts, from topology.yaml."""
    return {g: list(hosts) for g, hosts in (_topology().get("groups") or {}).items()}


def _qm_config(cfg: dict[str, Any], arms: dict[str, Any]) -> QmConfig:
    """Build a setup's QmConfig. The setup's arm declares a `short` token (#351
    Phase 2); the QM names derive from it — `<short>APP` / `<short>SVC` — so the
    rename is a single source change. There is no retired-name fallback: a setup
    whose arm has no short falls back to the explicit `qm.name`/`qm.svc` keys."""
    qm = cfg["qm"]
    short = (arms.get(cfg.get("arm") or "") or {}).get("short")
    return QmConfig(
        name=f"{short}APP" if short else qm["name"],
        vip=qm.get("vip", ""),
        vip_ext=qm.get("vip_ext", ""),
        svc_conn=qm.get("svc_conn"),
        svc=f"{short}SVC" if short else qm.get("svc", ""),
    )


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
            qm=_qm_config(cfg, data.get("arms") or {}) if cfg.get("qm") else None,
            arm=cfg.get("arm"),
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
