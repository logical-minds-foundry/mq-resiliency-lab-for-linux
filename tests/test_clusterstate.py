from __future__ import annotations

from pathlib import Path

from mqlab import clusterstate

FIXTURES = Path(__file__).parent / "fixtures" / "clusterstate"


def test_module_exposes_role_probe_sets():
    # cluster nodes probe crm/stonith/iscsi/daemons; san nodes probe drbd/daemons
    assert clusterstate.PROBE_SETS["cluster"] == ("crm", "stonith", "iscsi", "daemons")
    assert clusterstate.PROBE_SETS["storage"] == ("drbd", "daemons")


def test_parse_crm_extracts_quorum_nodes_and_resource_placement():
    xml = (FIXTURES / "crm_mon.xml").read_text()
    out = clusterstate.parse_crm(xml)
    assert out["quorate"] is True
    assert out["nodes"]["pcmk-a2"] == {"online": True, "standby": False, "unclean": False}
    # resource -> (state, holding node or None)
    assert out["resources"]["mq_qm"] == {"state": "Started", "node": "pcmk-a2"}
    assert out["resources"]["mq_fs"]["node"] == "pcmk-a2"


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
    text = """[{"name":"r0","role":"Secondary",
      "devices":[{"volume":0,"disk-state":"Outdated"}],
      "connections":[{"name":"san-b","connection-state":"StandAlone","peer-role":"Unknown",
        "peer_devices":[{"volume":0,"peer-disk-state":"DUnknown",
          "replication-state":"Off","percent-in-sync":42.0,"out-of-sync":2202010}]}]}]"""
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "StandAlone"  # split-brain / disconnected
    assert r0["disk"] == "Outdated"
    assert r0["resync_pct"] == 42.0
    assert r0["out_of_sync_bytes"] == 2202010


def test_parse_drbd_handles_no_connections():
    text = '[{"name":"r0","role":"Secondary","devices":[{"volume":0,"disk-state":"Diskless"}],"connections":[]}]'
    r0 = clusterstate.parse_drbd(text)["r0"]
    assert r0["conn"] == "Disconnected"
    assert r0["disk"] == "Diskless"
    assert r0["resync_pct"] is None
    assert r0["out_of_sync_bytes"] is None


def test_parse_stonith_counts_recent_fence_actions_per_node():
    text = (
        "pcmk-a2 was reset (off) by pcmk-a1 at Sat Jun 14 12:04:01 2026\n"
        "pcmk-a2 was reset (on) by pcmk-a1 at Sat Jun 14 12:05:10 2026\n"
    )
    # node -> count of fence actions seen in history (0 == clean)
    assert clusterstate.parse_stonith(text) == {"pcmk-a2": 2}


def test_parse_stonith_empty_history_is_clean():
    assert clusterstate.parse_stonith("") == {}


def test_parse_iscsi_paths_counts_sessions():
    text = "tcp: [1] 10.40.1.5:3260,1 iqn.2003-01.lab:san-a (non-flash)\n"
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
