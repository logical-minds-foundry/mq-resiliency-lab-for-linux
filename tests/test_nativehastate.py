from __future__ import annotations

import subprocess
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
    # blank lines + a fields-bearing line that is neither the QUORUM summary nor an instance
    # row (e.g. a replication footer) -> no quorum, no instances (fail-loud: nothing fabricated)
    out = nativehastate.parse_nativeha_x("\n  \nREPLICATION(Established) PORT(9414)\n")
    assert out["quorum_current"] is None
    assert out["quorum_total"] is None
    assert out["group_role"] is None
    assert out["instances"] == {}


def test_render_recovery_leader_role_codes_as_healthy_not_unknown():
    # the Recovery group's leader reports ROLE(Leader) (CRR replication target) — it must
    # code as 3 (healthy leader), not fall through to 0/Unknown/red (#279 feedback).
    hax = {
        "quorum_current": 3,
        "quorum_total": 3,
        "group_role": "Recovery",
        "instances": {
            "nha-rhel-b1": {"role": "Leader", "insync": True, "hastatus": "Normal"},
            "nha-rhel-b2": {"role": "Replica", "insync": True, "hastatus": "Normal"},
        },
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-b1", qm="QMNATIVE", hax=hax, grp=None, now=1, fresh_sources=()
    )
    assert 'cluster_nha_role_code{node="nha-rhel-b1",member="nha-rhel-b1"} 3' in out  # Leader
    assert 'cluster_node_online{node="nha-rhel-b1",member="nha-rhel-b1"} 1' in out  # online
    # the Recovery leader does not run the QM, so it is not an Active owner
    assert "cluster_resource_owner" not in out


def test_parse_nativeha_g_real_capture_live_and_recovery():
    # real capture: the leading QMNAME summary line is skipped; the live (local) group reports
    # role+status only (no CONNGRP/INSYNC/BACKLOG -> None); the recovery group carries the CRR
    # facets (connected/in-sync/backlog).
    out = nativehastate.parse_nativeha_g((FIXTURES / "dspmq_nativeha_g.txt").read_text())
    assert set(out) == {"Live", "Recovery"}  # the QMNAME summary line did NOT become a group
    assert out["Live"] == {
        "role": "Live",
        "status": "Normal",
        "connected": None,
        "insync": None,
        "backlog": None,
    }
    assert out["Recovery"] == {
        "role": "Recovery",
        "status": "Normal",
        "connected": True,
        "insync": True,
        "backlog": 0,
    }


def test_parse_nativeha_g_degraded_recovery_and_unknown_fields():
    # the summary line (has QUORUM) is skipped; a live line missing GRPROLE/GRSTATUS reads
    # Unknown; the recovery group is disconnected, not in-sync, with a backlog.
    text = (
        "QMNAME(QMNATIVE) ROLE(Active) QUORUM(3/3) GRPNAME(Live) GRPROLE(Live)\n"
        " GRPNAME(Live)\n"
        " GRPNAME(Recovery) GRPROLE(Recovery) GRSTATUS(Abnormal) CONNGRP(no) INSYNC(no) "
        "BACKLOG(4096)\n"
    )
    out = nativehastate.parse_nativeha_g(text)
    assert out["Live"]["role"] == "Unknown"  # GRPROLE absent on this synthetic live line
    assert out["Live"]["status"] == "Unknown"  # GRSTATUS absent
    assert out["Live"]["connected"] is None
    assert out["Recovery"]["connected"] is False
    assert out["Recovery"]["insync"] is False
    assert out["Recovery"]["backlog"] == 4096
    assert out["Recovery"]["status"] == "Abnormal"


def test_parse_nativeha_g_skips_summary_and_non_group_lines():
    # a line without GRPNAME, and the QMNAME summary line (GRPNAME present but QUORUM too)
    out = nativehastate.parse_nativeha_g(
        "\nGRPROLE(Live) CONNGRP(yes)\nQMNAME(QMNATIVE) QUORUM(3/3) GRPNAME(Live)\n"
    )
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
    assert 'cluster_nha_role_code{node="nha-rhel-a1",member="nha-rhel-a1"} 2' in out  # Active
    assert 'cluster_nha_role_code{node="nha-rhel-a1",member="nha-rhel-a2"} 1' in out  # Replica
    assert 'cluster_nha_hastatus_ok{node="nha-rhel-a1",member="nha-rhel-a1"} 1' in out  # Normal
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
        "instances": {"nha-rhel-a1": {"role": "Unknown", "insync": False, "hastatus": "Abnormal"}},
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
    assert 'cluster_nha_role_code{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out  # Unknown
    assert 'cluster_nha_hastatus_ok{node="nha-rhel-a1",member="nha-rhel-a1"} 0' in out  # Abnormal
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


def test_render_group_metrics_live_omits_absent_crr_facets():
    # the live group reports role+status only (CRR facets None -> omitted, never faked);
    # the recovery group carries connected/in-sync/backlog.
    grp = {
        "Live": {
            "role": "Live",
            "status": "Normal",
            "connected": None,
            "insync": None,
            "backlog": None,
        },
        "Recovery": {
            "role": "Recovery",
            "status": "Normal",
            "connected": True,
            "insync": True,
            "backlog": 0,
        },
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
    assert 'cluster_nha_group_status{node="nha-rhel-a1",group="Live",status="Normal"} 1' in out
    # Live has no CRR facets -> those metrics carry no Live series
    assert 'cluster_nha_connected{node="nha-rhel-a1",group="Live"}' not in out
    assert 'cluster_nha_group_insync{node="nha-rhel-a1",group="Live"}' not in out
    assert 'cluster_nha_group_backlog{node="nha-rhel-a1",group="Live"}' not in out
    # Recovery carries the CRR signal
    assert 'cluster_nha_connected{node="nha-rhel-a1",group="Recovery"} 1' in out
    assert 'cluster_nha_group_insync{node="nha-rhel-a1",group="Recovery"} 1' in out
    assert 'cluster_nha_group_backlog{node="nha-rhel-a1",group="Recovery"} 0' in out


def test_render_group_degraded_recovery_emits_zero_and_backlog():
    grp = {
        "Recovery": {
            "role": "Recovery",
            "status": "Abnormal",
            "connected": False,
            "insync": False,
            "backlog": 512,
        },
    }
    out = nativehastate.render_nativeha_state_prom(
        node="nha-rhel-a1",
        qm="QMNATIVE",
        hax=None,
        grp=grp,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_nha_connected{node="nha-rhel-a1",group="Recovery"} 0' in out
    assert 'cluster_nha_group_insync{node="nha-rhel-a1",group="Recovery"} 0' in out
    assert 'cluster_nha_group_backlog{node="nha-rhel-a1",group="Recovery"} 512' in out
    assert (
        'cluster_nha_group_status{node="nha-rhel-a1",group="Recovery",status="Abnormal"} 1' in out
    )


def test_probe_returns_stdout_then_none_on_failure(monkeypatch):
    monkeypatch.setattr(
        nativehastate.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 0, "out\n", ""),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) == "out\n"
    monkeypatch.setattr(
        nativehastate.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None
    monkeypatch.setattr(
        nativehastate.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, k["timeout"])),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None
    monkeypatch.setattr(
        nativehastate.subprocess,
        "run",
        lambda c, **k: (_ for _ in ()).throw(OSError("no dspmq")),
    )
    assert nativehastate.probe(["dspmq"], timeout=3) is None


def test_main_writes_textfile_atomically(tmp_path, monkeypatch):
    xtext = (FIXTURES / "dspmq_nativeha_x.txt").read_text()
    gtext = (FIXTURES / "dspmq_nativeha_g.txt").read_text()

    def fake_probe(cmd, timeout):
        # the dspmq invocation is the last `su -c` argument; route on its -x/-g suffix
        return gtext if cmd[-1].endswith("-g") else xtext

    monkeypatch.setattr(nativehastate, "probe", fake_probe)
    out = tmp_path / "lab_nativeha_state.prom"
    nativehastate.main(
        ["--qm", "QMNATIVE", "--node", "nha-rhel-a1", "--out", str(out), "--now", "1781455000"]
    )
    text = out.read_text()
    assert 'cluster_quorate{node="nha-rhel-a1"} 1' in text
    assert 'cluster_nha_group_role{node="nha-rhel-a1",group="Live",role="Live"} 1' in text
    assert 'source="nativeha_x"' in text
    assert 'source="nativeha_g"' in text
    assert not (tmp_path / "lab_nativeha_state.prom.tmp").exists()  # atomic move cleaned up


def test_main_marks_sources_stale_when_probes_time_out(tmp_path, monkeypatch):
    monkeypatch.setattr(nativehastate, "probe", lambda cmd, timeout: None)
    out = tmp_path / "c.prom"
    nativehastate.main(["--qm", "QMNATIVE", "--node", "nha-rhel-a1", "--out", str(out)])
    text = out.read_text()
    assert "cluster_quorate" not in text  # -x stale -> omitted
    assert "last_write_timestamp" not in text  # nothing fresh


def test_main_defaults_node_to_hostname_and_now_to_clock(tmp_path, monkeypatch):
    xtext = (FIXTURES / "dspmq_nativeha_x.txt").read_text()
    monkeypatch.setattr(
        nativehastate, "probe", lambda cmd, timeout: xtext if cmd[-1].endswith("-x") else None
    )
    monkeypatch.setattr(
        nativehastate.os, "uname", lambda: type("U", (), {"nodename": "nha-rhel-a2"})()
    )
    monkeypatch.setattr(nativehastate.time, "time", lambda: 1781455999.0)
    out = tmp_path / "c.prom"
    nativehastate.main(["--qm", "QMNATIVE", "--out", str(out)])
    text = out.read_text()
    assert 'cluster_quorate{node="nha-rhel-a2"} 1' in text
    assert (
        'cluster_state_last_write_timestamp{node="nha-rhel-a2",source="nativeha_x"} 1781455999'
        in text
    )
