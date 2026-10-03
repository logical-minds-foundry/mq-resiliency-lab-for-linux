from __future__ import annotations

from mqlab.fleet import fleet_rows, lab_guests, parse_domain_states
from mqlab.versions import load_catalog

VIRSH = """\
 Id   Name             State
---------------------------------
 -    lab_rdqm-a1      shut off
 49   lab_pcmk-b1      running
"""


def test_parse_domain_states_extracts_name_and_multiword_state():
    assert parse_domain_states(VIRSH) == {"lab_rdqm-a1": "shut off", "lab_pcmk-b1": "running"}


def test_parse_domain_states_skips_chrome_and_short_lines():
    assert parse_domain_states("\n   \nId Name State\n----\n bad\n") == {}


def test_lab_guests_maps_each_guest_to_its_generated_box(monkeypatch, tmp_path):
    """guest -> box through the version layer: a stack node gets its role on its stack's
    OS, a shared node (and a SAN target) its role on the infra OS."""
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "nodes:\n  rdqm-a1: { box: mq-rdqm }\n  obs: { box: obs }\n  san-a: { box: san }\n"
        "groups:\n  rdqm_a: [rdqm-a1]\n"
        "stacks:\n  rdqm-rhel: { os_family: rhel, groups: [rdqm_a] }\n"
    )
    cat = load_catalog()
    assert lab_guests() == {
        "rdqm-a1": cat.box("mq-rdqm", cat.default_os("rdqm-rhel")).name,
        "obs": cat.box("obs", cat.infra).name,
        "san-a": cat.box("san", cat.infra).name,
    }


def test_fleet_rows_joins_state_and_stacks_and_sorts_by_columns(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "groups:\n  san_a: [san-a]\n  pcmk_a: [pcmk-a1]\n  rdqm_a: [rdqm-a1]\n"
        "stacks:\n  pcmk-ubuntu:\n    groups: [san_a, pcmk_a]\n"
        "  rdqm-rhel:\n    groups: [rdqm_a]\n"
    )
    boxes = {
        "rdqm-a1": "mq-rdqm-rhel9",
        "pcmk-a1": "pcmk-ubuntu24",
        "san-a": "cloud-image/ubuntu-24.04",
    }
    states = {"lab_pcmk-a1": "running"}
    rows = fleet_rows(boxes, states)
    by_guest = {r.guest: r for r in rows}
    assert by_guest["rdqm-a1"].state == "not created"
    assert by_guest["rdqm-a1"].stacks == "rdqm-rhel"
    assert by_guest["san-a"].stacks == "pcmk-ubuntu"
    assert by_guest["pcmk-a1"].state == "running"
    # sorted by column left-to-right (guest first): plain alphabetical by guest
    assert [r.guest for r in rows] == ["pcmk-a1", "rdqm-a1", "san-a"]
