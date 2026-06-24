"""Classify a guest's or network's current libvirt state — the awareness that makes
the lifecycle verbs idempotent (#99/#98). You cannot be idempotent without first
looking at the world: each verb checks state, then acts only where needed.

Guest state is derived from `virsh list --all`, keyed by the `lab_<guest>` domain
name; network state from `virsh net-list --all`, keyed by the plain network name
(the ground-truth sources, per #96).
"""

from __future__ import annotations

ABSENT = "absent"  # no domain/network defined
OFF = "off"  # guest defined, not running
RUNNING = "running"  # guest defined and running
INACTIVE = "inactive"  # network defined, not active
ACTIVE = "active"  # network defined and active


def classify(states: dict[str, str], guest: str) -> str:
    """Map a guest to ABSENT / OFF / RUNNING from parsed `virsh list --all` state."""
    raw = states.get(f"lab_{guest}")
    if raw is None:
        return ABSENT
    if raw == "running":
        return RUNNING
    return OFF  # shut off / paused / etc. — defined but not running


def is_live(states: dict[str, str], guest: str) -> bool:
    """True iff the guest is defined AND still holds a live qemu process — running OR
    paused/suspended. Such a domain must be force-off'd before `undefine
    --remove-all-storage`, which only works on a genuinely stopped (`shut off`)
    domain. `classify`'s OFF lumps paused in with shut-off, so it can't make this
    distinction (#339)."""
    raw = states.get(f"lab_{guest}")
    return raw is not None and raw != "shut off"


def classify_net(states: dict[str, str], net: str) -> str:
    """Map a network to ABSENT / INACTIVE / ACTIVE from parsed `virsh net-list --all`.

    Networks are named plainly (no `lab_` prefix); virsh reports active/inactive.
    """
    raw = states.get(net)
    if raw is None:
        return ABSENT
    if raw == "active":
        return ACTIVE
    return INACTIVE  # defined but not active
