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


def test_render_emits_quorum_owner_and_per_instance_metrics():
    hax = {
        "quorum_current": 3,
        "quorum_total": 3,
        "group_role": "Live",
        "instances": {
            "nha-rhel-a1": {"role": "Active", "insync": True, "hastatus": "Normal"},
            "nha-rhel-a2": {"role": "Replica", "insync": True, "hastatus": "Normal"},
        },
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax=hax,
        grp=None,
        now=1781455000,
        fresh_sources=("nativeha_x",),
    )
    assert 'cluster_quorate{node="nha-rhel-a1"} 1' in out
    assert 'cluster_nha_quorum{node="nha-rhel-a1"} 3' in out
    assert 'cluster_node_online{node="nha-rhel-a1",member="nha-rhel-a1"} 1' in out
    assert 'cluster_nha_role{node="nha-rhel-a1",member="nha-rhel-a1",role="Active"} 1' in out
    assert 'cluster_nha_insync{node="nha-rhel-a1",member="nha-rhel-a2"} 1' in out
    assert (
        'cluster_resource_owner{node="nha-rhel-a1",resource="QMNATIVE",holder="nha-rhel-a1"} 1'
        in out
    )
    assert (
        'cluster_state_last_write_timestamp{node="nha-rhel-a1",source="nativeha_x"} 1781455000'
        in out
    )


def test_render_quorum_lost_and_unknown_leader_branches():
    hax = {
        "quorum_current": 1,
        "quorum_total": 3,
        "group_role": "Live",
        "instances": {
            "nha-rhel-a1": {"role": "Unknown", "insync": False, "hastatus": "Abnormal"}
        },
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax=hax,
        grp=None,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_quorate{node="nha-rhel-a1"} 0' in out  # 1 < majority(2)
    assert 'cluster_node_online{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out  # Unknown
    assert 'cluster_nha_insync{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out
    assert "cluster_resource_owner" not in out  # no Active -> no owner line
    assert "last_write_timestamp" not in out  # empty fresh_sources


def test_render_unknown_quorum_omits_quorate():
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax={
            "quorum_current": None,
            "quorum_total": None,
            "group_role": None,
            "instances": {},
        },
        grp=None,
        now=1,
        fresh_sources=(),
    )
    assert "cluster_quorate" not in out  # unknown -> omitted, never a false green
    assert "cluster_nha_quorum" not in out


def test_render_emits_group_metrics():
    grp = {
        "Live": {"role": "Live", "connected": True, "insync": True, "backlog": 0},
        "Recovery": {"role": "Recovery", "connected": False, "insync": False, "backlog": 512},
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax=None,
        grp=grp,
        now=1,
        fresh_sources=("nativeha_g",),
    )
    assert 'cluster_nha_group_role{node="nha-rhel-a1",group="Live",role="Live"} 1' in out
    assert 'cluster_nha_connected{node="nha-rhel-a1",group="Recovery"} 0' in out
    assert 'cluster_nha_group_backlog{node="nha-rhel-a1",group="Recovery"} 512' in out
    assert 'cluster_nha_group_insync{node="nha-rhel-a1",group="Live"} 1' in out
