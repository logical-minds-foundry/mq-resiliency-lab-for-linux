"""Topology coverage for the nativeha-rhel stack (#246, #267, #350).

The stack is ONE consolidated full-HADR stack (not the older _ha/_dr triple), and —
unlike RDQM/Pacemaker — Native HA has no floating VIP, so the QM must parse without one.
"""

import pathlib

import yaml

from mqlab.stacks import lab_stacks


def _topology() -> dict:
    return yaml.safe_load(pathlib.Path("lab/topology.yaml").read_text())


def test_nativeha_rhel_stack_uses_mqmonitor_verbs():
    stack = lab_stacks()["nativeha-rhel"]
    assert stack.mechanism == "native-ha"
    # lifecycle is the mqmonitor@ systemd unit, NOT endmqm/strmqm/pcs
    assert "mqmonitor@" in stack.verbs["qm-up"]["cmd"]
    assert "mqmonitor@" in stack.verbs["qm-down"]["cmd"]
    # CRR / DR verbs (Phase 3): planned cross-site switchover
    assert stack.verbs["dr-cutover"]["playbook"] == "site-nativeha-switchover.yml"
    assert stack.verbs["dr-failback"]["playbook"] == "site-nativeha-switchover.yml"
    # diagnostics capture (runmqras, read-only)
    assert "runmqras" in stack.verbs["diagnostics"]["cmd"]


def test_one_consolidated_full_hadr_stack():
    stacks = lab_stacks()
    s = stacks["nativeha-rhel"]
    # the keystone: both sites (HA + DR) in one stack (#267); commons (svc/app) are
    # shared and provisioned separately, so they are NOT in the stack's groups (#350)
    assert set(s.groups) == {"nha_rhel_a", "nha_rhel_b"}
    # no partial throwaway stacks
    assert "nativeha_ha" not in stacks
    assert "nativeha_dr" not in stacks


def test_platform_qualified_node_groups():
    g = _topology()["groups"]
    assert set(g["nha_rhel_a"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}
    assert set(g["nha_rhel_b"]) == {"nha-rhel-b1", "nha-rhel-b2", "nha-rhel-b3"}


def test_stack_parses_without_a_vip():
    # Native HA has no floating VIP (multi-instance CONNAME list instead);
    # QmConfig.vip must be optional for the stack to parse.
    s = lab_stacks()["nativeha-rhel"]
    assert s.qm.qm_app == "NHARAPP" and s.qm.qm_svc == "NHARSVC"  # short-derived (#351)
    assert s.qm.vip == ""
