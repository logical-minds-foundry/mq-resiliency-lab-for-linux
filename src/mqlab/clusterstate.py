"""Cluster-state collector for the PCMK drill cockpit (#177, Plan 1a).

Stdlib-only so this exact file is deployed verbatim to the pcmk/san guests as
/usr/local/bin/lab-cluster-state and run by a 5s systemd timer, AND imported by the
repo's unit tests. Pure parse functions turn command output into metric rows;
render_cluster_state_prom turns rows into a node_exporter textfile; probe() runs each
source bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# role -> ordered probe sources it runs
PROBE_SETS: dict[str, tuple[str, ...]] = {
    "cluster": ("crm", "stonith", "iscsi", "daemons"),
    "storage": ("drbd", "daemons"),
}


def parse_crm(xml_text: str) -> dict[str, Any]:
    """Parse crm_mon XML into quorum, per-node states, and resource placement.

    Returns {quorate: bool, nodes: {name: {online, standby, unclean}},
    resources: {id: {state, node}}}.
    """
    root = ET.fromstring(xml_text)  # noqa: S314  # locally-run crm_mon output, not untrusted input
    dc = root.find("./summary/current_dc")
    quorate = dc is not None and dc.get("with_quorum") == "true"

    nodes: dict[str, dict[str, bool]] = {}
    for n in root.findall("./nodes/node"):
        nodes[n.get("name", "")] = {
            "online": n.get("online") == "true",
            "standby": n.get("standby") == "true",
            "unclean": n.get("unclean") == "true",
        }

    resources: dict[str, dict[str, Any]] = {}
    # Scope to the mq_group — the resource-group row the cockpit shows. Top-level
    # STONITH (fence_*) resources are deliberately excluded; fencing is reported
    # separately from stonith_admin history (§3.1).
    for r in root.findall(".//group[@id='mq_group']//resource"):
        rid = r.get("id", "")
        held = r.find("./node")
        resources[rid] = {
            "state": r.get("role", "Unknown"),
            "node": held.get("name") if held is not None else None,
        }
    return {"quorate": quorate, "nodes": nodes, "resources": resources}


def parse_drbd(text: str) -> dict[str, Any]:
    """Parse `drbdsetup status --verbose --statistics` into
    {resource: {role,disk,conn,resync_pct,out_of_sync_bytes}}.

    Output is whitespace-indented `key:value` tokens::

        mqlun role:Primary suspended:no
          volume:0 minor:0 disk:UpToDate
          peer connection:Connected role:Secondary congested:no
            volume:0 replication:Established peer-disk:UpToDate ... done:73.2
                received:0 sent:65712 out-of-sync:0 ...

    `--json` is NOT used: this image's drbdsetup rejects it (#272). A resource
    block starts at column 0 (`<name> role:...`); indented lines refine it. The
    connection state (Connected/StandAlone/...) drives the integrity light; an
    in-sync resource has no `done:` token, so resync reads 100%.
    """
    out: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] = {}
    for raw in text.splitlines():
        tokens = dict(t.split(":", 1) for t in raw.split() if ":" in t)
        if raw[:1] not in ("", " ", "\t") and "role" in tokens:
            cur = {
                "role": tokens["role"],
                "disk": "Unknown",
                "conn": "Unknown",
                "resync_pct": 100.0,
                "out_of_sync_bytes": 0,
            }
            out[raw.split()[0]] = cur
        if "disk" in tokens:  # local volume line (the peer line uses peer-disk:)
            cur["disk"] = tokens["disk"]
        if "connection" in tokens:  # peer line: Connected / StandAlone / ...
            cur["conn"] = tokens["connection"]
        if "done" in tokens:  # present only while resyncing
            cur["resync_pct"] = float(tokens["done"])
        if "out-of-sync" in tokens:
            cur["out_of_sync_bytes"] = int(tokens["out-of-sync"])
    return out


def parse_stonith(text: str) -> dict[str, int]:
    """stonith_admin --history '*' -> {node: fence_action_count}; empty == clean."""
    counts: dict[str, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if " was reset " in line or " was fenced " in line:
            node = line.split(" ", 1)[0]
            counts[node] = counts.get(node, 0) + 1
    return counts


def parse_iscsi(text: str) -> int:
    """iscsiadm -m session -> count of active sessions (lines starting with a transport)."""
    return sum(1 for raw in text.splitlines() if raw.strip().startswith(("tcp:", "iser:")))


def parse_daemons(text: str, units: list[str]) -> dict[str, bool]:
    """One `systemctl is-active` line per unit (same order) -> {unit: is_active}."""
    lines = text.splitlines()
    return {
        unit: (lines[i].strip() == "active" if i < len(lines) else False)
        for i, unit in enumerate(units)
    }


def _m(name: str, labels: Mapping[str, object], value: object) -> str:
    """Format one Prometheus sample line: name{k="v",...} value."""
    rendered = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return f"{name}{{{rendered}}} {value}"


def render_cluster_state_prom(
    *,
    node: str,
    crm: dict[str, Any] | None,
    stonith: dict[str, int] | None,
    iscsi: int | None,
    daemons: dict[str, bool],
    drbd: dict[str, Any] | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed probe results into node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if crm is not None:
        lines.append(_m("cluster_quorate", {"node": node}, 1 if crm["quorate"] else 0))
        for member, st in crm["nodes"].items():
            base = {"node": node, "member": member}
            lines.append(_m("cluster_node_online", base, 1 if st["online"] else 0))
            lines.append(_m("cluster_node_unclean", base, 1 if st["unclean"] else 0))
        for rid, r in crm["resources"].items():
            rbase = {"node": node, "resource": rid}
            started = 1 if r["state"] == "Started" else 0
            lines.append(_m("cluster_resource_started", rbase, started))
            if r["node"]:
                lines.append(_m("cluster_resource_owner", {**rbase, "holder": r["node"]}, 1))
        # Fence baseline: every known member reads 0 (clean → green) unless stonith
        # history shows events for it (→ red). Tied to the crm member set so a clean
        # cluster isn't a column of grey no-data.
        fences = stonith or {}
        for member in crm["nodes"]:
            fc = {"node": node, "member": member}
            lines.append(_m("cluster_fence_count", fc, fences.get(member, 0)))

    if iscsi is not None:
        lines.append(_m("cluster_iscsi_sessions", {"node": node}, iscsi))

    for unit, up in daemons.items():
        lines.append(_m("cluster_daemon_up", {"node": node, "unit": unit}, 1 if up else 0))

    if drbd is not None:
        for res, d in drbd.items():
            rbase = {"node": node, "resource": res}
            for kind in ("role", "disk", "conn"):
                lines.append(_m(f"cluster_drbd_{kind}", {**rbase, kind: d[kind]}, 1))
            if d["resync_pct"] is not None:
                lines.append(_m("cluster_drbd_resync_pct", rbase, d["resync_pct"]))
            if d["out_of_sync_bytes"] is not None:
                lines.append(_m("cluster_drbd_out_of_sync_bytes", rbase, d["out_of_sync_bytes"]))

    for source in fresh_sources:
        ts = {"node": node, "source": source}
        lines.append(_m("cluster_state_last_write_timestamp", ts, now))

    return "\n".join(lines) + "\n"


# source -> (command, timeout seconds). Timeouts are well under the 5s tick (§4.2).
DAEMON_UNITS = {"cluster": ["corosync", "pacemaker"], "storage": ["drbd"]}
_COMMANDS = {
    "crm": (["crm_mon", "--one-shot", "--output-as=xml"], 3),
    "drbd": (["drbdsetup", "status", "--verbose", "--statistics"], 2),
    "stonith": (["stonith_admin", "--history", "*"], 2),
    "iscsi": (["iscsiadm", "-m", "session"], 2),
}


def probe(cmd: list[str], timeout: int, *, ignore_rc: bool = False) -> str | None:
    """Run cmd bounded; return stdout on success, None on timeout/nonzero/OSError (-> STALE).

    ignore_rc=True returns stdout regardless of exit code — for tools like
    `systemctl is-active` that report a valid state ("inactive") with a non-zero exit,
    where non-zero means "down", not "the probe failed".
    """
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired, OSError:
        return None
    if ignore_rc:
        return cp.stdout
    return cp.stdout if cp.returncode == 0 else None


def collect(role: str, node: str, now: int) -> str:
    """Run this role's probe set and render the textfile body."""
    crm = stonith = iscsi = drbd = None
    daemons: dict[str, bool] = {}
    fresh: list[str] = []
    for source in PROBE_SETS[role]:
        if source == "daemons":
            units = DAEMON_UNITS[role]
            # is-active exits non-zero when a unit is inactive/failed — that is a valid
            # "down" reading, not a probe failure, so keep stdout regardless of rc.
            raw = probe(["systemctl", "is-active", *units], timeout=2, ignore_rc=True)
            if raw is not None:
                daemons = parse_daemons(raw, units)
                fresh.append("daemons")
            continue
        cmd, timeout = _COMMANDS[source]
        raw = probe(cmd, timeout)
        if raw is None:
            continue
        if source == "crm":
            crm = parse_crm(raw)
        elif source == "drbd":
            drbd = parse_drbd(raw)
        elif source == "stonith":
            stonith = parse_stonith(raw)
        else:  # iscsi — the only remaining probe source
            iscsi = parse_iscsi(raw)
        fresh.append(source)
    return render_cluster_state_prom(
        node=node,
        crm=crm,
        stonith=stonith,
        iscsi=iscsi,
        daemons=daemons,
        drbd=drbd,
        now=now,
        fresh_sources=tuple(fresh),
    )


def main(argv: list[str] | None = None) -> None:
    """Entry point for the deployed collector. `lab-cluster-state --role {cluster,storage}`."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True, choices=sorted(PROBE_SETS))
    ap.add_argument("--node", default=os.uname().nodename)
    ap.add_argument("--out", default="/var/lib/node_exporter/textfile/lab_cluster_state.prom")
    ap.add_argument("--now", type=int, default=None)
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else int(time.time())
    body = collect(args.role, args.node, now)
    tmp = Path(args.out + ".tmp")
    tmp.write_text(body)
    tmp.replace(args.out)


if __name__ == "__main__":
    main()
