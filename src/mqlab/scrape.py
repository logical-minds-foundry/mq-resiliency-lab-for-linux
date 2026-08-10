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
# over the management plane: the arm's data-plane VIP where it has one, else the
# Native HA site-A instances' MGMT-plane addresses as a CONNAME list (no VIP).
MQ_LISTENER_PORT = 1414
MON_CHANNEL = "MON.SVRCONN"  # dedicated ops SVRCONN the exporter presents O=app-org on (#250)
# The SVC counterparty is a single shared QM (SVCQM) on svc-sim; it is scraped once,
# from the top-level `svc:` block — not one per stack (#446). See _svc_exporter_instance.
PROBE_GROUP = "probe"
# Monitoring is MANAGEMENT traffic, so the no-VIP (Native HA) exporter conn rides the
# net-mgmt plane — NOT the net-data-a *replication* plane, which is for Native HA raft
# log traffic and is not a client path from mon-probe (#975). Every QM node and
# mon-probe carries a net-mgmt nic, so this is always reachable. (VIP arms are
# unaffected: they connect on cfg.qm.vip, set on the data plane, not via this net.)
EXPORTER_CONN_NET = "net-mgmt"


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
    """The client CONNAME(s) the exporter uses to reach a stack's app QM: the arm's
    VIP when it has one, else — for a no-VIP Native HA stack — the site-A instances'
    MGMT-plane addresses as a CONNAME list. Monitoring is management traffic, so the
    no-VIP path rides net-mgmt, never the net-data-a replication plane (#975)."""
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
        ip = ((nodes.get(h) or {}).get("nics") or {}).get(EXPORTER_CONN_NET)
        if not ip:
            raise ScrapeError(f"host {h} has no {EXPORTER_CONN_NET} IP for an exporter conn")
        conns.append(f"{ip}({MQ_LISTENER_PORT})")
    return ",".join(conns)


def _svc_exporter_instance(topo: dict[str, Any]) -> dict[str, Any]:
    """The single shared svc exporter target (#446): one SVCQM on svc-sim, scraped
    once and labelled `commons`. Its name (<short>QM), conn, and port come from the
    top-level `svc:` block — fail loud if that block is incomplete."""
    svc = topo.get("svc") or {}
    short = svc.get("short")
    conn = svc.get("conn")
    port = svc.get("exporter_port")
    if not short or not conn or not port:
        raise ScrapeError("topology `svc:` block must declare short, conn, exporter_port")
    qm = f"{short}QM"
    return {
        "instance": qm.lower(),
        "stack": "commons",
        "role": "svc",
        "qm": qm,
        "conn": f"{conn}({MQ_LISTENER_PORT})",
        "channel": MON_CHANNEL,
        "port": port,
    }


def mq_exporter_instances(topo: dict[str, Any], stack: str | None = None) -> list[dict[str, Any]]:
    """Per-stack APP exporters (each on its stack's alloc port) + ONE shared SVCQM svc
    exporter. App emission is guarded on `exporter_app_port` ALONE — svc is no longer
    per-stack, so a stack without an svc port must NOT lose its app exporter (#446).
    QM names derive from the stack short (#351); series are qmgr-keyed so a board
    follows failover.

    When `stack` is given, the list is scoped to that stack's app exporter plus the
    shared svc (`commons`) exporter — so provisioning one stack deploys only its own
    exporter unit, never one for an un-provisioned stack that would then crash-loop
    (#503). `stack=None` returns the full topology-wide list."""
    out: list[dict[str, Any]] = []
    for name, cfg in (topo.get("stacks") or {}).items():
        cfg = cfg or {}
        short = cfg.get("short")
        app_port = (cfg.get("alloc") or {}).get("exporter_app_port")
        if not short or not app_port:
            continue  # reserved / not observable
        qm_app = f"{short}APP"
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
    out.append(_svc_exporter_instance(topo))
    if stack is not None:
        out = [e for e in out if e["stack"] in (stack, "commons")]
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


def render_mq_exporters(topo: dict[str, Any], stack: str | None = None) -> str:
    """The mq-exporter deployment list, consumed as an extra-var by site-obs.yml's
    include_role loop (`mq_exporters`). `stack` scopes it to one stack's exporter +
    the shared svc (#503)."""
    return json.dumps({"mq_exporters": mq_exporter_instances(topo, stack)}, indent=2) + "\n"


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


def lab_mq_exporters(stack: str | None = None) -> str:
    """Render the real lab/topology.yaml to the exporter deployment JSON, optionally
    scoped to one stack's exporter + the shared svc (#503)."""
    topo = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return render_mq_exporters(topo, stack)
