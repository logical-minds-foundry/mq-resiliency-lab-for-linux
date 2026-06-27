"""Resolve a guest-selection pattern (a regex, or the `all` keyword) to lab guest names.

Names come from the `nodes:` keys of the declarative lab/topology.yaml — not live
virsh — because `vm up` must be able to select guests that are not created yet, and
so the selection is deterministic and testable. As with the net selector (#75),
requiring an explicit pattern (with `all` for everything) keeps a destructive verb
like `vm destroy` from defaulting to "wipe every guest" (#80).
"""

from __future__ import annotations

import re

import yaml

from mqlab.paths import repo_root
from mqlab.stacks import stack_members

ALL = "all"


def lab_guest_names() -> list[str]:
    """Sorted guest names from the nodes: section of lab/topology.yaml."""
    data = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return sorted(data.get("nodes", {}))


def select_guests(pattern: str, names: list[str]) -> list[str]:
    """Names matching the pattern: every name for `all`, else re.search(pattern)."""
    if pattern == ALL:
        return list(names)
    return [n for n in names if re.search(pattern, n)]


def resolve_guests(pattern: str) -> list[str]:
    """Resolve a selection: a stack name (its members, in bring-up order) wins;
    else `all` / a regex over guest names (#90)."""
    members = stack_members(pattern)
    if members is not None:
        return members
    return select_guests(pattern, lab_guest_names())
