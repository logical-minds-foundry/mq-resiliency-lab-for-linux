from __future__ import annotations

import subprocess
from pathlib import Path

from mqlab import clusterstate

FIXTURES = Path(__file__).parent / "fixtures" / "clusterstate"


def test_module_exposes_role_probe_sets():
    # cluster nodes probe crm/stonith/iscsi/daemons; san nodes probe drbd/daemons
    assert clusterstate.PROBE_SETS["cluster"] == ("crm", "stonith", "iscsi", "daemons")
    assert clusterstate.PROBE_SETS["storage"] == ("drbd", "daemons")


def test_parse_crm_extracts_quorum_nodes_and_resource_placement():
    # real capture: QM group currently on pcmk-a3; top-level fence_* resources present
    xml = (FIXTURES / "crm_mon.xml").read_text()
    out = clusterstate.parse_crm(xml)
    assert out["quorate"] is True
    assert out["nodes"]["pcmk-a2"] == {"online": True, "standby": False, "unclean": False}
    # resource -> (state, holding node or None)
    assert out["resources"]["mq_qm"] == {"state": "Started", "node": "pcmk-a3"}
    assert out["resources"]["mq_fs"]["node"] == "pcmk-a3"
    # only the mq_group is collected; top-level STONITH resources are excluded
    assert set(out["resources"]) == {"mq_fs", "mq_vip", "mq_vip_ext", "mq_qm"}
    assert "fence_pcmk-a1" not in out["resources"]


def test_parse_crm_marks_offline_node_and_unplaced_resource():
    xml = """<pacemaker-result>
      <summary><current_dc with_quorum="false"/></summary>
      <nodes><node name="pcmk-a2" online="false" standby="false" unclean="true"/></nodes>
      <resources><group id="mq_group">
        <resource id="mq_qm" role="Stopped" active="false"/>
      </group></resources>
    </pacemaker-result>"""
    out = clusterstate.parse_crm(xml)
    assert out["quorate"] is False
    assert out["nodes"]["pcmk-a2"]["unclean"] is True
    assert out["resources"]["mq_qm"] == {"state": "Stopped", "node": None}


def test_parse_drbd_extracts_role_disk_conn_and_rpo_tail():
    out = clusterstate.parse_drbd((FIXTURES / "drbd_status.json").read_text())
    r0 = out["r0"]
    assert r0["role"] == "Primary"
    assert r0["disk"] == "UpToDate"
    assert r0["conn"] == "Connected"
    assert r0["resync_pct"] == 100.0
    assert r0["out_of_sync_bytes"] == 0


def test_parse_drbd_flags_split_brain_standalone_and_resync_tail():
    text = """[{"name": "r0", "role": "Secondary",
      "devices": [{"volume": 0, "disk-state": "Outdated"}],
      "connections": [{"name": "san-b", "connection-state": "StandAlone",
        "peer-role": "Unknown",
        "peer_devices": [{"volume": 0, "peer-disk-state": "DUnknown",
          "replication-state": "Off", "percent-in-sync": 42.0,
          "out-of-sync": 2202010}]}]}]"""
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "StandAlone"  # split-brain / disconnected
    assert r0["disk"] == "Outdated"
    assert r0["resync_pct"] == 42.0
    assert r0["out_of_sync_bytes"] == 2202010


def test_parse_drbd_handles_no_connections():
    text = (
        '[{"name": "r0", "role": "Secondary",'
        ' "devices": [{"volume": 0, "disk-state": "Diskless"}],'
        ' "connections": []}]'
    )
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "Disconnected"
    assert r0["disk"] == "Diskless"
    assert r0["resync_pct"] is None
    assert r0["out_of_sync_bytes"] is None


def test_parse_stonith_counts_recent_fence_actions_per_node():
    text = (
        "Stonith history for cluster mqpcmk:\n"  # non-matching header -> skipped
        "pcmk-a2 was reset (off) by pcmk-a1 at Sat Jun 14 12:04:01 2026\n"
        "pcmk-a2 was reset (on) by pcmk-a1 at Sat Jun 14 12:05:10 2026\n"
    )
    # node -> count of fence actions seen in history (0 == clean)
    assert clusterstate.parse_stonith(text) == {"pcmk-a2": 2}


def test_parse_stonith_empty_history_is_clean():
    assert clusterstate.parse_stonith("") == {}


def test_parse_iscsi_paths_counts_sessions():
    # a non-session line before the session line exercises the skip-and-continue path
    text = "Target: san-a\ntcp: [1] 10.40.1.5:3260,1 iqn.lab:san-a\n"
    assert clusterstate.parse_iscsi(text) == 1


def test_parse_iscsi_no_sessions_is_zero():
    assert clusterstate.parse_iscsi("iscsiadm: No active sessions.\n") == 0


def test_parse_daemons_reads_systemctl_is_active_block():
    # one "is-active" line per unit, in PROBE order
    text = "active\nactive\nfailed\n"
    units = ["corosync", "pacemaker", "drbd"]
    assert clusterstate.parse_daemons(text, units) == {
        "corosync": True,
        "pacemaker": True,
        "drbd": False,
    }


def test_parse_daemons_missing_line_reads_as_down():
    # fewer is-active lines than units -> the missing unit reads False (fail-loud)
    assert clusterstate.parse_daemons("active\n", ["corosync", "pacemaker"]) == {
        "corosync": True,
        "pacemaker": False,
    }


def test_parse_crm_without_current_dc_is_not_quorate():
    out = clusterstate.parse_crm("<pacemaker-result><nodes/><resources/></pacemaker-result>")
    assert out["quorate"] is False
    assert out["nodes"] == {}
    assert out["resources"] == {}


def test_render_cluster_section_emits_quorum_resource_and_timestamp():
    crm = {
        "quorate": True,
        "nodes": {"pcmk-a2": {"online": True, "standby": False, "unclean": False}},
        "resources": {"mq_qm": {"state": "Started", "node": "pcmk-a2"}},
    }
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm,
        stonith={"pcmk-a2": 0},
        iscsi=2,
        daemons={"corosync": True, "pacemaker": True},
        drbd=None,
        now=1781455000,
        fresh_sources=("crm", "stonith", "iscsi", "daemons"),
    )
    assert 'cluster_quorate{node="pcmk-a1"} 1' in out
    assert 'cluster_resource_started{node="pcmk-a1",resource="mq_qm"} 1' in out
    assert 'cluster_resource_owner{node="pcmk-a1",resource="mq_qm",holder="pcmk-a2"} 1' in out
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 2' in out
    assert 'cluster_daemon_up{node="pcmk-a1",unit="corosync"} 1' in out
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a2"} 0' in out  # clean baseline
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="crm"} 1781455000' in out


def test_render_fence_baseline_zero_for_clean_members_and_count_for_fenced():
    crm = {
        "quorate": True,
        "nodes": {
            "pcmk-a1": {"online": True, "standby": False, "unclean": False},
            "pcmk-a2": {"online": False, "standby": False, "unclean": True},
        },
        "resources": {},
    }
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a1",
        crm=crm,
        stonith={"pcmk-a2": 3},
        iscsi=None,
        daemons={},
        drbd=None,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a1"} 0' in out  # clean -> green
    assert 'cluster_fence_count{node="pcmk-a1",member="pcmk-a2"} 3' in out  # fenced -> red


def test_render_omits_timestamp_for_stale_source():
    # crm timed out this cycle -> not in fresh_sources -> no crm timestamp (cells go STALE)
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a1",
        crm=None,
        stonith={},
        iscsi=0,
        daemons={},
        drbd=None,
        now=1781455000,
        fresh_sources=("stonith", "iscsi", "daemons"),
    )
    assert 'source="crm"' not in out
    assert 'cluster_state_last_write_timestamp{node="pcmk-a1",source="stonith"} 1781455000' in out


def test_render_storage_section_emits_drbd_enums_and_rpo():
    drbd = {
        "r0": {
            "role": "Primary",
            "disk": "UpToDate",
            "conn": "StandAlone",
            "resync_pct": 42.0,
            "out_of_sync_bytes": 2202010,
        }
    }
    out = clusterstate.render_cluster_state_prom(
        node="san-a",
        crm=None,
        stonith=None,
        iscsi=None,
        daemons={"drbd": True},
        drbd=drbd,
        now=1781455000,
        fresh_sources=("drbd", "daemons"),
    )
    assert 'cluster_drbd_conn{node="san-a",resource="r0",conn="StandAlone"} 1' in out
    assert 'cluster_drbd_out_of_sync_bytes{node="san-a",resource="r0"} 2202010' in out
    assert 'cluster_drbd_resync_pct{node="san-a",resource="r0"} 42.0' in out


def test_render_covers_degraded_and_missing_value_branches():
    # offline+unclean node, stopped+unplaced resource, down daemon, drbd with no resync/RPO
    crm = {
        "quorate": False,
        "nodes": {"pcmk-a2": {"online": False, "standby": True, "unclean": True}},
        "resources": {"mq_qm": {"state": "Stopped", "node": None}},
    }
    drbd = {
        "r0": {
            "role": "Secondary",
            "disk": "Diskless",
            "conn": "Disconnected",
            "resync_pct": None,
            "out_of_sync_bytes": None,
        }
    }
    out = clusterstate.render_cluster_state_prom(
        node="pcmk-a2",
        crm=crm,
        stonith=None,
        iscsi=None,
        daemons={"drbd": False},
        drbd=drbd,
        now=1,
        fresh_sources=(),
    )
    assert 'cluster_quorate{node="pcmk-a2"} 0' in out
    assert 'cluster_node_online{node="pcmk-a2",member="pcmk-a2"} 0' in out
    assert 'cluster_node_unclean{node="pcmk-a2",member="pcmk-a2"} 1' in out
    assert 'cluster_resource_started{node="pcmk-a2",resource="mq_qm"} 0' in out
    assert "cluster_resource_owner" not in out  # node is None -> no owner line
    assert 'cluster_daemon_up{node="pcmk-a2",unit="drbd"} 0' in out
    assert "cluster_drbd_resync_pct" not in out  # None -> omitted
    assert "cluster_drbd_out_of_sync_bytes" not in out  # None -> omitted
    assert "last_write_timestamp" not in out  # empty fresh_sources


def test_probe_returns_stdout_on_success(monkeypatch):
    def fake_run(cmd, **kw):
        assert kw["timeout"] == 3
        return subprocess.CompletedProcess(cmd, 0, stdout="hello\n", stderr="")

    monkeypatch.setattr(clusterstate.subprocess, "run", fake_run)
    assert clusterstate.probe(["echo", "hi"], timeout=3) == "hello\n"


def test_probe_returns_none_on_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(clusterstate.subprocess, "run", fake_run)
    assert clusterstate.probe(["sleep", "9"], timeout=3) is None


def test_probe_returns_none_on_nonzero_or_oserror(monkeypatch):
    monkeypatch.setattr(
        clusterstate.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom")
    )
    assert clusterstate.probe(["false"], timeout=3) is None
    monkeypatch.setattr(
        clusterstate.subprocess, "run", lambda c, **k: (_ for _ in ()).throw(OSError("no binary"))
    )
    assert clusterstate.probe(["nope"], timeout=3) is None


def test_probe_ignore_rc_returns_stdout_on_nonzero(monkeypatch):
    # `systemctl is-active` exits 3 for an inactive unit but its stdout is the real state
    monkeypatch.setattr(
        clusterstate.subprocess,
        "run",
        lambda c, **k: subprocess.CompletedProcess(c, 3, "inactive\n", ""),
    )
    assert clusterstate.probe(["systemctl"], timeout=2) is None  # default: nonzero -> None
    assert clusterstate.probe(["systemctl"], timeout=2, ignore_rc=True) == "inactive\n"


def test_main_writes_textfile_atomically_for_storage_role(tmp_path, monkeypatch):
    drbd_json = (FIXTURES / "drbd_status.json").read_text()
    monkeypatch.setattr(
        clusterstate,
        "probe",
        lambda cmd, timeout, ignore_rc=False: drbd_json if "drbd" in cmd[0] else "active\n",
    )
    out = tmp_path / "lab_cluster_state.prom"
    clusterstate.main(
        ["--role", "storage", "--node", "san-a", "--out", str(out), "--now", "1781455000"]
    )
    text = out.read_text()
    assert 'cluster_drbd_role{node="san-a",resource="r0",role="Primary"} 1' in text
    assert 'source="drbd"' in text
    assert not (tmp_path / "lab_cluster_state.prom.tmp").exists()  # atomic move cleaned up


def test_main_cluster_role_runs_crm_stonith_iscsi(tmp_path, monkeypatch):
    crm_xml = (FIXTURES / "crm_mon.xml").read_text()

    def fake_probe(cmd, timeout, ignore_rc=False):
        if cmd[0] == "crm_mon":
            return crm_xml
        if cmd[0] == "stonith_admin":
            return "0 events found\n"
        if cmd[0] == "iscsiadm":
            return "tcp: [1] 10.40.1.5:3260,1 iqn.lab:san-a\n"
        if cmd[0] == "systemctl":
            return "active\nactive\n"
        return None

    monkeypatch.setattr(clusterstate, "probe", fake_probe)
    out = tmp_path / "c.prom"
    clusterstate.main(
        ["--role", "cluster", "--node", "pcmk-a1", "--out", str(out), "--now", "1781455000"]
    )
    text = out.read_text()
    assert 'cluster_quorate{node="pcmk-a1"} 1' in text
    assert 'cluster_iscsi_sessions{node="pcmk-a1"} 1' in text


def test_main_marks_source_stale_when_probe_times_out(tmp_path, monkeypatch):
    # everything times out -> every source None -> STALE
    monkeypatch.setattr(clusterstate, "probe", lambda cmd, timeout, ignore_rc=False: None)
    out = tmp_path / "c.prom"
    clusterstate.main(
        ["--role", "cluster", "--node", "pcmk-a1", "--out", str(out), "--now", "1781455000"]
    )
    text = out.read_text()
    assert "cluster_quorate" not in text  # crm stale -> omitted
    assert "last_write_timestamp" not in text  # no source fresh


def test_main_defaults_node_to_hostname_and_now_to_clock(tmp_path, monkeypatch):
    # no --node and no --now: exercise both default branches (os.uname, time.time)
    drbd_json = (FIXTURES / "drbd_status.json").read_text()

    def fake_probe(cmd, timeout, ignore_rc=False):
        return drbd_json if cmd[0] == "drbdsetup" else "active\n"

    monkeypatch.setattr(clusterstate, "probe", fake_probe)
    monkeypatch.setattr(clusterstate.os, "uname", lambda: type("U", (), {"nodename": "san-b"})())
    monkeypatch.setattr(clusterstate.time, "time", lambda: 1781455999.0)
    out = tmp_path / "c.prom"
    clusterstate.main(["--role", "storage", "--out", str(out)])
    text = out.read_text()
    assert 'cluster_daemon_up{node="san-b",unit="drbd"} 1' in text
    assert 'cluster_state_last_write_timestamp{node="san-b",source="daemons"} 1781455999' in text
