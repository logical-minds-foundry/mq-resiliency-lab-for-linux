"""Generate BIND zone records from lab/topology.yaml (#475, epic .github#21).

Pure functions: a parsed topology dict -> forward + reverse DNS records. No I/O
beyond the `lab_*` convenience readers at the bottom (which read the real
topology, mirroring inventory.py).

Naming scheme (epic-21 spec, Option A):
- every interface gets an ``<host>-<plane>`` A record in its org's zone, where
  ``<plane>`` is the topology NIC name minus the ``net-`` prefix (the function
  label the lab already carries: ``net-data-a`` -> ``data-a``);
- the bare host name is a CNAME to its *primary* (service-facing) interface, so
  ``pcmk-a1`` resolves to its data-plane address, never the mgmt/hb/san planes;
- pcmk/rdqm VIPs get ``<short>-vip`` / ``<short>-vip-ext`` service names (Native
  HA has no VIP — clients dial the three per-instance ``<host>-data-a`` names,
  which the generic per-NIC records already produce);
- reverse (PTR) records are grouped by ``/24`` ``in-addr.arpa`` zone. The
  ``net-ext`` reverse zone spans both orgs by construction — which nameserver is
  *authoritative* for it is a serving decision left to the BIND role (#476).

Forward records for a node land in the node's ``org`` zone. ``org`` defaults to
``client`` (our estate is the overwhelming majority — the instrumented side);
only the counterparty nodes carry an explicit ``org: service``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import yaml

from mqlab.paths import repo_root

# org -> forward zone apex.
ZONES = {"client": "client.com", "service": "service.com"}

# The bare host name CNAMEs to the first interface present, by service-facing
# priority: the data planes are a node's public identity; net-ext is the
# inter-business link; net-mgmt (The Watcher) is the last resort so a node is
# never nameless. hb/san/wan planes are never a primary identity.
_PRIMARY_ORDER = ("net-data-a", "net-data-b", "net-ext", "net-mgmt")


class DnsError(RuntimeError):
    """topology.yaml cannot be rendered to DNS records."""


@dataclass(frozen=True, order=True)
class Record:
    """One resource record. ``name`` is the owner FQDN (A/CNAME) or the
    ``in-addr.arpa`` owner (PTR); ``value`` is the address (A) or target FQDN
    (CNAME/PTR)."""

    name: str
    type: str
    value: str


def _suffix(plane: str) -> str:
    """``net-data-a`` -> ``data-a`` (the function label); other names pass through."""
    return plane[len("net-") :] if plane.startswith("net-") else plane


def _zone_of(spec: dict[str, Any]) -> str:
    org = spec.get("org", "client")
    if org not in ZONES:
        raise DnsError(f"node org must be one of {sorted(ZONES)}, got {org!r}")
    return ZONES[org]


def _primary_plane(host: str, nics: dict[str, str]) -> str:
    for plane in _PRIMARY_ORDER:
        if plane in nics:
            return plane
    raise DnsError(f"node {host!r} has no primary-eligible interface: {sorted(nics)}")


def _rev(ip: str) -> tuple[str, str]:
    """(reverse /24 zone, PTR owner) for an IPv4 address."""
    octets = ip.split(".")
    if len(octets) != 4:
        raise DnsError(f"not an IPv4 address: {ip!r}")
    a, b, c, d = octets
    return f"{c}.{b}.{a}.in-addr.arpa", f"{d}.{c}.{b}.{a}.in-addr.arpa"


def _node_forward(host: str, spec: dict[str, Any], zone: str) -> list[Record]:
    nics = spec.get("nics") or {}
    if not nics:
        raise DnsError(f"node {host!r} has no nics")
    recs = [Record(f"{host}-{_suffix(p)}.{zone}", "A", str(ip)) for p, ip in nics.items()]
    primary = _primary_plane(host, nics)
    recs.append(Record(f"{host}.{zone}", "CNAME", f"{host}-{_suffix(primary)}.{zone}"))
    return recs


def _vips(topo: dict[str, Any]) -> list[tuple[str, str]]:
    """(fqdn, ip) for each pcmk/rdqm VIP — client-org service names. The site suffix
    matches the ``-data-a``/``-data-b`` convention: ``-a`` is the live (site-A) VIP,
    ``-b`` the DR (site-B) VIP; ``-ext`` are the partner-facing (net-ext) VIPs.
    Native HA stacks omit ``vip`` and contribute none."""
    out: list[tuple[str, str]] = []
    for cfg in (topo.get("stacks") or {}).values():
        short = ((cfg or {}).get("short") or "").lower()
        qm = (cfg or {}).get("qm") or {}
        if not short:
            continue
        for key, label in (
            ("vip", "vip-a"),
            ("vip_b", "vip-b"),
            ("vip_ext", "vip-ext-a"),
            ("vip_ext_b", "vip-ext-b"),
        ):
            if qm.get(key):
                out.append((f"{short}-{label}.{ZONES['client']}", str(qm[key])))
    return out


def forward_zones(topo: dict[str, Any]) -> dict[str, list[Record]]:
    """Forward zones: ``{apex -> sorted records}`` for every declared org zone."""
    zones: dict[str, list[Record]] = {z: [] for z in ZONES.values()}
    for host, spec in (topo.get("nodes") or {}).items():
        zone = _zone_of(spec)
        zones[zone].extend(_node_forward(host, spec, zone))
    for fqdn, ip in _vips(topo):
        zones[ZONES["client"]].append(Record(fqdn, "A", ip))
    return {z: sorted(set(recs)) for z, recs in zones.items()}


def reverse_zones(topo: dict[str, Any]) -> dict[str, list[Record]]:
    """Reverse zones: ``{"c.b.a.in-addr.arpa" -> sorted PTR records}``, one per
    ``/24`` that any interface or VIP occupies. Spans orgs where a plane does."""
    zones: dict[str, list[Record]] = {}
    for host, spec in (topo.get("nodes") or {}).items():
        zone = _zone_of(spec)
        for plane, ip in (spec.get("nics") or {}).items():
            rz, owner = _rev(str(ip))
            zones.setdefault(rz, []).append(Record(owner, "PTR", f"{host}-{_suffix(plane)}.{zone}"))
    for fqdn, ip in _vips(topo):
        rz, owner = _rev(ip)
        zones.setdefault(rz, []).append(Record(owner, "PTR", fqdn))
    return {rz: sorted(set(recs)) for rz, recs in zones.items()}


def lab_forward_zones() -> dict[str, list[Record]]:
    """Forward zones for the real lab/topology.yaml."""
    return forward_zones(_lab_topo())


def lab_reverse_zones() -> dict[str, list[Record]]:
    """Reverse zones for the real lab/topology.yaml."""
    return reverse_zones(_lab_topo())


def _lab_topo() -> dict[str, Any]:
    raw = yaml.safe_load((repo_root() / "lab" / "topology.yaml").read_text())
    return cast("dict[str, Any]", raw)
