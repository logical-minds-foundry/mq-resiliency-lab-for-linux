from __future__ import annotations

from mqlab.setups import lab_groups, lab_setups, setup_members, setups_of

TOPO = (
    "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  pcmk-a2: {}\n  rdqm-a1: {}\n"
    "groups:\n"
    "  san_a: [san-a]\n"
    "  pcmk_a: [pcmk-a1, pcmk-a2]\n"
    "  rdqm_a: [rdqm-a1]\n"
    "setups:\n"
    "  pcmk_san_ha:\n"
    "    description: Pacemaker SAN HA\n"
    "    groups: [san_a, pcmk_a]\n"
    "    provision: ansible/site-pcmk.yml\n"
    "  rdqm_ha:\n"
    "    groups: [rdqm_a]\n"
)


def _seed(tmp_path):
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(TOPO)


def test_lab_setups_parses_groups_description_and_provision(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    setups = lab_setups()
    assert setups["pcmk_san_ha"].groups == ["san_a", "pcmk_a"]
    assert setups["pcmk_san_ha"].description == "Pacemaker SAN HA"
    assert setups["pcmk_san_ha"].provision == "ansible/site-pcmk.yml"
    assert setups["rdqm_ha"].provision is None  # provision is optional


def test_lab_groups_reads_atomic_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert lab_groups()["pcmk_a"] == ["pcmk-a1", "pcmk-a2"]


def test_setup_members_flattens_groups_in_order(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert setup_members("pcmk_san_ha") == ["san-a", "pcmk-a1", "pcmk-a2"]  # bring-up order
    assert setup_members("nope") is None


def test_setup_members_dedupes_a_host_shared_across_groups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  h1: {}\n  h2: {}\n"
        "groups:\n  a: [h1, h2]\n  b: [h1]\n"
        "setups:\n  s:\n    groups: [a, b]\n"
    )
    assert setup_members("s") == ["h1", "h2"]  # h1 in both groups -> listed once


def test_setups_of_lists_a_guests_setups(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    _seed(tmp_path)
    assert setups_of("san-a") == ["pcmk_san_ha"]
    assert setups_of("rdqm-a1") == ["rdqm_ha"]
    assert setups_of("mystery") == []
