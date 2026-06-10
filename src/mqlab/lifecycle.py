"""Classify a guest's current libvirt state — the awareness that makes the vm
lifecycle idempotent (#99). You cannot be idempotent without first looking at the
world: each verb checks state, then acts only where needed.

State is derived from `virsh list --all` (the ground-truth source, per #96), keyed
by the `lab_<guest>` domain name.
"""

from __future__ import annotations

ABSENT = "absent"  # no domain defined
OFF = "off"  # defined, not running
RUNNING = "running"  # defined and running


def classify(states: dict[str, str], guest: str) -> str:
    """Map a guest to ABSENT / OFF / RUNNING from parsed `virsh list --all` state."""
    raw = states.get(f"lab_{guest}")
    if raw is None:
        return ABSENT
    if raw == "running":
        return RUNNING
    return OFF  # shut off / paused / etc. — defined but not running
