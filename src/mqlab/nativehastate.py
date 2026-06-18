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

    The leading QMNAME summary line carries QUORUM(x/y) + GRPNAME/GRPROLE (but no HASTATUS);
    the indented lines are one per instance (INSTANCE/ROLE/INSYNC/HASTATUS/...). INSYNC(yes)
    -> True. The summary line is consumed for quorum + group role only, never as an instance
    row (the per-instance state comes from the indented lines, which carry HASTATUS).
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
            continue
        if "INSTANCE" in f and "ROLE" in f:
            instances[f["INSTANCE"]] = {
                "role": f["ROLE"],
                "insync": f.get("INSYNC") == "yes",
                "hastatus": f.get("HASTATUS", "Unknown"),
            }
    return {**summary, "instances": instances}


def _yn(f: dict[str, str], key: str) -> bool | None:
    """A yes/no field as bool, or None when the field is absent (the live group does not
    report CONNGRP/INSYNC — those are reported only on the recovery group line)."""
    return f[key] == "yes" if key in f else None


def parse_nativeha_g(text: str) -> dict[str, dict[str, Any]]:
    """Parse `dspmq -m <qm> -o nativeha -g` (CRR) into {group_name: {role,status,connected,
    insync,backlog}}, keyed by GRPNAME.

    The output is led by the same QMNAME summary line as -x (skipped here — it carries
    QUORUM), then one line per group. Both groups report GRPROLE + GRSTATUS; only the
    recovery group line carries CONNGRP/INSYNC/BACKLOG (the live group's cross-region facets
    are absent, so they read None and are omitted downstream rather than faked). BACKLOG is a
    message count, never seconds.
    """
    groups: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        f = _fields(line)
        if "GRPNAME" not in f or "QUORUM" in f:  # skip non-group lines + the QMNAME summary
            continue
        groups[f["GRPNAME"]] = {
            "role": f.get("GRPROLE", "Unknown"),
            "status": f.get("GRSTATUS", "Unknown"),
            "connected": _yn(f, "CONNGRP"),
            "insync": _yn(f, "INSYNC"),
            "backlog": int(f["BACKLOG"]) if "BACKLOG" in f else None,
        }
    return groups


def _m(name: str, labels: Mapping[str, object], value: object) -> str:
    """Format one Prometheus sample line: name{k="v",...} value."""
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


def render_nativeha_state_prom(
    *,
    node: str,
    qm: str,
    hax: dict[str, Any] | None,
    grp: dict[str, dict[str, Any]] | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed dspmq results into node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if hax is not None:
        cur, total = hax["quorum_current"], hax["quorum_total"]
        if cur is not None and total is not None:
            majority = total // 2 + 1
            lines.append(_m("cluster_quorate", {"node": node}, 1 if cur >= majority else 0))
            lines.append(_m("cluster_nha_quorum", {"node": node}, cur))
        for member, st in hax["instances"].items():
            base = {"node": node, "member": member}
            lines.append(_m("cluster_node_online", base, 0 if st["role"] == "Unknown" else 1))
            lines.append(_m("cluster_nha_role", {**base, "role": st["role"]}, 1))
            lines.append(_m("cluster_nha_insync", base, 1 if st["insync"] else 0))
            lines.append(_m("cluster_nha_hastatus", {**base, "status": st["hastatus"]}, 1))
            if st["role"] == "Active":
                owner = {"node": node, "resource": qm, "holder": member}
                lines.append(_m("cluster_resource_owner", owner, 1))

    if grp is not None:
        for name, g in grp.items():
            gbase = {"node": node, "group": name}
            lines.append(_m("cluster_nha_group_role", {**gbase, "role": g["role"]}, 1))
            lines.append(_m("cluster_nha_group_status", {**gbase, "status": g["status"]}, 1))
            # CRR facets are reported only on the recovery group line; omit (never fake)
            # them for the live group, where dspmq does not report them.
            if g["connected"] is not None:
                lines.append(_m("cluster_nha_connected", gbase, 1 if g["connected"] else 0))
            if g["insync"] is not None:
                lines.append(_m("cluster_nha_group_insync", gbase, 1 if g["insync"] else 0))
            if g["backlog"] is not None:
                lines.append(_m("cluster_nha_group_backlog", gbase, g["backlog"]))

    for source in fresh_sources:
        ts = {"node": node, "source": source}
        lines.append(_m("cluster_state_last_write_timestamp", ts, now))

    return "\n".join(lines) + "\n"


def _commands(qm: str) -> dict[str, tuple[list[str], int]]:
    """source -> (argv, timeout). dspmq must run as the mqm user (matching the arm's
    qm-status verb), so each probe shells through `su - mqm -c`; the absolute dspmq path
    sidesteps any dependence on mqm's PATH. Timeouts are well under the 5s tick."""
    dspmq = f"/opt/mqm/bin/dspmq -m {qm} -o nativeha"
    return {
        "nativeha_x": (["su", "-", "mqm", "-c", f"{dspmq} -x"], 3),
        "nativeha_g": (["su", "-", "mqm", "-c", f"{dspmq} -g"], 3),
    }


def probe(cmd: list[str], timeout: int) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE)."""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return None
    return cp.stdout if cp.returncode == 0 else None


def collect(node: str, qm: str, now: int) -> str:
    """Run both dspmq probes and render the textfile body."""
    cmds = _commands(qm)
    fresh: list[str] = []
    raw_x = probe(*cmds["nativeha_x"])
    hax = None
    if raw_x is not None:
        hax = parse_nativeha_x(raw_x)
        fresh.append("nativeha_x")
    raw_g = probe(*cmds["nativeha_g"])
    grp = None
    if raw_g is not None:
        grp = parse_nativeha_g(raw_g)
        fresh.append("nativeha_g")
    return render_nativeha_state_prom(
        node=node, qm=qm, hax=hax, grp=grp, now=now, fresh_sources=tuple(fresh)
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the deployed collector: `lab-nativeha-state --qm QMNATIVE`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--qm", required=True)
    ap.add_argument("--node", default=os.uname().nodename)
    ap.add_argument("--out", default="/var/lib/node_exporter/textfile/lab_nativeha_state.prom")
    ap.add_argument("--now", type=int, default=None)
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    body = collect(args.node, args.qm, now)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(body)
    tmp.replace(args.out)


if __name__ == "__main__":
    main()
