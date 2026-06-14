"""Cluster-state collector for the PCMK drill cockpit (#177, Plan 1a).

Stdlib-only so this exact file is deployed verbatim to the pcmk/san guests as
/usr/local/bin/lab-cluster-state and run by a 5s systemd timer, AND imported by the
repo's unit tests. Pure parse functions turn command output into metric rows;
render_cluster_state_prom turns rows into a node_exporter textfile; probe() runs each
source bounded + non-blocking (timeout -> no fresh sample -> the cell reads STALE).
"""

from __future__ import annotations

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
