"""Resolve a net-selection pattern (a regex, or the `all` keyword) to lab net names.

Candidate names come from the declarative `lab/networks/net-*.xml` definitions, not
live virsh — because `up` must be able to select nets that are not defined yet, and
because the selection is then deterministic and testable. Requiring an explicit
pattern (with `all` as the keyword for everything) keeps a destructive verb like
`net down` from defaulting to "wipe everything" (#75).
"""

from __future__ import annotations

import re

from mqlab.paths import repo_root

ALL = "all"


def lab_net_names() -> list[str]:
    """Sorted names of the lab networks, from lab/networks/net-*.xml."""
    return sorted(p.stem for p in (repo_root() / "lab" / "networks").glob("net-*.xml"))


def select_nets(pattern: str, names: list[str]) -> list[str]:
    """Names matching the pattern: every name for `all`, else re.search(pattern)."""
    if pattern == ALL:
        return list(names)
    return [n for n in names if re.search(pattern, n)]


def resolve_nets(pattern: str) -> list[str]:
    """Resolve a selection pattern to the matching lab net names."""
    return select_nets(pattern, lab_net_names())
