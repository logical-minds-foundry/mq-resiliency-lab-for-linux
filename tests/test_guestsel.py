from __future__ import annotations

from mqlab.guestsel import lab_guest_names, resolve_guests, select_guests

NAMES = ["node-a1", "node-a2", "pcmk-a1", "rdqm-a1"]


def test_select_all_returns_every_name():
    assert select_guests("all", NAMES) == NAMES


def test_select_regex_matches_subset():
    assert select_guests("pcmk", NAMES) == ["pcmk-a1"]


def test_select_exact_name_matches_only_itself():
    assert select_guests("node-a1", NAMES) == ["node-a1"]


def test_select_no_match_returns_empty():
    assert select_guests("zzz", NAMES) == []


def test_lab_guest_names_reads_topology(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text("nodes:\n  rdqm-a1: {}\n  node-a1: {}\n")
    assert lab_guest_names() == ["node-a1", "rdqm-a1"]


def test_resolve_filters_lab_guest_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  pcmk-a1: {}\n  pcmk-b1: {}\n  node-a1: {}\n"
    )
    assert resolve_guests("pcmk") == ["pcmk-a1", "pcmk-b1"]


def test_resolve_guests_resolves_a_stack_name_to_members_in_order(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  san-a: {}\n  pcmk-a1: {}\n  pcmk-a2: {}\n"
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1, pcmk-a2]\n"
        "stacks:\n  pcmk-ubuntu:\n    short: PCMK\n    groups: [san_a, pcmk_a]\n"
    )
    # a stack name wins over regex, and returns members in declared (bring-up) order
    assert resolve_guests("pcmk-ubuntu") == ["san-a", "pcmk-a1", "pcmk-a2"]
