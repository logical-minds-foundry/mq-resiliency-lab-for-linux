"""Native-HA cluster-state collector for the lab-nativeha-cluster cockpit (#279).

Stdlib-only so this exact file deploys verbatim to the nha nodes as
/usr/local/bin/lab-nativeha-state and runs on a 5s systemd timer, AND is imported by the
repo's unit tests. Pure parse functions turn `dspmq -o nativeha -x`/`-g` output into rows;
render_nativeha_state_prom turns rows into a node_exporter textfile; probe() runs each source
bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).

Emits the same cluster_* shape as clusterstate.py (so the clusterboard.py builders and the
overview roll-up work unchanged); Native-HA-only facets get cluster_nha_* names (spec §4).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_FIELD = re.compile(r"(\w+)\(([^)]*)\)")


def _fields(line: str) -> dict[str, str]:
    """All KEY(value) tokens on a line -> {KEY: value}. Values that themselves contain
    parens (GRPADDR) aren't read by any metric, so the naive scan is harmless."""
    return dict(_FIELD.findall(line))


def parse_nativeha_x(text: str) -> dict[str, Any]:
    """Parse `dspmq -m <qm> -o nativeha -x` into quorum + group role + per-instance state.

    The summary line carries QUORUM(x/y) and GRPROLE; the indented lines are one per
    instance (INSTANCE/ROLE/INSYNC/HASTATUS). INSYNC(yes) -> True.
    """
    summary: dict[str, Any] = {
        "quorum_current": None,
        "quorum_total": None,
        "group_role": None,
    }
    instances: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if not f:
            continue
        if "QUORUM" in f:
            cur, total = f["QUORUM"].split("/", 1)
            summary["quorum_current"] = int(cur)
            summary["quorum_total"] = int(total)
            summary["group_role"] = f.get("GRPROLE")
        if "INSTANCE" in f and "ROLE" in f:
            instances[f["INSTANCE"]] = {
                "role": f["ROLE"],
                "insync": f.get("INSYNC") == "yes",
                "hastatus": f.get("HASTATUS", "Unknown"),
            }
    return {**summary, "instances": instances}
