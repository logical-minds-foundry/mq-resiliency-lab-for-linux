"""The rdqm-rhel app and partner links reach the QM at either site after a DR cutover (#1622).

The RDQM arm's mirror of the #1428 pdrbd fix (tests/test_pdrbd_qm_role.py). There, a live
cutover held RPO-0 but e2e then failed 5/5 with MQRC 2538: app_conn named only the site-A VIP,
and the SVC sender's CONNAME named only site-A net-ext nodes, the only nodes with a net-ext NIC.
Pinned statically; the live check (cutover --rpo0-drill, then qm e2e at site B and after
failback) needs the x86 CloudLab.
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = REPO_ROOT / "ansible"
SITE = ANSIBLE / "site-rdqm.yml"
AUTHZ_VALIDATE = ANSIBLE / "site-rdqm-authz-validate.yml"
TOPOLOGY = REPO_ROOT / "lab" / "topology.yaml"

_BOTH_VIPS = "rdqm-vip-a.client.com(1414),rdqm-vip-b.client.com(1414)"


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _plays(path: Path) -> list[dict[str, Any]]:
    return [p for p in _load(path) if isinstance(p, dict)]


def _distributed_vars() -> dict[str, Any]:
    [imp] = [p for p in _plays(SITE) if p.get("import_playbook") == "site-distributed-shared.yml"]
    return imp["vars"]


def test_distributed_import_reaches_the_qm_at_either_site() -> None:
    # Both sites, site A first: after a DR cutover the QM runs at site B.
    v = _distributed_vars()
    assert v["our_conn"] == ",".join(
        f"rdqm-{s}{i}-ext.client.com(1414)" for s in ("a", "b") for i in (1, 2, 3)
    )
    assert v["app_conn"] == _BOTH_VIPS
    assert v["app_tls"] is True


def test_every_rdqm_node_named_in_our_conn_has_a_net_ext_nic() -> None:
    topo = _load(TOPOLOGY)
    hosts = topo["groups"]["rdqm_a"] + topo["groups"]["rdqm_b"]
    assert len(hosts) == 6
    for h in hosts:
        assert "net-ext" in topo["nodes"][h]["nics"], h
    # our_conn names exactly these hosts' net-ext FQDNs (the generated <host>-ext name).
    conns = _distributed_vars()["our_conn"].split(",")
    assert [c.split(".", 1)[0].removesuffix("-ext") for c in conns] == hosts


def test_app_conn_vips_are_the_stacks_declared_site_vips() -> None:
    # rdqm-vip-b only resolves (and is only bound by rdqm-qm-create.sh / rdqm-dr-cutover.sh)
    # because the stack declares vip_b; app_conn's second VIP depends on it.
    qm = _load(TOPOLOGY)["stacks"]["rdqm-rhel"]["qm"]
    assert qm["vip"] == "10.10.1.100"
    assert qm["vip_b"] == "10.10.2.100"


def test_authz_validate_harness_uses_the_same_app_conn() -> None:
    [play] = [p for p in _plays(AUTHZ_VALIDATE) if "app_conn" in (p.get("vars") or {})]
    assert play["vars"]["app_conn"] == _distributed_vars()["app_conn"]


def test_every_lab_address_is_unique() -> None:
    # Adding site-B net-ext (#1622) must not reuse an address another node NIC or a
    # stack's floating IP (qm.vip / vip_b / vip_ext / vip_ext_b) already holds.
    topo = _load(TOPOLOGY)
    seen: dict[str, list[str]] = collections.defaultdict(list)
    for host, spec in topo["nodes"].items():
        for net, ip in (spec.get("nics") or {}).items():
            seen[str(ip)].append(f"{host}:{net}")
    for stack, cfg in topo["stacks"].items():
        for key, ip in ((cfg or {}).get("qm") or {}).items():
            if key.startswith("vip"):
                seen[str(ip)].append(f"{stack}:qm.{key}")
    dupes = {ip: owners for ip, owners in seen.items() if len(owners) > 1}
    assert not dupes, dupes


def test_rdqm_site_b_net_ext_avoids_the_reserved_vendor_block() -> None:
    # 10.60.0.40-.49 is the svc-sim-a/-b block reserved by the two-site SVC DR plan (#237).
    nodes = _load(TOPOLOGY)["nodes"]
    for h in ("rdqm-b1", "rdqm-b2", "rdqm-b3"):
        last = int(nodes[h]["nics"]["net-ext"].rsplit(".", 1)[1])
        assert not 40 <= last <= 49, h
