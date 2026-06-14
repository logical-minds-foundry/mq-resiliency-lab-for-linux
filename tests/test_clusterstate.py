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
