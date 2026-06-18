from __future__ import annotations

from pathlib import Path

from mqlab import nativehastate

FIXTURES = Path(__file__).parent / "fixtures" / "nativehastate"


def test_parse_nativeha_x_extracts_quorum_group_role_and_per_instance_state():
    # real capture: site-A 3/3, a1 Active, a2/a3 Replica, all in-sync, group is Live
    out = nativehastate.parse_nativeha_x((FIXTURES / "dspmq_nativeha_x.txt").read_text())
    assert out["quorum_current"] == 3
    assert out["quorum_total"] == 3
    assert out["group_role"] == "Live"
    assert out["instances"]["nha-rhel-a1"] == {
        "role": "Active",
        "insync": True,
        "hastatus": "Normal",
    }
    assert out["instances"]["nha-rhel-a2"]["role"] == "Replica"
    assert set(out["instances"]) == {"nha-rhel-a1", "nha-rhel-a2", "nha-rhel-a3"}


def test_parse_nativeha_x_handles_degraded_unknown_and_not_insync():
    # a degraded snapshot: quorum lost (1/3), the leader Unknown, a replica not in-sync
    text = (
        "QMNAME(QMNATIVE) ROLE(Unknown) INSTANCE(nha-rhel-a1) INSYNC(no) QUORUM(1/3) "
        "HASTATUS(Abnormal) GRPROLE(Live)\n"
        " INSTANCE(nha-rhel-a1) ROLE(Unknown) INSYNC(no) HASTATUS(Abnormal)\n"
        " INSTANCE(nha-rhel-a2) ROLE(Replica) INSYNC(no) HASTATUS(Normal)\n"
    )
    out = nativehastate.parse_nativeha_x(text)
    assert out["quorum_current"] == 1
    assert out["instances"]["nha-rhel-a1"]["role"] == "Unknown"
    assert out["instances"]["nha-rhel-a1"]["insync"] is False
    assert out["instances"]["nha-rhel-a2"]["insync"] is False


def test_parse_nativeha_x_without_quorum_line_leaves_summary_none():
    # blank / unexpected output -> no quorum, no instances (fail-loud: nothing fabricated)
    out = nativehastate.parse_nativeha_x("\n  \n")
    assert out["quorum_current"] is None
    assert out["quorum_total"] is None
    assert out["group_role"] is None
    assert out["instances"] == {}


def test_parse_nativeha_g_extracts_both_groups():
    # the nested-paren GRPADDR in the fixture must NOT corrupt the scalar fields we read
    out = nativehastate.parse_nativeha_g((FIXTURES / "dspmq_nativeha_g.txt").read_text())
    assert out["Live"] == {"role": "Live", "connected": True, "insync": True, "backlog": 0}
    assert out["Recovery"]["role"] == "Recovery"
    assert out["Recovery"]["connected"] is True


def test_parse_nativeha_g_flags_disconnected_recovery_with_backlog():
    text = (
        "GRPNAME(Live) GRPROLE(Live) CONNGRP(no) INSYNC(no) BACKLOG(0)\n"
        "GRPNAME(Recovery) GRPROLE(Recovery) CONNGRP(no) INSYNC(no) BACKLOG(4096)\n"
    )
    out = nativehastate.parse_nativeha_g(text)
    assert out["Live"]["connected"] is False
    assert out["Recovery"]["insync"] is False
    assert out["Recovery"]["backlog"] == 4096


def test_parse_nativeha_g_skips_lines_without_group_name():
    out = nativehastate.parse_nativeha_g("\nGRPROLE(Live) CONNGRP(yes)\n")  # no GRPNAME
    assert out == {}
