"""Cluster-state collector for the PCMK drill cockpit (#177, Plan 1a).

Stdlib-only so this exact file is deployed verbatim to the pcmk/san guests as
/usr/local/bin/lab-cluster-state and run by a 5s systemd timer, AND imported by the
repo's unit tests. Pure parse functions turn command output into metric rows;
render_cluster_state_prom turns rows into a node_exporter textfile; probe() runs each
source bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

# role -> ordered probe sources it runs
PROBE_SETS: dict[str, tuple[str, ...]] = {
    "cluster": ("crm", "stonith", "iscsi", "daemons"),
    "storage": ("drbd", "daemons"),
}


def parse_crm(xml_text: str) -> dict:
    """crm_mon --output-as=xml -> {quorate, nodes{name:{online,standby,unclean}}, resources{id:{state,node}}}."""
    root = ET.fromstring(xml_text)
    dc = root.find("./summary/current_dc")
    quorate = dc is not None and dc.get("with_quorum") == "true"

    nodes: dict[str, dict[str, bool]] = {}
    for n in root.findall("./nodes/node"):
        nodes[n.get("name", "")] = {
            "online": n.get("online") == "true",
            "standby": n.get("standby") == "true",
            "unclean": n.get("unclean") == "true",
        }

    resources: dict[str, dict] = {}
    for r in root.findall(".//resources//resource"):
        rid = r.get("id", "")
        held = r.find("./node")
        resources[rid] = {
            "state": r.get("role", "Unknown"),
            "node": held.get("name") if held is not None else None,
        }
    return {"quorate": quorate, "nodes": nodes, "resources": resources}


def parse_drbd(json_text: str) -> dict:
    """drbdsetup/drbdadm status --json -> {resource: {role, disk, conn, resync_pct, out_of_sync_bytes}}."""
    out: dict[str, dict] = {}
    for res in json.loads(json_text):
        dev0 = (res.get("devices") or [{}])[0]
        conns = res.get("connections") or []
        if conns:
            conn0 = conns[0]
            peerdev0 = (conn0.get("peer_devices") or [{}])[0]
            conn = conn0.get("connection-state", "Unknown")
            resync = peerdev0.get("percent-in-sync")
            oos = peerdev0.get("out-of-sync")
        else:
            conn, resync, oos = "Disconnected", None, None
        out[res.get("name", "")] = {
            "role": res.get("role", "Unknown"),
            "disk": dev0.get("disk-state", "Unknown"),
            "conn": conn,
            "resync_pct": resync,
            "out_of_sync_bytes": oos,
        }
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


def render_cluster_state_prom(
    *,
    node: str,
    crm: dict | None,
    stonith: dict | None,
    iscsi: int | None,
    daemons: dict,
    drbd: dict | None,
    now: int,
    fresh_sources: tuple[str, ...],
) -> str:
    """Project parsed probe results -> node_exporter textfile lines (label node=<self>)."""
    lines: list[str] = []

    if crm is not None:
        lines.append(f'cluster_quorate{{node="{node}"}} {1 if crm["quorate"] else 0}')
        for member, st in crm["nodes"].items():
            online = 1 if st["online"] else 0
            unclean = 1 if st["unclean"] else 0
            lines.append(f'cluster_node_online{{node="{node}",member="{member}"}} {online}')
            lines.append(f'cluster_node_unclean{{node="{node}",member="{member}"}} {unclean}')
        for rid, r in crm["resources"].items():
            started = 1 if r["state"] == "Started" else 0
            lines.append(f'cluster_resource_started{{node="{node}",resource="{rid}"}} {started}')
            if r["node"]:
                lines.append(
                    f'cluster_resource_owner{{node="{node}",resource="{rid}",holder="{r["node"]}"}} 1'
                )

    if stonith is not None:
        for member, count in stonith.items():
            lines.append(f'cluster_fence_count{{node="{node}",member="{member}"}} {count}')

    if iscsi is not None:
        lines.append(f'cluster_iscsi_sessions{{node="{node}"}} {iscsi}')

    for unit, up in daemons.items():
        lines.append(f'cluster_daemon_up{{node="{node}",unit="{unit}"}} {1 if up else 0}')

    if drbd is not None:
        for res, d in drbd.items():
            for kind in ("role", "disk", "conn"):
                lines.append(
                    f'cluster_drbd_{kind}{{node="{node}",resource="{res}",{kind}="{d[kind]}"}} 1'
                )
            if d["resync_pct"] is not None:
                lines.append(f'cluster_drbd_resync_pct{{node="{node}",resource="{res}"}} {d["resync_pct"]}')
            if d["out_of_sync_bytes"] is not None:
                lines.append(
                    f'cluster_drbd_out_of_sync_bytes{{node="{node}",resource="{res}"}} {d["out_of_sync_bytes"]}'
                )

    for source in fresh_sources:
        lines.append(f'cluster_state_last_write_timestamp{{node="{node}",source="{source}"}} {now}')

    return "\n".join(lines) + "\n"
