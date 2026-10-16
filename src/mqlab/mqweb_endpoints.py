"""Canonical mqweb endpoints (Console + REST), derived from lab/topology.yaml (epic #39).

Each QM's admin REST/Console (Liberty, 9443/HTTPS) is a data-plane infrastructure
surface reached at the QM's service address, published for BOTH sites (the endpoint
must be known on whichever site is live after a DR cutover):

- pcmk / RDQM -> the data-plane VIP per site (vip / vip_b).
- Native HA   -> the active instance's data-plane node IP, resolved at runtime among
                 each site's candidate nodes (no VIP exists).
- svc-sim     -> the counterparty, over net-ext (lab-only; outside "our data plane").

Fail-loud DR invariant: a VIP stack with a site-A VIP MUST declare vip_b.

Derives the *published* endpoint(s); does not connect. This is the internal library
behind `mqlab console open`: it supplies the per-site VIP or the Native HA candidate
list that the state probe (`mqlab.mqweb_probe`) walks to find the live mqweb (epic
logical-minds-foundry/.github#341 spec §2.3). Provisioning applies QM content via
Ansible runmqsc, not REST (epic #39 spec §3).
"""

from typing import Any, cast

import yaml

from mqlab.paths import repo_root

REST_PORT = 9443
DATA_NET_A = "net-data-a"
DATA_NET_B = "net-data-b"
EXT_NET = "net-ext"


class EndpointError(Exception):
    """A stack's mqweb endpoint cannot be derived from topology (fail loud)."""


def _lab_topo() -> dict[str, Any]:
    text = (repo_root() / "lab" / "topology.yaml").read_text()
    return cast("dict[str, Any]", yaml.safe_load(text))


def _url(ip: str) -> str:
    return f"https://{ip}:{REST_PORT}"


def _node_urls(topo: dict[str, Any], group: str, net: str) -> list[str]:
    nodes = topo.get("nodes") or {}
    hosts = (topo.get("groups") or {}).get(group) or []
    urls: list[str] = []
    for host in hosts:
        ip = ((nodes.get(host) or {}).get("nics") or {}).get(net)
        if not ip:
            raise EndpointError(f"node {host!r} has no {net} address")
        urls.append(_url(ip))
    return urls


def mqweb_endpoints(topo: dict[str, Any]) -> list[dict[str, Any]]:
    """One record per QM REST surface, with both-site endpoints, derived from ``topo``.

    Each record is ``{"stack": str, "kind": "vip"|"active-instance"|"counterparty",
    "endpoints": dict}`` where ``endpoints`` maps a site key (``"site-a"``, optionally
    ``"site-b"``) to a list of full ``https://<ip>:9443`` URLs.
    """
    out: list[dict[str, Any]] = []
    nodes = topo.get("nodes") or {}
    for name, cfg in (topo.get("stacks") or {}).items():
        cfg = cfg or {}
        qm = cfg.get("qm") or {}
        vip, vip_b = qm.get("vip"), qm.get("vip_b")
        if vip:
            if not vip_b:
                raise EndpointError(
                    f"stack {name!r}: site-A VIP but no vip_b — a DR/HA stack must "
                    f"publish a VIP on both sites"
                )
            out.append(
                {
                    "stack": name,
                    "kind": "vip",
                    "endpoints": {"site-a": [_url(vip)], "site-b": [_url(vip_b)]},
                }
            )
            continue
        group_a = cfg.get("cluster_group")
        if not group_a:
            raise EndpointError(
                f"stack {name!r}: no vip and no cluster_group to derive a REST endpoint"
            )
        endpoints: dict[str, list[str]] = {"site-a": _node_urls(topo, group_a, DATA_NET_A)}
        groups_b = [g for g in (cfg.get("groups") or []) if g != group_a]
        if groups_b:
            endpoints["site-b"] = _node_urls(topo, groups_b[0], DATA_NET_B)
        out.append({"stack": name, "kind": "active-instance", "endpoints": endpoints})
    svc_ext = ((nodes.get("svc-sim") or {}).get("nics") or {}).get(EXT_NET)
    if svc_ext:
        out.append(
            {"stack": "svc-sim", "kind": "counterparty", "endpoints": {"site-a": [_url(svc_ext)]}}
        )
    return out


def lab_mqweb_endpoints() -> list[dict[str, Any]]:
    """``mqweb_endpoints`` over the real lab/topology.yaml."""
    return mqweb_endpoints(_lab_topo())
