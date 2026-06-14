from __future__ import annotations

from pathlib import Path

from mqlab import clusterstate

FIXTURES = Path(__file__).parent / "fixtures" / "clusterstate"


def test_module_exposes_role_probe_sets():
    # cluster nodes probe crm/stonith/iscsi/daemons; san nodes probe drbd/daemons
    assert clusterstate.PROBE_SETS["cluster"] == ("crm", "stonith", "iscsi", "daemons")
    assert clusterstate.PROBE_SETS["storage"] == ("drbd", "daemons")
