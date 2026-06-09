from __future__ import annotations

from mqlab.netsel import lab_net_names, resolve_nets, select_nets

NAMES = ["net-data-a", "net-data-b", "net-hb-a", "net-wan"]


def test_select_all_returns_every_name():
    assert select_nets("all", NAMES) == NAMES


def test_select_regex_matches_subset():
    assert select_nets("data", NAMES) == ["net-data-a", "net-data-b"]


def test_select_exact_name_matches_only_itself():
    assert select_nets("net-wan", NAMES) == ["net-wan"]


def test_select_no_match_returns_empty():
    assert select_nets("nope", NAMES) == []


def test_lab_net_names_reads_xml_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    nets = tmp_path / "lab" / "networks"
    nets.mkdir(parents=True)
    (nets / "net-wan.xml").write_text("<network/>")
    (nets / "net-data-a.xml").write_text("<network/>")
    (nets / "other.txt").write_text("x")  # ignored: not net-*.xml
    assert lab_net_names() == ["net-data-a", "net-wan"]


def test_resolve_filters_lab_net_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    nets = tmp_path / "lab" / "networks"
    nets.mkdir(parents=True)
    for name in ("net-hb-a", "net-hb-b", "net-wan"):
        (nets / f"{name}.xml").write_text("<network/>")
    assert resolve_nets("hb") == ["net-hb-a", "net-hb-b"]
