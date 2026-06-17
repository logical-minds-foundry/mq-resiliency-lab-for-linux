"""The arm-backend registry resolver (RDQM-parity design §2).

`topology.yaml`'s `arms:` block is the catalog: each arm names its mechanism and
maps stable verbs to their per-arm implementation. This resolver is the single
source of arm truth — it replaces P1's `provisional_arm` stopgap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from mqlab.paths import repo_root
from mqlab.setups import lab_setups


@dataclass(frozen=True)
class Arm:
    name: str
    mechanism: str
    verbs: dict[str, dict[str, str]]


@dataclass(frozen=True)
class VerbImpl:
    kind: str  # "playbook" | "pcs" | "cmd" | "script"
    value: str


def _topology() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return data


def lab_arms() -> dict[str, Arm]:
    """All arms from topology.yaml's `arms:` block, keyed by name."""
    arms: dict[str, Arm] = {}
    for name, cfg in (_topology().get("arms") or {}).items():
        cfg = cfg or {}
        arms[name] = Arm(
            name=name,
            mechanism=cfg.get("mechanism", ""),
            verbs=cfg.get("verbs") or {},
        )
    return arms


def arm_of(setup_name: str) -> str:
    """The arm a setup belongs to. Raises if the setup is unknown or arm-agnostic."""
    setup = lab_setups().get(setup_name)
    if setup is None or setup.arm is None:
        raise ValueError(f"setup {setup_name!r} has no arm")
    return setup.arm


def resolve_verb(setup_name: str, verb: str) -> VerbImpl:
    """The (kind, value) implementation of `verb` for `setup_name`'s arm."""
    arm_name = arm_of(setup_name)
    arm = lab_arms()[arm_name]
    impl = arm.verbs.get(verb)
    if not impl:
        raise KeyError(f"arm {arm_name!r} does not implement verb {verb!r}")
    [(kind, value)] = impl.items()
    return VerbImpl(kind=kind, value=value)
