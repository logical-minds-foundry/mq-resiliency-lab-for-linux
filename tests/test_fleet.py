from __future__ import annotations

from mqlab.fleet import arm_of, fleet_rows, lab_guests, parse_domain_states

VIRSH = """\
 Id   Name             State
---------------------------------
 -    lab_rdqm-a1      shut off
 49   lab_pcmk-b1      running
"""


def test_arm_of_maps_prefixes_and_standalone():
    assert arm_of("rdqm-a1") == "RDQM / RHEL"
    assert arm_of("pcmk-a1") == "Pacemaker / SAN"
    assert arm_of("san-a") == "Pacemaker / SAN"
    assert arm_of("node-a1") == "Phase-A placeholder"
    assert arm_of("qm-main") == "Phase-B standalone"
    assert arm_of("mystery") == "?"


def test_parse_domain_states_extracts_name_and_multiword_state():
    assert parse_domain_states(VIRSH) == {"lab_rdqm-a1": "shut off", "lab_pcmk-b1": "running"}


def test_parse_domain_states_skips_chrome_and_short_lines():
    assert parse_domain_states("\n   \nId Name State\n----\n bad\n") == {}


def test_lab_guests_reads_platform_with_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MQLAB_REPO_ROOT", str(tmp_path))
    (tmp_path / "lab").mkdir(parents=True)
    (tmp_path / "lab" / "topology.yaml").write_text(
        "defaults: { platform: ubuntu2404-arm64 }\nnodes:\n"
        "  rdqm-a1: { platform: rhel96-x86_64 }\n  pcmk-a1: {}\n"
    )
    assert lab_guests() == {"rdqm-a1": "rhel96-x86_64", "pcmk-a1": "ubuntu2404-arm64"}


def test_fleet_rows_joins_marks_not_created_and_sorts_by_arm():
    platforms = {
        "rdqm-a1": "rhel96-x86_64",
        "pcmk-b1": "ubuntu2404-arm64",
        "node-a1": "ubuntu2404-arm64",
    }
    states = {"lab_pcmk-b1": "running"}
    rows = fleet_rows(platforms, states)
    by_guest = {r.guest: r for r in rows}
    assert by_guest["rdqm-a1"].state == "not created"
    assert by_guest["rdqm-a1"].arm == "RDQM / RHEL"
    assert by_guest["pcmk-b1"].state == "running"
    # sorted by (arm, guest): Pacemaker < Phase-A < RDQM
    assert [r.guest for r in rows] == ["pcmk-b1", "node-a1", "rdqm-a1"]
