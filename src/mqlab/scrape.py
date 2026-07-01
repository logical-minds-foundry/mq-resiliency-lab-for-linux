"""Render Prometheus file_sd scrape targets as a pure function of lab/topology.yaml (#103).

Sibling of inventory.py: one source of truth (topology), one address plan (the
net-mgmt IP). Emits the `node` job target list (node_exporter on :9100) and the
`ibmmq` job — one mq_prometheus exporter pair PER STACK on the topology alloc
ports, so every per-stack messaging board can be live (incl. all stacks at once,
#423). Fail-loud — a node without a net-mgmt IP is an error, never a silent skip.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import yaml

from mqlab.paths import repo_root, work

if TYPE_CHECKING:
    from pathlib import Path

NODE_EXPORTER_PORT = 9100
HYPERVISOR_MGMT_IP = "10.50.0.1"  # the Vergil VM (libvirt host) on net-mgmt

# --- MQ exporter (#423) -------------------------------------------------------
# The mq_prometheus exporters run on mon-probe, client-mode, one app + one svc
# instance per stack on the stack's alloc ports. The exporter reaches each app QM
# the SAME way the app does: the arm's data-plane VIP where it has one, else the
# Native HA site-A instances' data-plane addresses as a CONNAME list (no VIP).
MQ_LISTENER_PORT = 1414
MON_CHANNEL = "MON.SVRCONN"  # dedicated ops SVRCONN the exporter presents O=app-org on (#250)
# The SVC counterparty QM is reached on svc-sim's net-ext address; one per stack,
# but they share svc-sim:1414, so only the active stack's svc exporter connects.
SVC_EXPORTER_CONN = f"10.60.0.50({MQ_LISTENER_PORT})"
PROBE_GROUP = "probe"
EXPORTER_DATA_NET = "net-data-a"  # the plane mon-probe reaches site-A QMs on (VIPs live here)


class ScrapeError(RuntimeError):
    """topology.yaml cannot be rendered to valid scrape targets."""


def _mgmt_ip(spec: dict[str, Any], host: str) -> str:
    ip = (spec.get("nics") or {}).get("net-mgmt")
    if not ip:
        raise ScrapeError(f"host has no net-mgmt IP: {host}")
    return str(ip)


def _groups_of(groups: dict[str, list[str]], host: str) -> str:
    return ",".join(sorted(g for g, hosts in groups.items() if host in hosts))


def render_scrape_targets(topo: dict[str, Any]) -> str:
    """Project parsed topology -> Prometheus file_sd JSON for the node job."""
    nodes = topo.get("nodes", {})
    groups = topo.get("groups", {})
    entries = [
        {
            "targets": [f"{_mgmt_ip(spec or {}, host)}:{NODE_EXPORTER_PORT}"],
            "labels": {"host": host, "groups": _groups_of(groups, host)},
        }
        for host, spec in nodes.items()
    ]
    entries.append(
        {
            "targets": [f"{HYPERVISOR_MGMT_IP}:{NODE_EXPORTER_PORT}"],
            "labels": {"host": "hypervisor", "groups": "hypervisor"},
        }
    )
    return json.dumps(entries, indent=2) + "\n"


def scrape_targets_path() -> Path:
    """Where the rendered node target list is written — under the gitignored build/ tree."""
    return work("prometheus", "targets", "node.json")


def lab_scrape_targets() -> str:
    """Render the real lab/topology.yaml to file_sd JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_scrape_targets(topo)


# --- MQ exporter derivation (#423) -------------------------------------------


def _probe_mgmt_ip(topo: dict[str, Any]) -> str:
    """The mgmt-plane address the mq_prometheus exporters listen on (mon-probe)."""
    hosts = (topo.get("groups") or {}).get(PROBE_GROUP) or []
    if not hosts:
        raise ScrapeError(f"no '{PROBE_GROUP}' group host to run the MQ exporters on")
    host = hosts[0]
    return _mgmt_ip((topo.get("nodes") or {}).get(host) or {}, host)


def _app_qm_conn(topo: dict[str, Any], name: str, cfg: dict[str, Any]) -> str:
    """The client CONNAME(s) the exporter uses to reach a stack's app QM — reached the
    same way the app reaches it: the arm's VIP when it has one, else the Native HA
    site-A instances' data-plane addresses as a CONNAME list (no VIP)."""
    vip = (cfg.get("qm") or {}).get("vip")
    if vip:
        return f"{vip}({MQ_LISTENER_PORT})"
    cluster_group = cfg.get("cluster_group")
    if not cluster_group:
        raise ScrapeError(f"stack {name}: no QM VIP and no cluster_group for an exporter conn")
    hosts = (topo.get("groups") or {}).get(cluster_group) or []
    if not hosts:
        raise ScrapeError(f"stack {name}: cluster_group {cluster_group} has no hosts")
    nodes = topo.get("nodes") or {}
    conns = []
    for h in hosts:
        ip = ((nodes.get(h) or {}).get("nics") or {}).get(EXPORTER_DATA_NET)
        if not ip:
            raise ScrapeError(f"host {h} has no {EXPORTER_DATA_NET} IP for an exporter conn")
        conns.append(f"{ip}({MQ_LISTENER_PORT})")
    return ",".join(conns)


def mq_exporter_instances(topo: dict[str, Any]) -> list[dict[str, Any]]:
    """One mq_prometheus instance per QM per stack (app + svc), on the stack's alloc
    ports. QM names derive from the stack short (#351); series are qmgr-keyed so a
    board follows failover. Stacks without alloc exporter ports are skipped."""
    out: list[dict[str, Any]] = []
    for name, cfg in (topo.get("stacks") or {}).items():
        cfg = cfg or {}
        short = cfg.get("short")
        alloc = cfg.get("alloc") or {}
        app_port = alloc.get("exporter_app_port")
        svc_port = alloc.get("exporter_svc_port")
        if not short or not app_port or not svc_port:
            continue  # reserved / not observable
        qm_app, qm_svc = f"{short}APP", f"{short}SVC"
        out.append(
            {
                "instance": qm_app.lower(),
                "stack": name,
                "role": "app",
                "qm": qm_app,
                "conn": _app_qm_conn(topo, name, cfg),
                "channel": MON_CHANNEL,
                "port": app_port,
            }
        )
        out.append(
            {
                "instance": qm_svc.lower(),
                "stack": name,
                "role": "svc",
                "qm": qm_svc,
                "conn": SVC_EXPORTER_CONN,
                "channel": MON_CHANNEL,
                "port": svc_port,
            }
        )
    return out


def render_mq_scrape_targets(topo: dict[str, Any]) -> str:
    """Project topology -> Prometheus file_sd JSON for the `ibmmq` job: one target per
    exporter instance on mon-probe. Targets for a stopped stack simply read down."""
    probe = _probe_mgmt_ip(topo)
    entries = [
        {
            "targets": [f"{probe}:{inst['port']}"],
            "labels": {"host": "mon-probe", "stack": inst["stack"], "role": inst["role"]},
        }
        for inst in mq_exporter_instances(topo)
    ]
    return json.dumps(entries, indent=2) + "\n"


def render_mq_exporters(topo: dict[str, Any]) -> str:
    """The per-stack mq-exporter deployment list, consumed as an extra-var by
    site-obs.yml's include_role loop (`mq_exporters`)."""
    return json.dumps({"mq_exporters": mq_exporter_instances(topo)}, indent=2) + "\n"


def mq_scrape_targets_path() -> Path:
    """Where the rendered ibmmq file_sd target list is written (gitignored)."""
    return work("prometheus", "targets", "ibmmq.json")


def mq_exporters_path() -> Path:
    """Where the rendered per-stack exporter deployment list is written (gitignored)."""
    return work("obs", "mq-exporters.json")


def lab_mq_scrape_targets() -> str:
    """Render the real lab/topology.yaml to the ibmmq file_sd JSON text."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_mq_scrape_targets(topo)


def lab_mq_exporters() -> str:
    """Render the real lab/topology.yaml to the per-stack exporter deployment JSON."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_mq_exporters(topo)
