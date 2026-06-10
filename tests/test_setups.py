from __future__ import annotations

from mqlab.setups import lab_setups, setup_members, setups_of

TOPO = (
    "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  rdqm-a1: {}\n"
    "setups:\n"
    "  pcmk-san-ha:\n"
    "    description: Pacemaker SAN HA\n"
    "    members: [san-a, pcmk-a1]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "  rdqm-ha:\n"
    "    members: [rdqm-a1]\n"
)


def _seed(tmp_path):
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(TOPO)


def test_lab_setups_parses_members_description_and_provision(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    setups = lab_setups()
    assert setups["pcmk-san-ha"].members == ["san-a", "pcmk-a1"]
    assert setups["pcmk-san-ha"].description == "Pacemaker SAN HA"
    assert setups["pcmk-san-ha"].provision == "ansible/site-pcmk.yml"
    assert setups["rdqm-ha"].provision is None  # provision is optional


def test_setup_members_returns_order_or_none(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert setup_members("pcmk-san-ha") == ["san-a", "pcmk-a1"]  # bring-up order
    assert setup_members("nope") is None


def test_setups_of_lists_a_guests_setups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert setups_of("san-a") == ["pcmk-san-ha"]
    assert setups_of("rdqm-a1") == ["rdqm-ha"]
    assert setups_of("mystery") == []
