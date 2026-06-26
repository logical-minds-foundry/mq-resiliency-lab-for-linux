"""Topology coverage for the nativeha-rhel arm (#246, #267).

The arm gets ONE consolidated distributed-HADR setup (not the older
_ha/_dr/distributed-no-DR triple), and — unlike RDQM/Pacemaker — Native HA has
no floating VIP, so the setup must parse without one.
"""

import pathlib

import yaml

from mqlab.setups import lab_setups


def _topology() -> dict:
    return yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())


def test_nativeha_rhel_arm_uses_mqmonitor_verbs():
    arm = _topology()["arms"]["nativeha-rhel"]
    assert arm["mechanism"] == "native-ha"
    # lifecycle is the mqmonitor@ systemd unit, NOT endmqm/strmqm/pcs
    assert "mqmonitor@" in arm["verbs"]["qm-up"]["cmd"]
    assert "mqmonitor@" in arm["verbs"]["qm-down"]["cmd"]
    # CRR / DR verbs (Phase 3): planned cross-site switchover
    assert arm["verbs"]["dr-cutover"]["playbook"] == "site-nativeha-switchover.yml"
    assert arm["verbs"]["dr-failback"]["playbook"] == "site-nativeha-switchover.yml"
    # diagnostics capture (runmqras, read-only)
    assert "runmqras" in arm["verbs"]["diagnostics"]["cmd"]


def test_one_consolidated_distributed_hadr_setup():
    t = _topology()
    s = t["setups"]["distributed-nativeha-rhel"]
    assert s["arm"] == "nativeha-rhel"
    # the keystone: distributed + both sites (HA + DR) in one stack (#267)
    assert set(s["groups"]) == {"nha_rhel_a", "nha_rhel_b", "svc", "app"}
    # no partial throwaway setups
    assert "nativeha_ha" not in t["setups"]
    assert "nativeha_dr" not in t["setups"]


def test_platform_qualified_node_groups():
    g = _topology()["groups"]
    assert set(g["nha_rhel_a"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}
    assert set(g["nha_rhel_b"]) == {"nha-rhel-b1", "nha-rhel-b2", "nha-rhel-b3"}


def test_setup_parses_without_a_vip():
    # Native HA has no floating VIP (multi-instance CONNAME list instead);
    # QmConfig.vip must be optional for the setup to parse.
    s = lab_setups()["distributed-nativeha-rhel"]
    assert s.qm is not None
    assert s.qm.qm_app == "NHARAPP" and s.qm.qm_svc == "NHARSVC"  # short-derived (#351)
    assert s.qm.vip == ""
