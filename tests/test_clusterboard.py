from __future__ import annotations

from mqlab.clusterboard import active_side, fold_side, matrix, render_cluster_dashboard

DS = "promtest"


def test_matrix_is_a_joined_colourised_table():
    cols = [
        ("corosync", 'max by (n)(label_replace(cluster_daemon_up{unit="corosync"},"n","$1","node","(.*)"))', "up"),
        ("fence", 'max by (n)(label_replace(cluster_fence_count,"n","$1","member","(.*)"))', "clean0"),
    ]
    p = matrix("② Compute", cols, DS, y=0)
    assert p["type"] == "table"
    assert p["title"] == "② Compute"
    # one instant table-format target per column, all on the pinned datasource
    assert [t["refId"] for t in p["targets"]] == ["A", "B"]
    assert all(t["format"] == "table" and t["instant"] for t in p["targets"])
    assert all(t["datasource"] == {"type": "prometheus", "uid": DS} for t in p["targets"])
    assert p["targets"][0]["expr"].startswith("max by (n)(label_replace(cluster_daemon_up")
    # join on n, then rename Value #<ref> -> column title, n -> node, drop Time
    tids = [t["id"] for t in p["transformations"]]
    assert tids == ["joinByField", "organize"]
    org = p["transformations"][1]["options"]
    assert org["renameByName"] == {"Value #A": "corosync", "Value #B": "fence", "n": "node"}
    assert org["excludeByName"] == {"Time": True}
    # one colour-background override per column, with the right mapping family
    ov = {o["matcher"]["options"]: o for o in p["fieldConfig"]["overrides"]}
    assert set(ov) == {"corosync", "fence"}
    cell = next(pr for pr in ov["corosync"]["properties"] if pr["id"] == "custom.cellOptions")
    assert cell["value"] == {"type": "color-background", "mode": "basic"}


def test_fold_side_precedence():
    assert fold_side(["green", "green"]) == "green"
    assert fold_side(["green", "amber"]) == "amber"
    assert fold_side(["amber", "red"]) == "red"
    assert fold_side(["red", "STALE"]) == "STALE"  # STALE outranks red
    assert fold_side([]) == "STALE"  # no cells = blind = STALE


def test_active_side_states():
    assert active_side(["A"]) == "A"
    assert active_side(["B"]) == "B"
    assert active_side([]) == "none"  # nobody owns it mid-transition
    assert active_side(["A", "B"]) == "split"  # dual owner = hazard


def test_board_has_uid_and_the_two_matrices():
    d = render_cluster_dashboard({}, arm="pcmk")
    assert d["uid"] == "lab-pcmk-cluster"
    titles = [p["title"] for p in d["panels"]]
    assert titles == ["② Compute — node × component", "③ Storage — DRBD / SAN"]
    compute = d["panels"][0]
    cols = compute["transformations"][1]["options"]["renameByName"]
    assert {"Value #A", "Value #D"} <= set(cols)  # corosync .. fence present
    # storage matrix sits below compute (no overlap)
    assert d["panels"][1]["gridPos"]["y"] > compute["gridPos"]["y"]
